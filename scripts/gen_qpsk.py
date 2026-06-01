#!/usr/bin/env python3
# Reference QPSK signal for validating src/qpsk.rail's carrier recovery. Random Gray
# QPSK symbols (1 sample/symbol, no pulse shaping — isolates CARRIER recovery), rotated
# by an unknown carrier frequency offset + phase, plus AWGN. int8 IQ -> /tmp/qpsk_in.s8.
import numpy as np, sys
rng=np.random.default_rng(5)
N = int(sys.argv[sys.argv.index('--n')+1]) if '--n' in sys.argv else 4000
foff = float(sys.argv[sys.argv.index('--foff')+1]) if '--foff' in sys.argv else 0.004  # cyc/sample
snr = float(sys.argv[sys.argv.index('--snr')+1]) if '--snr' in sys.argv else 12.0
bits=rng.integers(0,2,2*N)
I=np.where(bits[0::2]==0,1.0,-1.0); Q=np.where(bits[1::2]==0,1.0,-1.0)
sym=(I+1j*Q)/np.sqrt(2)
n=np.arange(N); phi=0.7
rx=sym*np.exp(1j*(2*np.pi*foff*n+phi))
sigma=(1.0/np.sqrt(2))/(10**(snr/20))
rx=rx+(rng.standard_normal(N)+1j*rng.standard_normal(N))*sigma
A=90.0
i8=np.clip(np.round(rx.real*A),-127,127).astype(np.int8)
q8=np.clip(np.round(rx.imag*A),-127,127).astype(np.int8)
iq=np.empty(2*N,np.int8); iq[0::2]=i8; iq[1::2]=q8; iq.tofile('/tmp/qpsk_in.s8')
np.save('/tmp/qpsk_truth.npy', bits.astype(np.uint8))
print(f'{N} QPSK symbols, foff={foff} cyc/samp, phi={phi}, snr={snr}dB sigma={sigma:.3f}')
