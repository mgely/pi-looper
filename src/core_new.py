#####################
# NEXT: use python scheduler for example to call self.one() at each start of a bar
# which can also trigger some states
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

    def __init__(self):

        self.rec_led = LED(18)
        self.play_led = LED(8)

        self.machine = Machine(model = self,states = self.states,initial='rec')
        self.machine.add_transition('press_rec', 'play', 'rec')
        self.machine.add_transition('press_play', 'rec', 'play')
    
    def on_enter_play(self):
        self.play_led.on()
        self.rec_led.off()
        
    def on_enter_rec(self):
        self.play_led.off()
        self.rec_led.on()
        self.machine.add_transition('bar_started', 'rec', 'play')


if __name__ == "__main__":
    l = Looper()
    rec_button = Button(23)
    play_button = Button(7)


    rec_button.when_deactivated = l.press_rec
    play_button.when_deactivated = l.press_play

    
    # play_button.
    while True:
        sleep(1)