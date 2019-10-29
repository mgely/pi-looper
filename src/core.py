import matplotlib.pyplot as plt
import numpy as np
import RPi.GPIO as GPIO
import sounddevice as sd
import soundfile as sf
from daemons import recorder,metronome, player
import threading
import os
import logging
from datetime import datetime
import time
import shutil

logging.basicConfig(level=logging.DEBUG,
                    format='(%(threadName)-10s) %(message)s',
                    )

class Looper:
    def __init__(self):

        # setup timing
        self.bpm = 80
        self.start_time = time.time()
        self.timing_precision = 0.3e-3 # half a milisecond

        self.loop_on_flags = []
        self.n_loop = 0

        self.repo_directory = '/home/pi/Desktop/pi-looper/'
        self.src_directory = '/home/pi/Desktop/pi-looper/src/'

        self.recording_directory = '/home/pi/Desktop/pi-looper-data/'
        self.recording_directory += datetime.fromtimestamp(
            time.time()).strftime('%Y-%m-%d__%H-%M-%S/')
        os.mkdir(self.recording_directory)
        time.sleep(self.timing_precision)
        self.temp_recording_filename = self.recording_directory+'temp.wav'
        self.loop_filename = self.recording_directory+'loop_{:03d}.wav'

        # Configure metronome
        metronome_file = self.src_directory+'data/high_hat_001.wav'
        self.metronome_on_flag = threading.Event()
        self.metronome_thread = threading.Thread(name='metronome',
                      target=metronome,
                      args=(self.metronome_on_flag,
                            self.bpm,
                            self.start_time,
                            self.timing_precision,
                            metronome_file),
                      daemon = True)
        self.metronome_thread.start()
        self.metronome_on()

        self.record_flag = threading.Event()
        self.recording_thread = threading.Thread(name='recorder',
                      target=recorder,
                      args=(self.record_flag,
                        self.timing_precision,
                      self.temp_recording_filename),
                      daemon = True)
        self.recording_thread.start()

        try:
            os.remove(self.temp_recording_filename)
        except FileNotFoundError:
            pass

        #samplerate
        self.sr = 44100

        # GPIO setup
        self.record_led = 4
        self.record_button = 14
        self.state = 'IDLE'

        GPIO.setmode(GPIO.BCM) # call pins by GPIO not PIN numbers, see https://raspberrypi.stackexchange.com/questions/12966/what-is-the-difference-between-board-and-bcm-for-gpio-pin-numbering
        # setup recording LED
        # see https://thepihut.com/blogs/raspberry-pi-tutorials/27968772-turning-on-an-led-with-your-raspberry-pis-gpio-pins
        GPIO.setup(self.record_led, GPIO.OUT) # setup as output
        GPIO.output(self.record_led,GPIO.LOW) # turn off
        # setup push button
        # see https://raspberrypihq.com/use-a-push-button-with-raspberry-pi-gpio/
        # Set pin to be an input pin and set initial value to be pulled low (off)
        GPIO.setup(self.record_button, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
        # Setup event on pin RISING edge
        GPIO.add_event_detect(self.record_button, GPIO.RISING, callback = self.push_record)

    def measure_latency(self):
        # Assuming metronome is on
        self.start_recording()
        time.sleep(60/self.bpm*4.9)
        self.stop_recording()
        time.sleep(self.timing_precision)
        calibration_filename = self.recording_directory+'latency_measurement'
        shutil.copyfile(self.temp_recording_filename, calibration_filename)
        time.sleep(self.timing_precision)
        sound, sr = sf.read(calibration_filename)
        plt.plot(sound)

    def metronome_on(self):
        self.metronome_on_flag.set()

    def metronome_off(self):
        self.metronome_on_flag.clear()

    def start_recording(self):
        # turn on record LED
        GPIO.output(self.record_led, GPIO.HIGH)
        # start recording
        self.record_flag.set()

    def stop_recording(self):
        self.record_flag.clear()
        # turn off record LED
        GPIO.output(self.record_led, GPIO.LOW)

    def push_record(self,pin = 0):
        # pin should be integer value of self.record_button
        if self.state in ['IDLE','PLAYBACK']:
            self.state = 'RECORDING'
            self.start_recording()

        elif self.state == 'RECORDING':
            self.stop_recording()
            self.state = 'PLAYBACK'
            self.n_loop += 1
            loop_filename = self.loop_filename.format(self.n_loop)
            shutil.copyfile(self.temp_recording_filename, loop_filename)
            loop_on_flag = threading.Event()
            self.loop_on_flags += [loop_on_flag]
            t_repetition = 4*60/self.bpm # duration of loop
            time.sleep(self.timing_precision)
            loop_thread = threading.Thread(
                name = self.loop_filename.format(self.n_loop),
                target = player,
                args=(loop_on_flag,
                    t_repetition,
                    self.start_time,
                    self.timing_precision,
                    loop_filename),
                daemon = True)
            loop_thread.start()
            loop_on_flag.set()
            self.metronome_on_flag.clear()


if __name__ == "__main__":
    l = Looper()
    time.sleep(1)
    l.metronome_on()
    t_repetition = 60/l.bpm
    n = int((time.time()-l.start_time)/t_repetition)# beats so far
    while l.start_time+(n+1)*t_repetition<time.time():
        time.sleep(self.timing_precision)
    t_start = time.time()
    l.start_recording()
    time.sleep(t_repetition)
    l.stop_recording()
    time.sleep(l.timing_precision)
    calibration_filename = l.recording_directory+'latency_measurement'
    shutil.copyfile(l.temp_recording_filename, calibration_filename)
    time.sleep(l.timing_precision)
    sound, sr = sf.read(calibration_filename)
    plt.plot(np.linspace(0,t_repetition,len(sound)),np.absolute(sound))
    plt.show()
    # GPIO.cleanup() # erase all predefined behavior of GPIO ports