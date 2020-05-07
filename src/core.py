import os
import sys
import shutil
from gpiozero import LED, Button
import time
from datetime import datetime
import threading
from transitions import Machine, State
import soundfile as sf
import sounddevice as sd
import numpy as np
import daemons
import logging
from tempfile import gettempprefix
from copy import deepcopy
from functools import wraps
def timing(f):
    @wraps(f)
    def wrap(*args, **kw):
        ts = time.time()
        result = f(*args, **kw)
        te = time.time()
        logging.debug('func:%r took: %.0f ms' % \
          (f.__name__, (te-ts)*1e3))
        return result
    return wrap

def restart_program(): 
    led_circle()
    python = sys.executable
    os.execl(python, python, * sys.argv) 

# Setup logging
logging_level = logging.DEBUG
logging.basicConfig(
    level=logging_level,
    format='(%(threadName)-10s) %(message)s')
logging.getLogger('transitions').setLevel(logging_level)

# Log all uncaught exceptions too
def my_handler(type, value, tb):
    logger.exception("Uncaught exception: {0}".format(str(value)))
sys.excepthook = my_handler
        

# try:
#     # Cloud logging setup tutorial: https://cloud.google.com/logging/docs/setup/python
#     # Cloud log viewer: https://console.cloud.google.com/logs/viewer?project=pi-looper&folder&organizationId&minLogLevel=0&expandAll=false&timestamp=2020-05-03T12:19:47.706000000Z&customFacets=&limitCustomFacetWidth=true&dateRangeStart=2020-05-03T11:19:22.962Z&interval=PT1H&resource=global&scrollTimestamp=2020-05-03T12:17:23.829686000Z&dateRangeUnbound=forwardInTime
#     import google.cloud.logging # Don't conflict with standard logging
#     from google.cloud.logging.handlers import CloudLoggingHandler, setup_logging
#     client = google.cloud.logging.Client()
#     handler = CloudLoggingHandler(client)
#     setup_logging(handler)
# except Exception as e:
#     logging.warning('Setting up cloud logging failed with error:\n%s'%str(e))

