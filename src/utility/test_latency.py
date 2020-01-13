# Note: input is plugged into output

import sys
from os import path
sys.path.append(path.dirname(path.dirname(path.abspath(__file__))))
from core import Looper
from time import sleep
import matplotlib.pyplot as plt
import numpy as np

N_tests = 2

with Looper() as l:
    sleep(3)
    data_1 = l.loop
    l.release_rec_button()
    sleep(2)
    l.release_play_button()
    sleep(2)
    data_2 = l.loop

    l.release_rec_button()
    sleep(2)
    l.release_play_button()
    sleep(2)
    data_3 = l.loops[1]


fig = plt.figure()
plt.plot(data_1[:,0])
plt.plot(data_2[:,0])
fig.savefig('/home/pi/Desktop/plot.pdf')

latency_samples = np.argmax(data_2) - np.argmax(data_1)
latency_time = latency_samples/float(l.sample_rate)
print('LATENCY 1 = %.0f ms'%(1e3*latency_time))

latency_samples = np.argmax(data_3) - np.argmax(data_2)
latency_time = latency_samples/float(l.sample_rate)
print('LATENCY 2 = %.0f ms'%(1e3*latency_time))
