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
from copy import deepcopy
logging.basicConfig(level=logging.DEBUG,format='(%(threadName)-10s) %(message)s')
logging.getLogger('transitions').setLevel(logging.INFO)


def run_looper(looper_running_flag):
    active_looper = Looper()
    while looper_running_flag:
        time.sleep(active_looper.timing_precision)

class LooperManager(object):

    def __init__(self):
        self.start_running_looper()
    
    def start_running_looper(self):
        self.looper_running_flag = threading.Event()
        self.looper_thread = threading.Thread(name='looper',
                      target=run_looper,
                      args=(self.looper_running_flag,),
                      daemon = True)
        self.looper_thread.start()


class Looper(object):

    states = ['rec', 'play', 'pre_rec', 'pre_play','out_of_use']

    transitions = [
        # trigger                       # source        # destination
        ['release_rec_button',          'play',         'pre_rec'],
        #
        ['start_recording',            'pre_rec',      'rec'],
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
        
        self.sample_rate = 44100
        self.latency = 50e-3 # seconds (half of what is measured in the test_latency script)
        self.latency_samples = int(float(self.latency)*float(self.sample_rate))
        self.audio_out = sd.OutputStream(
            samplerate=self.sample_rate,
            channels = 2,
            latency = 0.05,
            dtype='float32')
        self.audio_out.start()
        
        self.n_loop = 0
        self.n_loop_previous = 0
        self.loops = []

        self.machine = Machine(
            model = self,
            states = self.states, 
            transitions = self.transitions, 
            initial = 'play')
        self.init_hardware()
        self.init_timing()
        self.init_files()
        self.init_metronome()
        self.init_recording()

        self.update_loop()
        self.loop_player()
    
    def __enter__(self):
        return self

    def __exit__(self ,type, value, traceback):
        logging.debug('Stopping looper...')
        sd.stop()
        self.player_thread.cancel()
        self.state = 'out_of_use'

    def update_loop(self):
        if self.n_loop > 0:
            if self.n_loop != self.n_loop_previous:
                # logging.debug('Updating loop...')
                self.n_loop_previous = self.n_loop

                loop_lengths = [len(l) for l in self.loops[:self.n_loop]]
                loop_n_samples = round(max(loop_lengths)/self.samples_per_beat)*self.samples_per_beat

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

        self.loop_time = float(len(self.loop))/float(self.sample_rate)
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
            t = threading.Timer(self.loop_time/2+self.timing_precision,self.half_end_recording)
            t.start()
            self.audio_out.write(self.loop)

        elif self.state == 'pre_play':
            t = threading.Timer(0,self.end_recording)
            t.start()
            self.audio_out.write(self.half_loop)
            self.audio_out.write(self.loop[len(self.half_loop):])
        else:
            self.audio_out.write(self.loop)

        # Schedule this function to play in a loop-durations time
        self.player_thread = threading.Timer(self.time_to_next_loop_start(),self.loop_player)
        self.player_thread.start()

    def init_recording(self):
        
        self.record_flag = threading.Event()
        self.recording_thread = threading.Thread(name='recorder',
                      target=daemons.recorder,
                      args=(self.record_flag,
                        self.timing_precision,
                      self.temp_recording_filename),
                      daemon = True)
        self.recording_thread.start()

    def init_files(self):
        
        self.repo_directory = '/home/pi/Desktop/pi-looper/'
        self.src_directory = '/home/pi/Desktop/pi-looper/src/'
        self.recording_directory = '/home/pi/Desktop/pi-looper-data/'
        self.recording_directory += datetime.fromtimestamp(
            time.time()).strftime('%Y-%m-%d__%H-%M-%S/')
        os.mkdir(self.recording_directory)
        time.sleep(self.timing_precision)
        self.temp_recording_filename = self.recording_directory+'temp.wav'
        self.loop_filename = self.recording_directory+'loop_{:03d}.wav'

    def init_metronome(self):
        
        metronome_file = self.src_directory+'data/high_hat_001.wav'
        # Extract data and sampling rate from file
        metronome_sound, metronome_sr = sf.read(metronome_file, dtype='float32')
        if metronome_sr != self.sample_rate:
            raise RuntimeError('Wrong metronome sample rate: %d instead of %d'%(metronome_sr,self.sample_rate))
        
        if len(metronome_sound) > self.samples_per_beat:
            self.metronome_loop = metronome_sound[:self.samples_per_beat]
        else:
            self.metronome_loop = np.zeros((self.samples_per_beat,2), dtype = 'float32')
            self.metronome_loop[:len(metronome_sound)] = metronome_sound

        # Make it 4/4
        self.metronome_loop = np.concatenate((self.metronome_loop,np.tile(self.metronome_loop/2,(3,1))))

    def init_timing(self):
        self.bpm = 120
        self.seconds_per_beat = 60./float(self.bpm)
        self.samples_per_beat = int(self.sample_rate*self.seconds_per_beat)
        self.start_time = None
        self.timing_precision = 0.3e-3 # half a milisecond


    def init_hardware(self):

        # LEDs
        self.rec_led = LED(18)
        self.play_led = LED(8)
        self.back_led = LED(14)
        self.forw_led = LED(24)

        # Buttons
        self.rec_button = Button(23)
        self.play_button = Button(7)
        self.back_button = Button(15)
        self.forw_button = Button(25)

        # Button events
        self.rec_button.when_deactivated = self.release_rec_button
        self.play_button.when_deactivated = self.release_play_button
        # self.forw_button.when_deactivated = self.release_forw_button
        self.back_button.when_deactivated = self.release_back_button

        self.blink_on_time = 60./240. #seconds

        # Check sample rates of input and output devices

        device_info = sd.query_devices(None, 'input')
        logging.debug(device_info)
        samplerate_input = int(device_info['default_samplerate'])
        if samplerate_input != self.sample_rate:
            raise RuntimeError('Wrong input sample rate: %d instead of %d'%(samplerate_input,self.sample_rate))

        device_info = sd.query_devices(None, 'output')
        logging.debug(device_info)
        samplerate_output = int(device_info['default_samplerate'])
        if samplerate_output != self.sample_rate:
            raise RuntimeError('Wrong ouptut sample rate: %d instead of %d'%(samplerate_output,self.sample_rate))

    def start_recording(self):
        self.record_flag.set()
        self.trigger('start_recording')

    def end_recording(self):
        self.record_flag.clear()
        self.add_recording_to_loops()
        self.update_loop()
        self.trigger('end_recording')

    def half_end_recording(self):
        sound, sr = sf.read(self.temp_recording_filename, dtype='float32')
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
        shutil.copyfile(self.temp_recording_filename, loop_filename)
        te = time.time()
        logging.debug('Copying file took:\n\t\t\t\t\t %.0f ms'%((te-ts)*1e3))

        ts = time.time()
        sound, sr = sf.read(loop_filename, dtype='float32')
        te = time.time()
        logging.debug('Reading file took:\n\t\t\t\t\t %.0f ms'%((te-ts)*1e3))
       
        self.loops.append(sound)
        self.n_loop += 1


    def all_leds_off(self):
        for l in [self.rec_led,self.play_led]:
            l.off()

    def on_enter(self):
        self.all_leds_off()

    def on_enter_play(self):
        self.on_enter()

        self.play_led.on()
        self.record_flag.clear()

    def on_enter_rec(self):
        self.on_enter()
        self.rec_led.on()
    
    def time_to_next_loop_start(self):
        t = self.start_time + self.loop_time - time.time()
        logging.debug('Time to next loop start = %.4f'%t)
        return t

    def on_enter_pre_play(self):
        self.on_enter()
        self.blink(self.play_led)
    
    def time_to_next_beat(self):
        
        t = self.start_time - time.time()
        while t < 0:
            t += self.seconds_per_beat
        return t
        

    def blink(self, led):

        # def start_blinking():
        #     led.blink(on_time = self.blink_on_time, off_time= self.seconds_per_beat-self.blink_on_time)
        # t = threading.Timer(self.time_to_next_beat() ,start_blinking)
        # t.start()
        # self.scheduled_events.append(t)

        led.blink(on_time = self.blink_on_time, off_time= self.seconds_per_beat-self.blink_on_time)
        

    def on_enter_pre_rec(self):
        self.on_enter()        
        self.blink(self.rec_led)

if __name__ == "__main__":
    # with Looper() as l:
    #     time.sleep(0.3)

    # l = Looper()
    # time.sleep(1)
    # l.release_rec_button()
    # time.sleep(2)
    # l.add_recording_to_loops()

    lm = LooperManager()
    while True:
        time.sleep(1) 