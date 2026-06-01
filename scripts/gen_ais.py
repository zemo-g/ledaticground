#!/usr/bin/env python3
# Synthetic AIS-style GMSK signal to validate src/gmsk.rail. AIS = MSK, 9600 baud,
# deviation = baud/4 = 2400 Hz, Gaussian BT=0.4. Generates from known bits (raw bits,
# NRZI is a later rung), int8 IQ -> /tmp/ais_gmsk.s8, truth bits -> /tmp/ais_truth.npy.
import numpy as np, sys
fs=96000; baud=9600; sps=fs//baud; dev=baud/4
N=int(sys.argv[sys.argv.index('--n')+1]) if '--n' in sys.argv else 2000
snr=float(sys.argv[sys.argv.index('--snr')+1]) if '--snr' in sys.argv else 20.0
rng=np.random.default_rng(13)
bits=rng.integers(0,2,N)
nrz=2*bits-1.0
up=np.repeat(nrz,sps)
# Gaussian pulse shaping (BT=0.4)
BT=0.4; span=4
t=np.arange(-span*sps,span*sps+1)/sps
import math
sigma=math.sqrt(math.log(2))/(2*math.pi*BT)
g=np.exp(-t**2/(2*sigma**2)); g/=g.sum()
shaped=np.convolve(up,g,'same')
# FM (MSK) modulate: phase = cumulative freq
ph=np.cumsum(shaped*2*np.pi*dev/fs)
z=np.exp(1j*ph)
sig=(1.0/np.sqrt(2))/(10**(snr/20))
z=z+(rng.standard_normal(len(z))+1j*rng.standard_normal(len(z)))*sig
A=90.0
i8=np.clip(np.round(z.real*A),-127,127).astype(np.int8)
q8=np.clip(np.round(z.imag*A),-127,127).astype(np.int8)
iq=np.empty(2*len(z),np.int8); iq[0::2]=i8; iq[1::2]=q8; iq.tofile('/tmp/ais_gmsk.s8')
np.save('/tmp/ais_truth.npy', bits.astype(np.uint8))
np.save('/tmp/ais_sps.npy', np.array([sps]))
print(f'{N} bits, sps={sps}, baud={baud}, dev={dev}, snr={snr}dB -> /tmp/ais_gmsk.s8')
