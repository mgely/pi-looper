#####################
# NEXT: 
#
# Use python scheduler for example to call self.one() at each start of a bar
# which can also trigger for example the start of rec. 
# The end of rec should be dealt with internally with a scheduled self.end_of_rec() event
# 
#  
#####################


from gpiozero import LED, Button
from time import sleep
from threading import Timer
from transitions import Machine, State

# Set up logging; The basic log level will be DEBUG
import logging
logging.basicConfig(level=logging.DEBUG)
# Set transitions' log level to INFO; DEBUG messages will be omitted
logging.getLogger('transitions').setLevel(logging.INFO)

# back rec forw play
GPIO_leds = [14,18,24,8]
GPIO_buttons = [15,23,25,7]

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
        self.machine = Machine(model = self,states = self.states, transitions = self.transitions, 
            initial = 'play')
        self.scheduled_events = []
        self.init_hardware()
        
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
    
    def start_recording(self):
        print('TODO: START RECORDING')
        self.trigger('start_recording')

    def end_recording(self):
        print('TODO: ADD RECORDING TO LOOPS')
        self.trigger('end_recording')

    def all_leds_off(self):
        for l in [self.rec_led,self.play_led]:
            l.off()

    def cancel_all_scheduled_events(self):
        for i in range(len(self.scheduled_events)):
            self.scheduled_events[i].cancel()
        self.scheduled_events = []

    def on_enter(self):
        self.all_leds_off()
        self.cancel_all_scheduled_events()

    def on_enter_play(self):
        self.on_enter()
        self.play_led.on()
        print('TODO: STOP ALL RECORDINGS')
        
    def on_enter_rec(self):
        self.on_enter()
        self.rec_led.on()
        
    def on_enter_pre_play(self):
        self.on_enter()
        self.play_led.blink()

        event = Timer(2,self.end_recording)
        event.start()
        self.scheduled_events.append(event)

        
    def on_enter_pre_rec(self):
        self.on_enter()
        self.rec_led.blink()
        
        event = Timer(2,self.start_recording)
        event.start()
        self.scheduled_events.append(event)


if __name__ == "__main__":
    l = Looper()    
    while True:
        sleep(1)