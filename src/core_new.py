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

    states = ['rec', 'play']

    transitions = [
        # trigger                       # source        # destination
        ['release_rec_button',          'play',         'rec'],
        ['release_play_button',         'rec',          'play'],
    ]

    def __init__(self):
        self.machine = Machine(model = self,states = self.states, transitions = self.transitions, 
            initial = 'initial')
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
    
    def all_leds_off(self):
        for l in [self.rec_led,self.play_led]:
            l.off()

    def on_enter(self):
        self.all_leds_off()

    def on_enter_play(self):
        self.on_enter()
        self.play_led.on()
        
    def on_enter_rec(self):
        self.on_enter()
        self.rec_led.on()


if __name__ == "__main__":
    l = Looper()    
    while True:
        sleep(1)