import os
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
logging.basicConfig(level=logging.DEBUG,format='(%(threadName)-10s) %(message)s')
logging.getLogger('transitions').setLevel(logging.INFO)

# Settings
timing_precision = 0.3e-3 # half a milisecond
recording_directory = '/home/pi/Desktop/pi-looper-data/'

# Initialize recording
temp_recording_filename = os.path.join(recording_directory,'temp_recording_file.wav')

record_flag = threading.Event()
recording_thread = threading.Thread(name='recorder',
                target=daemons.recorder,
                args=(record_flag,
                timing_precision,
                temp_recording_filename),
                daemon = True)
recording_thread.start()

# setup audio
sample_rate = 44100
audio_out = sd.OutputStream(
    samplerate=sample_rate,
    channels = 2,
    latency = 0.05,
    dtype='float32')
audio_out.start()

# LEDs
rec_led = LED(18)
play_led = LED(8)
back_led = LED(14)
forw_led = LED(24)

def all_leds_off():
    for l in [rec_led,play_led,back_led,forw_led]:
        l.off()

# Buttons
rec_button = Button(23)
play_button = Button(7)
back_button = Button(15)
forw_button = Button(25)


def run_looper(looper_running_flag):
    with Looper():
        while looper_running_flag.isSet():
            time.sleep(timing_precision)
    logging.debug('Terminating looper')

class LooperManager(object):

    states = ['looper_active', 'looper_inactive']

    transitions = [
        # trigger                       # source                # destination
        ['hold_forw_back',              'looper_active',        'looper_inactive'],
        ['release_play_button',         'looper_inactive',      'looper_active'],
    ]

    def __init__(self):
        self.machine = Machine(
            model = self,
            states = self.states, 
            transitions = self.transitions, 
            initial = 'looper_active')
        self.on_enter_looper_active()
    
    def start_running_looper(self):
        self.looper_running_flag = threading.Event()
        self.looper_running_flag.set()
        self.looper_thread = threading.Thread(name='looper',
                      target=run_looper,
                      args=(self.looper_running_flag,),
                      daemon = True)
        self.looper_thread.start()

        
    def setup_stop_button(self):
        all_leds_off()

        def check_if_hold_forw_back():
            def future_check_if_hold_forw_back():
                if forw_button.is_active and back_button.is_active:
                    self.trigger('hold_forw_back')

            t = threading.Timer(1,future_check_if_hold_forw_back)
            t.daemon = True
            t.start()
        back_button.when_activated = check_if_hold_forw_back
        
    def setup_start_button(self):
        play_button.when_deactivated = self.release_play_button
        all_leds_off()
        # play_led.blink(on_time = 0.1, off_time= 0.4)
        play_led.on()

    def on_enter_looper_active(self):
        self.start_running_looper()
        self.setup_stop_button()

    def on_enter_looper_inactive(self):
        self.looper_running_flag.clear()
        self.setup_start_button()