class Looper(object):

    states = ['rec', 'play', 'pre_rec', 'pre_play','pause','metronome']

    transitions = [
        # trigger                       # source        # destination
        ['release_play_button',         'init',         'metronome'],
        #
        ['release_rec_button',         'metronome',     'pre_rec'],
        #
        ['release_rec_button',          'play',         'pre_rec'],
        #
        ['start_recording',            'pre_rec',       'rec'],
        ['release_play_button',         'pre_rec',      'play'],
        ['release_back_button',         'pre_rec',      'play'],
        #
        ['release_play_button',         'rec',          'pre_play'],
        ['release_rec_button',          'rec',          'pre_rec'],
        ['release_back_button',         'rec',          'play'],
        #
        ['end_recording',              'pre_play',     'play'], # added current recording
        ['release_rec_button',          'pre_play',     'pre_rec'],
        ['release_back_button',         'pre_play',     'play'], # didnt add current recording
    ]

    def __init__(self):

        
        latency = 50e-3 # seconds (half of what is measured in the test_latency script)
        self.latency_samples = int(float(latency)*float(sample_rate))
        
        self.n_loop = 0
        self.n_loop_previous = 0
        self.loops = []

        self.machine = Machine(
            model = self,
            states = self.states, 
            transitions = self.transitions, 
            initial = 'init')
        self.init_hardware()
        self.init_files()
        self.init_metronome()
        
        led_square()
        play_led.on()

        self.last_written_file_i =0 
        self.writing = False

        # TODO: move this to a better place
        self.fade_samples = int(fade_time*sample_rate)
        self.fadein_mask = np.ones((self.fade_samples,2))
        self.fadeout_mask = np.ones((self.fade_samples,2))
        for i in range(self.fade_samples):
            self.fadein_mask[i]*=i/self.fade_samples
            self.fadeout_mask[-1-i]*=i/self.fade_samples

    def on_enter_metronome(self):
        self.on_enter()

        back_led.on()
        forw_led.on()
        self.start_metronome()

    def on_exit_metronome(self):

        # Make a 4/4 metronome loop
        self.metronome_loop = np.concatenate((
            self.metronome_sound,
            np.tile(self.metronome_sound/2,(3,1))
            ))
        self.update_loop() # will set the loop to be the metronome loop

        
        self.player_thread = threading.Timer(
            self.time_to_next_loop_start()+(3-self.beat)*self.seconds_per_beat()-play_blocking_delta, 
            self.loop_player) # start playing the loop
        self.player_thread.daemon = False
        self.player_thread.start()

    @timing
    def update_loop(self):
        logging.debug('Updating loop ...')
        if self.n_loop > 0:
            if self.n_loop != self.n_loop_previous:
                
                self.n_loop_previous = self.n_loop

                loop_lengths = [len(l) for l in self.loops[:self.n_loop]]
                
                # Round the longest loop to a certain number of beats
                loop_n_samples = round(max(loop_lengths)/self.samples_per_beat())*self.samples_per_beat()

                loop = np.zeros((loop_n_samples,2), dtype = 'float32')
                for l in self.loops:

                    # trim
                    l = self.trim(l)
                    # fade in/out
                    l = self.fade(l)

                    # stitch N versions of the sample together and add to master loop
                    loop += np.tile(l,(round(loop_n_samples/len(l)),1))

                self.loop = loop
                
                self.second_half_loop = deepcopy(self.loop[len(self.half_loop):])
        else:
            logging.debug('Loop is just the metronome')
            self.loop = deepcopy(self.metronome_loop)

        self.loop_time = float(len(self.loop))/float(sample_rate)
        logging.debug('Loop duration:  %.2f s'%(self.loop_time))

    @timing
    def trim(self, loop):

        # Number of samples there should be in this loop
        loop_n_samples = round(len(loop)/self.samples_per_beat())*self.samples_per_beat()

        # Adjust for latency
        if loop_n_samples>len(loop)-self.latency_samples:
            trimmed_loop = np.zeros((loop_n_samples,2))
            trimmed_loop[:len(loop)-self.latency_samples] += loop[self.latency_samples:len(loop)]
        else:
            trimmed_loop = loop[self.latency_samples:loop_n_samples+self.latency_samples]

        return trimmed_loop

    def fade(self, loop, where = ['in','out']):
        
        # Fade in/out
        if 'in' in where:
            loop[:self.fade_samples]*=self.fadein_mask
        if 'out' in where:
            loop[-self.fade_samples:]*=self.fadeout_mask
        return loop

    def loop_player(self):
        
        # At beginning of loop, exit pre- states
        if self.state == 'pre_rec':
            self.start_recording()
            t = threading.Timer(
                self.loop_time/2+timing_precision,
                self.half_end_recording)
            t.daemon = False
            t.start()
            self.write(self.loop)

        elif self.state == 'pre_play':
            t = threading.Timer(0,self.end_recording)
            t.daemon = False
            t.start()
            self.write(self.half_loop)
            self.write(self.second_half_loop)
        else:
            self.write(self.loop)

        # Schedule this function to play in a loop-durations time
        self.player_thread = threading.Timer(
            self.time_to_next_loop_start()-play_blocking_delta, # start writing just before
            self.loop_player)
        self.player_thread.daemon = False
        self.player_thread.start()

    def write(self,sound):
        '''
        Nearly-blocking
        '''
        if self.writing:
            raise RuntimeError('Trying to play two sounds at the same time')
        else:
            self.writing = True

        # So we can modify the sound
        # object without interfering with the playing
        sound = deepcopy(sound)

        if file1_flag.isSet():
            # Currently playing file 1

            # Write to file 0 
            with sf.SoundFile(temp_playing_filename[0], 
                mode='w',channels = 2, samplerate=sample_rate) as f:
                f.write(sound)
            
            # Wait for file 0 to start reading
            while file1_flag.isSet():
                time.sleep(timing_precision)

            
        elif not file1_flag.isSet():
            # Currently playing file 0

            # Write to file 1
            with sf.SoundFile(temp_playing_filename[1], 
                mode='w',channels = 2, samplerate=sample_rate) as f:
                f.write(sound)
            
            # Wait for file 1 to start reading
            while not file1_flag.isSet():
                time.sleep(timing_precision)

        # Has started playing
        time_to_end = len(sound)/sample_rate
        self.time_at_end = time.time()+time_to_end
        time.sleep(time_to_end-2*play_blocking_delta)
        self.writing = False
        
        

    def init_files(self):
        
        self.repo_directory = '/home/pi/Desktop/pi-looper/'
        self.src_directory = '/home/pi/Desktop/pi-looper/src/'
        self.recording_directory = recording_directory + datetime.fromtimestamp(
            time.time()).strftime('%Y-%m-%d__%H-%M-%S/')
        os.mkdir(self.recording_directory)
        time.sleep(timing_precision)
        self.loop_filename = self.recording_directory+'loop_{:03d}.wav'

    def init_metronome(self):
        self.bpm = initial_bpm
        self.time_at_end = time.time()
        self.beat = -1
        metronome_file = self.src_directory+'data/high_hat_001.wav'
        # Extract data and sampling rate from file
        self.metronome_sound_raw, metronome_sr = sf.read(metronome_file, dtype='float32')
        if metronome_sr != sample_rate:
            raise RuntimeError('Wrong metronome sample rate: %d instead of %d'%(metronome_sr,sample_rate))
    
    def start_metronome(self):

        self.beat = (self.beat +1) %4
        logging.debug('beat %d'%self.beat)

        # Play metronome beat
        samples = self.samples_per_beat()

        if samples>len(self.metronome_sound_raw):
            extended = np.zeros((samples,2))
            extended[:len(self.metronome_sound_raw)] = self.metronome_sound_raw
            self.metronome_sound = extended
        else:
            self.metronome_sound = self.metronome_sound_raw[:samples]

        # Adjust if we're on the one
        if self.beat == 0:
            self.write(self.metronome_sound)
        else:
            self.write(self.metronome_sound/2)

        if self.state == 'metronome' or (self.state != 'metronome' and self.beat<3):
            t = threading.Timer(self.time_to_next_loop_start()-play_blocking_delta
            ,self.start_metronome)
            t.daemon = False
            t.start()

    def seconds_per_beat(self):
        return 60./float(self.bpm)
    
    def samples_per_beat(self):
        return int(sample_rate*self.seconds_per_beat())

    def press_forw_button(self):
        if self.state == 'metronome':
            while forw_button.is_active and self.bpm < 300:
                back_led.off()
                self.bpm += 2
                logging.debug("bpm = %d"%self.bpm)
                time.sleep(0.06)
            back_led.on()

    def press_back_button(self):
        if self.state == 'metronome':
            while back_button.is_active and self.bpm > 40:
                forw_led.off()
                self.bpm -= 2
                logging.debug("bpm = %d"%self.bpm)
                time.sleep(0.06)
            forw_led.on()

    def init_hardware(self):

        # Button events
        rec_button.when_deactivated = self.release_rec_button
        play_button.when_deactivated = self.release_play_button
        back_button.when_deactivated = self.release_back_button
        forw_button.when_activated = self.press_forw_button
        back_button.when_activated = self.press_back_button

        self.blink_on_time = 60./240. #seconds

    def start_recording(self):
        record_flag.set()
        self.trigger('start_recording')

    def end_recording(self):
        time.sleep(latency*3) # to not miss any notes! 
        record_flag.clear()
        self.add_recording_to_loops()
        self.update_loop()
        self.trigger('end_recording')

    def half_end_recording(self):
        sound, sr = sf.read(temp_recording_filename, dtype='float32')
        n_samples_half_loop = int(len(self.loop)/2)
        self.half_loop = deepcopy(self.loop[:n_samples_half_loop])

        # If this is the first recording, 
        # we want to remove the metronome
        if self.n_loop == 0:
            self.half_loop *= 0

        sound = self.trim(sound[:n_samples_half_loop+self.latency_samples])
        sound = self.fade(sound, where = 'in')
        self.half_loop += sound


    def add_recording_to_loops(self):
        # Extract audio
        loop_filename = self.loop_filename.format(self.n_loop)
        ts = time.time()
        shutil.copyfile(temp_recording_filename, loop_filename)
        te = time.time()
        logging.debug('Copying file took:  %.0f ms'%((te-ts)*1e3))

        ts = time.time()
        sound, sr = sf.read(loop_filename, dtype='float32')
        te = time.time()
        logging.debug('Reading file took: %.0f ms'%((te-ts)*1e3))
        
        self.loops.append(sound)
        self.n_loop += 1

    def on_enter(self):
        all_leds_off()

    def on_enter_play(self):
        self.on_enter()

        play_led.on()
        record_flag.clear()

    def on_enter_rec(self):
        self.on_enter()
        rec_led.on()
    
    def time_to_next_loop_start(self):
        t= self.time_at_end -time.time() # time to end currently playing loop
        logging.debug('Time to next loop: %.2f'%t)
        return t
    

    def on_enter_pre_play(self):
        self.on_enter()
        self.blink(play_led)
    
        

    def blink(self, led):
        led.blink(on_time = self.blink_on_time, off_time= self.seconds_per_beat()-self.blink_on_time)
        

    def on_enter_pre_rec(self):
        self.on_enter()        
        self.blink(rec_led)


    def kill(self):
        logging.debug('Stopping looper...')
        stop_streams_flags.set()
        recording_thread.join()
        playing_thread.join()

