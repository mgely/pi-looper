import RPi.GPIO as GPIO
import sounddevice as sd
import soundfile as sf
from daemons import recorder,metronome
import threading
import os
import logging
from datetime import datetime
import time

logging.basicConfig(level=logging.DEBUG,
                    format='(%(threadName)-10s) %(message)s',
                    )

class Looper:
    def __init__(self):

        # setup timing
        self.bpm = 500
        self.start_time = time.time()
        self.timing_precision = 0.3e-3 # half a milisecond

        self.repo_directory = '/home/pi/Desktop/pi-looper/'
        self.src_directory = '/home/pi/Desktop/pi-looper/src/'

        self.temp_recording_filename = 'test.wav'
        self.recording_directory = '/home/pi/Desktop/pi-looper-data/'
        self.recording_directory += datetime.fromtimestamp(time.time()).strftime('%Y-%m-%d__%H-%M-%S')
        os.mkdir(self.recording_directory)

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
                      self.temp_recording_filename,
                      self.recording_directory,),
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

    def metronome_on(self):
        self.metronome_on_flag.set()

    def metronome_off():
        self.metronome_on_flag.clear()


    def push_record(self,pin):
        # pin should be integer value of self.record_button
        if self.state in ['IDLE','PLAYBACK']:
            self.state = 'RECORDING'
            # turn on record LED
            GPIO.output(self.record_led, GPIO.HIGH)
            # start recording
            self.record_flag.set()

        elif self.state == 'RECORDING':
            # stop recording
            self.record_flag.clear()
            # turn off record LED
            GPIO.output(self.record_led, GPIO.LOW)
            self.state = 'PLAYBACK'


if __name__ == "__main__":
    Looper()
    # GPIO.cleanup() # erase all predefined behavior of GPIO ports