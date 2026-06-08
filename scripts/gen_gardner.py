#!/usr/bin/env python3
# Reference vector for src/gardner.rail — Gardner symbol-timing recovery, the block
# between the RRC matched filter and qpsk.rail's Costas loop.
#
# Gardner TED needs 2 samples/symbol. It estimates the timing error from
#   e = (x_late.real - x_early.real)*x_mid.real + (...imag)   [decision-free, NDA]
# i.e. e[k] = Re{ (y[2k] - y[2k-2]) * conj(y[2k-1]) } using the midpoint sample.
# A loop adjusts the resampling instant (mu) to drive e->0, locking to the symbol clock.
#
# Test signal: random QPSK symbols, RRC-pulse-shaped at OSF samples/symbol, with a
# fractional sample-timing offset (so the symbol centers fall BETWEEN samples). The rail
# Gardner loop must recover one (I,Q) decision per symbol; the check tries the 4 QPSK
# rotations and reports symbol error rate. int8 IQ -> /tmp/gardner_in.s8.
import numpy as np, sys

rng = np.random.default_rng(11)
N    = int(sys.argv[sys.argv.index('--n')+1]) if '--n' in sys.argv else 400
OSF  = int(sys.argv[sys.argv.index('--osf')+1]) if '--osf' in sys.argv else 8   # tx oversample
BETA = 0.6
TAU  = float(sys.argv[sys.argv.index('--tau')+1]) if '--tau' in sys.argv else 0.37  # frac sym offset

def rrc_taps(beta, sps, span):
    M = 2*sps*span+1
    t = (np.arange(M)-(M-1)/2)/sps
    h=np.zeros(M)
    for i,ti in enumerate(t):
        if abs(ti)<1e-8: h[i]=1-beta+4*beta/np.pi
        elif beta>0 and abs(abs(4*beta*ti)-1)<1e-8:
            h[i]=(beta/np.sqrt(2))*((1+2/np.pi)*np.sin(np.pi/(4*beta))+(1-2/np.pi)*np.cos(np.pi/(4*beta)))
        else:
            h[i]=(np.sin(np.pi*ti*(1-beta))+4*beta*ti*np.cos(np.pi*ti*(1+beta)))/(np.pi*ti*(1-(4*beta*ti)**2))
    return h/np.sqrt(np.sum(h**2))

bits = rng.integers(0,2,2*N)
I = np.where(bits[0::2]==0,1.0,-1.0); Q = np.where(bits[1::2]==0,1.0,-1.0)
sym = (I+1j*Q)/np.sqrt(2)

up = np.zeros(N*OSF, complex); up[::OSF] = sym
h = rrc_taps(BETA, OSF, 6)
sig = np.convolve(up, h, mode='same')

# apply a fractional timing offset TAU (symbols) via Fourier shift, then decimate to 2 sps
shift = TAU*OSF
M=len(sig)
f=np.fft.fftfreq(M)
sig = np.fft.ifft(np.fft.fft(sig)*np.exp(-2j*np.pi*f*shift))

# decimate OSF -> 2 samples/symbol (factor OSF/2)
dec = OSF//2
two = sig[::dec]
two = two/ (np.max(np.abs(two))+1e-9)
A=90.0
i8=np.clip(np.round(two.real*A),-127,127).astype(np.int8)
q8=np.clip(np.round(two.imag*A),-127,127).astype(np.int8)
iq=np.empty(2*len(two),np.int8); iq[0::2]=i8; iq[1::2]=q8
iq.tofile('/tmp/gardner_in.s8')
np.save('/tmp/gardner_truth.npy', bits.astype(np.uint8))
print(f"{N} QPSK symbols, 2 sps in ({len(two)} cplx samples), tau={TAU} sym, beta={BETA}")
