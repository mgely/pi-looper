#!/usr/bin/env python3
import argparse
import logging
import tempfile
import queue
import sys
import time
import sounddevice as sd
import soundfile as sf
import numpy as np # Make sure NumPy is loaded before it is used in the callback
assert np  # avoid "imported but unused" message (W0611)

'''
Play settings are those of
https://python-sounddevice.readthedocs.io/en/0.3.11/#sounddevice.OutputStream
except channels, dtype, callback and finished_callback
'''

device_info = sd.query_devices(None, 'input')
samplerate = int(device_info['default_samplerate'])


def metronome(metronome_on_flag, bpm, start_time, timing_precision, filename):

    seconds_between_beats = 60./float(bpm)

    # Extract data and sampling rate from file
    metronome_sound, metronome_sr = sf.read(filename)
    logging.debug('metronome samplerate '+str(metronome_sr))
    # Normalize the metronome sound
    metronome_sound /= np.amax(metronome_sound)

    total_n_beats = 0
    while True:
        if start_time+total_n_beats*seconds_between_beats<time.time():
            total_n_beats += 1
            if metronome_on_flag.isSet():
                sd.play(metronome_sound,
                    samplerate=metronome_sr)
                # sd.wait()
                # logging.debug('bip')
        time.sleep(timing_precision)


def recorder(recording_flag, timing_precision, filename = '', directory = ''):
    """
    adapted from
    https://github.com/spatialaudio/python-sounddevice/blob/master/examples/rec_unlimited.py
    """

    recording_file = tempfile.mktemp(prefix=filename,
                                        suffix='.wav', dir=directory)

    q = queue.Queue()

    def callback(indata, frames, time, status):
        """This is called (from a separate thread) for each audio block."""
        if status:
            print(status, file=sys.stderr)
        q.put(indata.copy())

    # Make sure the file is opened before recording anything:
    with sf.SoundFile(recording_file, mode='x',channels = 1, samplerate=samplerate) as file:
        with sd.InputStream(samplerate=samplerate,channels = 1,callback=callback):
            logging.debug('temporary file name: '+file.name)
            while True:
                file.truncate(1) # Deletes contents of the file
                with q.mutex:
                    q.queue.clear() # Deletes content of the q object
                while recording_flag.isSet():
                    file.write(q.get()) # Adds audio to the file
                file.flush() # Add any unwritten audio to the file
                logging.debug('not recording')
                while not recording_flag.isSet():
                    time.sleep(timing_precision)
                logging.debug('recording')