def all_leds_off():
    for l in [rec_led,play_led,back_led,forw_led]:
        l.off()
def led_square():
    all_leds_off()
    for l in [rec_led,forw_led,play_led,back_led]:
        l.on()
        time.sleep(0.1)
        l.off()
def led_circle():
    all_leds_off()
    for l in [rec_led,play_led,forw_led,back_led]:
        l.on()
        time.sleep(0.1)
        l.off()

if __name__ == "__main__":
    try:
        # Settings
        initial_bpm = 100
        latency = 0.05 # works fine 
        sample_rate = 44100
        timing_precision = 0.1e-3 # milisecond
        fade_time = 0.03 # seconds
        play_blocking_delta = 0.1 # sets how much time in advance you have to click stuff
        timing_precision_samples = int(timing_precision*sample_rate)
        recording_directory = '/home/pi/Desktop/pi-looper-data/'

        # LEDs
        rec_led = LED(18)
        play_led = LED(8)
        back_led = LED(14)
        forw_led = LED(24)
        
        # Buttons
        rec_button = Button(23)
        play_button = Button(7)
        back_button = Button(15)
        forw_button = Button(25)
        
        def is_all_buttons_active():
            for b in [rec_button,play_button,back_button,forw_button]:
                if not b.is_active:
                    return False
            return True


        stop_streams_flags = threading.Event()
        stop_streams_flags.clear()

        # Initialize recording
        temp_recording_filename = os.path.join(
            recording_directory,
            'temp_recording_file.wav')

        record_flag = threading.Event()
        recording_thread = threading.Thread(name='recorder',
                        target=daemons.recorder,
                        args=(record_flag,
                        stop_streams_flags,
                        timing_precision,
                        temp_recording_filename),
                        daemon = False)
        recording_thread.start()

        # setup audio
        temp_playing_filename = [
            os.path.join(
            recording_directory,
            'temp_playing_file0.wav'),
            os.path.join(
            recording_directory,
            'temp_playing_file1.wav'),
        ]
        for fn in temp_playing_filename:
            with sf.SoundFile(fn, mode='w',channels = 2, samplerate=sample_rate) as f:
                f.write(np.zeros((int(sample_rate*timing_precision),2)))
        
        playing_flag = threading.Event()
        file1_flag = threading.Event()
        playing_flag.set()
        file1_flag.clear()
        playing_thread = threading.Thread(name='player',
                        target=daemons.player,
                        args=(playing_flag,
                        file1_flag,
                        stop_streams_flags,
                        latency,
                        temp_playing_filename),
                        daemon = False)
        playing_thread.start()



        looper = Looper()
        while not is_all_buttons_active():
            time.sleep(1)

        looper.kill()
        logging.info('User restarted the looper')
        restart_program()

    except sd.PortAudioError as e:
        logging.critical('Audio interface issue:\n%s'%str(e))
        # Indicate the error with LEDs
        all_leds_off()
        rec_led.blink(1,1)
        forw_led.blink(1,1)
    
    except Exception as e:
        logging.critical(e)
        # Indicate the error with LEDs
        all_leds_off()
        rec_led.blink(1,1)
        forw_led.blink(1,1)
        back_led.blink(1,1)

    while not is_all_buttons_active():
        time.sleep(1)
    restart_program()
