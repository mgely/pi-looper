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
logging.basicConfig(level=logging.DEBUG,format='(%(threadName)-10s) %(message)s')
logging.getLogger('transitions').setLevel(logging.INFO)



class Looper(object):

    states = ['rec', 'play', 'pre_rec', 'pre_play']

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
        self.check_sample_rates()
        self.n_loop = 0
        self.n_loop_previous = 0
        self.loops = []

        self.machine = Machine(model = self,states = self.states, transitions = self.transitions, 
            initial = 'play')
        self.init_hardware()
        self.init_timing()
        self.init_files()
        self.init_metronome()
        self.init_recording()

        self.scheduled_events = []

        self.update_loop()
        self.loop_player()

    def check_sample_rates(self):

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

    def update_loop(self):
        if self.n_loop > 0:
            if self.n_loop != self.n_loop_previous:
                self.n_loop_previous = self.n_loop

                loop_lengths = [len(l) for l in self.loops[:self.n_loop]]
                longest_loop_n_samples = max(loop_lengths)
                longest_loop_index = loop_lengths.index(max(loop_lengths))

                self.loop = self.loops[longest_loop_index]
                for i,l in enumerate(self.loops):
                    if i != longest_loop_index:
                        self.loop += np.tile(l,(longest_loop_n_samples/len(l),1))
        else:
            self.loop = self.metronome_loop
        self.loop_time = float(len(self.loop))/float(self.sample_rate)
        logging.debug('Loop duration = %.2f s'%self.loop_time)

    def loop_player(self):
        sd.play(self.loop,samplerate=self.sample_rate)
        if self.start_time is None:
            self.start_time = time.time()
        else:
            self.start_time += self.loop_time

        t = threading.Timer(self.time_to_next_loop_start(),self.loop_player)
        t.start()

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
        metronome_sound, metronome_sr = sf.read(metronome_file)
        if metronome_sr != self.sample_rate:
            raise RuntimeError('Wrong metronome sample rate: %d instead of %d'%(metronome_sr,self.sample_rate))
        
        if len(metronome_sound) > self.samples_per_beat:
            self.metronome_loop = metronome_sound[:self.samples_per_beat]
        else:
            self.metronome_loop = np.zeros((self.samples_per_beat,2))
            self.metronome_loop[:len(metronome_sound)] = metronome_sound

        # Make it 4/4
        self.metronome_loop = np.concatenate((self.metronome_loop,np.tile(self.metronome_loop/2,(3,1))))

    def init_timing(self):
        self.bpm = 120
        self.seconds_per_beat = 60./float(self.bpm)
        self.samples_per_beat = int(self.sample_rate*self.seconds_per_beat)
        self.start_time = None
        self.timing_precision = 0.3e-3 # half a milisecond
        # TODO: deal with latency


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

    def start_recording(self):
        self.record_flag.set()
        self.trigger('start_recording')

    def end_recording(self):
        self.record_flag.clear()
        self.add_recording_to_loops()
        self.trigger('end_recording')

    def add_recording_to_loops(self):
        # Extract audio
        loop_filename = self.loop_filename.format(self.n_loop)
        shutil.copyfile(self.temp_recording_filename, loop_filename)
        sound, sr = sf.read(loop_filename)

        # Round the number of samples to the nearest number of bars
        n_samples_in_loop = round(float(len(sound))/float(self.samples_per_beat*4))*self.samples_per_beat*4

        if len(sound) > n_samples_in_loop:
            loop = sound[:n_samples_in_loop]
        else:
            loop = np.zeros((n_samples_in_loop,2))
            loop[:len(sound)] = sound
        
        self.loops.append(loop)
        self.n_loop += 1
        self.update_loop()


    def all_leds_off(self):
        for l in [self.rec_led,self.play_led]:
            l.off()

    def cancel_all_scheduled_events(self):
        for i in range(len(self.scheduled_events)):
            self.scheduled_events[i].cancel()
        self.scheduled_events = []

    def on_enter(self):
        self.cancel_all_scheduled_events()
        self.all_leds_off()

    def on_enter_play(self):
        self.on_enter()

        self.play_led.on()
        self.record_flag.clear()

    def on_enter_rec(self):
        self.on_enter()
        self.rec_led.on()
    
    def time_to_next_loop_start(self):
        return self.start_time + self.loop_time - time.time()

    def on_enter_pre_play(self):
        self.on_enter()

        event = threading.Timer(self.time_to_next_loop_start() ,self.end_recording)
        event.start()
        self.scheduled_events.append(event)


        self.blink(self.play_led)
    
    def time_to_next_beat(self):
        
        t = self.start_time - time.time()
        while t < 0:
            t += self.seconds_per_beat
        return t
        

    def blink(self, led):

        def start_blinking():
            led.blink(on_time = self.blink_on_time, off_time= self.seconds_per_beat-self.blink_on_time)

        t = threading.Timer(self.time_to_next_beat() ,start_blinking)
        t.start()
        self.scheduled_events.append(t)
        

    def on_enter_pre_rec(self):
        self.on_enter()
        
        event = threading.Timer(self.time_to_next_loop_start() ,self.start_recording)
        event.start()
        self.scheduled_events.append(event)
        
        self.blink(self.rec_led)

if __name__ == "__main__":
    l = Looper()    
    while True:
        time.sleep(l.timing_precision)