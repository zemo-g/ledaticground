#!/usr/bin/env python3
# Rung 2 generator: synthetic FSK/AFSK test vectors for src/beacon_fsk.rail.
#
#   --mode afsk1200 : Bell-202 AFSK. mark=1200Hz space=2200Hz audio tones at 1200 baud,
#                     written as mono s16 -> /tmp/beacon_fsk_in.s16  (audio path; the
#                     beacon's FM-demod output before bit slicing).
#   --mode fsk9600  : direct 2-FSK at 9600 baud, deviation +/-3600 Hz, complex IQ at
#                     96 kHz, written as int8 IQ -> /tmp/beacon_fsk_in.s8.
#
# Truth bits -> /tmp/beacon_fsk_truth.npy ; params -> /tmp/beacon_fsk_params.npy.
# SYNTHETIC TEST VECTOR — not a real beacon reception.
import numpy as np, sys, math

def argf(n,d): return float(sys.argv[sys.argv.index(n)+1]) if n in sys.argv else d
def argi(n,d): return int(sys.argv[sys.argv.index(n)+1]) if n in sys.argv else d
def args(n,d): return sys.argv[sys.argv.index(n)+1] if n in sys.argv else d

mode = args('--mode', 'afsk1200')
N    = argi('--n', 256)
snr  = argf('--snr', 18.0)
rng  = np.random.default_rng(202)
bits = rng.integers(0,2,N).astype(np.uint8)

if mode == 'afsk1200':
    fs=48000; baud=1200; sps=fs//baud
    mark=1200.0; space=2200.0
    # continuous-phase AFSK: phase advances at mark/space per sample
    ph=0.0; samp=[]
    for b in bits:
        f = mark if b==1 else space
        for _ in range(sps):
            ph += 2*np.pi*f/fs
            samp.append(np.sin(ph))
    x=np.array(samp)
    sig=1.0/np.sqrt(2)/(10**(snr/20))
    x=x+rng.standard_normal(len(x))*sig
    s16=np.clip(np.round(x/np.abs(x).max()*20000),-32767,32767).astype('<i2')
    s16.tofile('/tmp/beacon_fsk_in.s16')
    np.save('/tmp/beacon_fsk_truth.npy', bits)
    # params: [mode_id, fs, baud, sps, mark, space]
    np.save('/tmp/beacon_fsk_params.npy', np.array([0,fs,baud,sps,mark,space],dtype=np.float64))
    open('/tmp/beacon_fsk_mode.txt','w').write('0\n')
    open('/tmp/beacon_fsk_sps.txt','w').write(f'{sps}\n')
    print(f'AFSK1200: {N} bits, fs={fs}, baud={baud}, sps={sps}, mark={mark}, space={space}, '
          f'snr={snr}dB -> /tmp/beacon_fsk_in.s16')
else:
    fs=96000; baud=9600; sps=fs//baud; dev=3600.0
    nrz=2*bits-1.0
    up=np.repeat(nrz,sps)
    ph=np.cumsum(up*2*np.pi*dev/fs)
    z=np.exp(1j*ph)
    sig=1.0/np.sqrt(2)/(10**(snr/20))
    z=z+(rng.standard_normal(len(z))+1j*rng.standard_normal(len(z)))*sig
    A=90.0
    i8=np.clip(np.round(z.real*A),-127,127).astype(np.int8)
    q8=np.clip(np.round(z.imag*A),-127,127).astype(np.int8)
    iq=np.empty(2*len(z),np.int8); iq[0::2]=i8; iq[1::2]=q8
    iq.tofile('/tmp/beacon_fsk_in.s8')
    np.save('/tmp/beacon_fsk_truth.npy', bits)
    np.save('/tmp/beacon_fsk_params.npy', np.array([1,fs,baud,sps,dev,0],dtype=np.float64))
    open('/tmp/beacon_fsk_mode.txt','w').write('1\n')
    open('/tmp/beacon_fsk_sps.txt','w').write(f'{sps}\n')
    print(f'FSK9600: {N} bits, fs={fs}, baud={baud}, sps={sps}, dev={dev}, '
          f'snr={snr}dB -> /tmp/beacon_fsk_in.s8')