class Looper(object):

    states = ['rec', 'play', 'pre_rec', 'pre_play','out_of_use']

    transitions = [
        # trigger                       # source        # destination
        ['release_play_button',         'metronome',    'play'],
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
        
        self.latency = 50e-3 # seconds (half of what is measured in the test_latency script)
        self.latency_samples = int(float(self.latency)*float(sample_rate))
        
        self.n_loop = 0
        self.n_loop_previous = 0
        self.start_time = None
        self.loops = []

        self.machine = Machine(
            model = self,
            states = self.states, 
            transitions = self.transitions, 
            initial = 'metronome')
        self.init_hardware()
        self.init_files()
        self.start_metronome()

    def on_exit_metronome(self):
        
        if len(self.metronome_sound) > self.samples_per_beat():
            self.metronome_loop = self.metronome_sound[:self.samples_per_beat()]
        else:
            self.metronome_loop = np.zeros((self.samples_per_beat(),2), dtype = 'float32')
            self.metronome_loop[:len(self.metronome_sound)] = self.metronome_sound

        # Make it 4/4
        self.metronome_loop = np.concatenate((self.metronome_loop,np.tile(self.metronome_loop/2,(3,1))))

        self.update_loop() # will set the loop to be the metronome loop
        self.loop_player() # start playing the loop
        play_led.on() # indicate that we are in playing mode
    
    def __enter__(self):
        return self

    def __exit__(self ,type, value, traceback):
        logging.debug('Stopping looper...')
        self.state = 'out_of_use'
        audio_out.stop()
        audio_out.start()
        record_flag.clear()

    def update_loop(self):
        if self.n_loop > 0:
            if self.n_loop != self.n_loop_previous:
                # logging.debug('Updating loop...')
                self.n_loop_previous = self.n_loop

                loop_lengths = [len(l) for l in self.loops[:self.n_loop]]
                loop_n_samples = round(max(loop_lengths)/self.samples_per_beat())*self.samples_per_beat()

                loop = np.zeros((loop_n_samples,2), dtype = 'float32')
                for l in self.loops:
                    l = np.tile(l,(round(loop_n_samples/len(l)),1))
  
                    # Adjust for latency
                    l = l[self.latency_samples:]

                    if len(l) > loop_n_samples:
                        loop += l[:loop_n_samples]
                    else:
                        loop[:len(l)] += l
                self.loop = loop
        else:
            self.loop = self.metronome_loop

        self.loop_time = float(len(self.loop))/float(sample_rate)
        logging.debug('Loop time:\n\t\t\t\t\t %.0f s'%(self.loop_time))

    def loop_player(self):
        
        # Set beginning of loop time
        # Important: should always be before the loop
        # is updated.
        if self.start_time is None:
            self.start_time = time.time()
            logging.debug('Starting time set')
        else:
            self.start_time += self.loop_time

        # At beginning of loop, exit pre- states
        if self.state == 'pre_rec':
            self.start_recording()
            t = threading.Timer(self.loop_time/2+timing_precision,self.half_end_recording)
            t.daemon = True
            t.start()
            audio_out.write(self.loop)

        elif self.state == 'pre_play':
            t = threading.Timer(0,self.end_recording)
            t.daemon = True
            t.start()
            audio_out.write(self.half_loop)
            audio_out.write(self.loop[len(self.half_loop):])
        elif self.state == 'out_of_use':
            return
        else:
            audio_out.write(self.loop)

        # Schedule this function to play in a loop-durations time
        self.player_thread = threading.Timer(self.time_to_next_loop_start(),self.loop_player)
        self.player_thread.daemon = True
        self.player_thread.start()

    def init_files(self):
        
        self.repo_directory = '/home/pi/Desktop/pi-looper/'
        self.src_directory = '/home/pi/Desktop/pi-looper/src/'
        self.recording_directory = recording_directory + datetime.fromtimestamp(
            time.time()).strftime('%Y-%m-%d__%H-%M-%S/')
        os.mkdir(self.recording_directory)
        time.sleep(timing_precision)
        self.loop_filename = self.recording_directory+'loop_{:03d}.wav'

    def init_metronome(self):
        self.bpm = 100
    
        metronome_file = self.src_directory+'data/high_hat_001.wav'
        # Extract data and sampling rate from file
        self.metronome_sound, metronome_sr = sf.read(metronome_file, dtype='float32')
        if metronome_sr != sample_rate:
            raise RuntimeError('Wrong metronome sample rate: %d instead of %d'%(metronome_sr,sample_rate))
    
    def start_metronome(self):
        if self.state == 'metronome':
            audio_out.write(self.metronome_sound[:min(self.samples_per_beat(),len(self.metronome_sound))])
            t = threading.Timer(self.seconds_per_beat(),self.start_metronome)
            t.daemon = True
            t.start()

    def seconds_per_beat(self):
        return 60./float(self.bpm)
    
    def samples_per_beat(self):
        return int(sample_rate*self.seconds_per_beat())

    def release_forw_button(self):
        if self.state == 'metronome' and self.bpm < 250:
            self.bpm += 2
        else:
            self.trigger('release_forw_button')

    def release_back_button(self):
        if self.state == 'metronome' and self.bpm > 40:
            self.bpm -= 2
        else:
            self.trigger('release_back_button')

    def init_hardware(self):

        # Button events
        rec_button.when_deactivated = self.release_rec_button
        play_button.when_deactivated = self.release_play_button
        forw_button.when_deactivated = self.release_forw_button
        back_button.when_deactivated = self.release_back_button

        self.blink_on_time = 60./240. #seconds

        # Check sample rates of input and output devices

        device_info = sd.query_devices(None, 'input')
        logging.debug(device_info)
        samplerate_input = int(device_info['default_samplerate'])
        if samplerate_input != sample_rate:
            raise RuntimeError('Wrong input sample rate: %d instead of %d'%(samplerate_input,sample_rate))

        device_info = sd.query_devices(None, 'output')
        logging.debug(device_info)
        samplerate_output = int(device_info['default_samplerate'])
        if samplerate_output != sample_rate:
            raise RuntimeError('Wrong ouptut sample rate: %d instead of %d'%(samplerate_output,sample_rate))

    def start_recording(self):
        record_flag.set()
        self.trigger('start_recording')

    def end_recording(self):
        record_flag.clear()
        self.add_recording_to_loops()
        self.update_loop()
        self.trigger('end_recording')

    def half_end_recording(self):
        sound, sr = sf.read(temp_recording_filename, dtype='float32')
        n_samples_half_loop = int(len(self.loop)/2)
        self.half_loop = deepcopy(self.loop[:n_samples_half_loop])

        l = sound[self.latency_samples:]

        if len(l) > n_samples_half_loop:
            self.half_loop += l[:n_samples_half_loop]
        else:
            self.half_loop[:len(l)] += l

    def add_recording_to_loops(self):
        # Extract audio
        loop_filename = self.loop_filename.format(self.n_loop)
        ts = time.time()
        shutil.copyfile(temp_recording_filename, loop_filename)
        te = time.time()
        logging.debug('Copying file took:\n\t\t\t\t\t %.0f ms'%((te-ts)*1e3))

        ts = time.time()
        sound, sr = sf.read(loop_filename, dtype='float32')
        te = time.time()
        logging.debug('Reading file took:\n\t\t\t\t\t %.0f ms'%((te-ts)*1e3))
       
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
        t = self.start_time + self.loop_time - time.time()
        logging.debug('Time to next loop start = %.4f'%t)
        return t

    def on_enter_pre_play(self):
        self.on_enter()
        self.blink(play_led)
    
    def time_to_next_beat(self):
        
        t = self.start_time - time.time()
        while t < 0:
            t += self.seconds_per_beat()
        return t
        

    def blink(self, led):
        led.blink(on_time = self.blink_on_time, off_time= self.seconds_per_beat()-self.blink_on_time)
        

    def on_enter_pre_rec(self):
        self.on_enter()        
        self.blink(rec_led)


if __name__ == "__main__":
    lm = LooperManager()
    while True:
        time.sleep(1) 