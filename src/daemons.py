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

def recorder(recording_flag, stop_streams_flags, timing_precision, filename):
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
            while not stop_streams_flags.isSet():
                file.truncate(1) # Deletes contents of the file
                with q.mutex:
                    q.queue.clear() # Deletes content of the q object
                while recording_flag.isSet() and not stop_streams_flags.isSet():
                    file.write(q.get()) # Adds audio to the file
                file.flush() # Add any unwritten audio to the file
                # logging.debug('not recording')
                while not recording_flag.isSet() and not stop_streams_flags.isSet():
                    time.sleep(timing_precision)
                # logging.debug('recording')



blocksize = 1024 # Number of samples in each block TODO: link to timing
buffersize = 20 # Number of blocks we load the output stream with TODO: link to maximum tempo
sample_rate = 44100
blocktime = blocksize * buffersize / sample_rate


def player(playing_flag,
            file1_flag,
            stop_streams_flags,
            latency,
            temp_playing_filename):
    q = queue.Queue(maxsize=buffersize)
            

    def callback(outdata, frames, time, status):

        # assert frames == blocksize
        if status.output_underflow:
            print('Output underflow: increase blocksize?', file=sys.stderr)
            # raise sd.CallbackAbort
        # assert not status
        try:
            data = q.get_nowait()
        except queue.Empty:
            data = b''
            print('Buffer is empty: increase buffersize?', file=sys.stderr)
            # raise sd.CallbackAbort

        if len(data) < len(outdata):
            outdata[:len(data)] = data
            outdata[len(data):] = b'\x00' * (len(outdata) - len(data))
            # raise sd.CallbackStop

        else:
            outdata[:] = data

    i=0
    with sd.RawOutputStream(
        samplerate=sample_rate, blocksize=blocksize, channels=2, dtype='float32',
        latency = latency, callback=callback):
        while not stop_streams_flags.isSet():
            with sf.SoundFile(temp_playing_filename[i]) as f:
                data = f.buffer_read(blocksize, dtype = 'float32')

                while data and not stop_streams_flags.isSet():
                    data = f.buffer_read(blocksize, dtype = 'float32')
                    q.put(data, timeout=blocktime)

                    # playing flag not set:
                    # wait for play and 
                    # output silence
                    # while not playing_flag.isSet():
                    #     data = b'\x00'*blocksize * buffersize
                    #     q.put(data,timeout=blocktime)

            # File finished: play the other file
            i = (i+1)%2
            if i==1:
                file1_flag.set()
            else:
                file1_flag.clear()
