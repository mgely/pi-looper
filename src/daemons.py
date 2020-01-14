#!/usr/bin/env python3
import argparse
import logging
import tempfile
import queue
import sys
import os
import time
import sounddevice as sd
import soundfile as sf
import numpy as np # Make sure NumPy is loaded before it is used in the callback
assert np  # avoid "imported but unused" message (W0611)
sample_rate = 44100

def recorder(recording_flag, timing_precision, filename):
    """
    adapted from
    https://github.com/spatialaudio/python-sounddevice/blob/master/examples/rec_unlimited.py
    """

    try:
        os.remove(filename)
    except FileNotFoundError:
        pass

    q = queue.Queue()

    def callback(indata, frames, time, status):
        """This is called (from a separate thread) for each audio block."""
        if status:
            print(status, file=sys.stderr)
        q.put(indata.copy())

    with sf.SoundFile(filename, mode='x',channels = 2, samplerate=sample_rate) as file:
        with sd.InputStream(samplerate=sample_rate,channels = 2,callback=callback, latency = 0.05, dtype='float32'):
            logging.debug('temporary file name: '+file.name)
            while True:
                file.truncate(1) # Deletes contents of the file
                with q.mutex:
                    q.queue.clear() # Deletes content of the q object
                while recording_flag.isSet():
                    file.write(q.get()) # Adds audio to the file
                file.flush() # Add any unwritten audio to the file
                # logging.debug('not recording')
                while not recording_flag.isSet():
                    time.sleep(timing_precision)
                # logging.debug('recording')