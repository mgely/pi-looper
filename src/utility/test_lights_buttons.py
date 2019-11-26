from gpiozero import LED, Button
from time import sleep

# back rec forw play
GPIO_leds = [14,18,24,8]
leds = []
GPIO_buttons = [15,23,25,7]
buttons = []

for i in range(4):
    leds.append(LED(GPIO_leds[i]))
    buttons.append(Button(GPIO_buttons[i]))
    buttons[i].when_deactivated = leds[i].toggle
while True:
    sleep(1)