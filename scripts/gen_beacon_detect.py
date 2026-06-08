#!/usr/bin/env python3
# Rung 1 generator: synthetic narrowband BEACON in a wide IQ capture for
# src/beacon_detect.rail. A CubeSat beacon at ~137-138 MHz is a narrowband
# carrier (CW or low-rate FSK) sitting in a baseband that the SDR captures at
# some center freq fc with sample rate fs. We place a pure tone at a known
# OFFSET from center, add wideband noise, and write int8 IQ -> /tmp/beacon_detect_in.s8.
# Ground truth (center offset Hz, absolute MHz, in-band SNR) -> /tmp/beacon_detect_truth.npy.
#
# This is a SYNTHETIC TEST VECTOR. It is not a real satellite reception.
import numpy as np, sys, math

def argf(name, default):
    return float(sys.argv[sys.argv.index(name)+1]) if name in sys.argv else default
def argi(name, default):
    return int(sys.argv[sys.argv.index(name)+1]) if name in sys.argv else default

fc   = argf('--fc', 137_500_000.0)   # SDR center freq (Hz)
fs   = argf('--fs', 240_000.0)       # sample rate (Hz)  -> +/-120 kHz span
foff = argf('--foff', 37_500.0)      # beacon offset from center (Hz) -> 137.5375 MHz
snr  = argf('--snr', 12.0)           # in-band SNR (dB)
nfft = argi('--nfft', 4096)
nfr  = argi('--frames', 16)
rng  = np.random.default_rng(137)

N = nfft * nfr
t = np.arange(N) / fs
# pure complex tone at the offset (a CW beacon carrier)
amp = 1.0
tone = amp * np.exp(1j * 2*np.pi*foff*t)
# wideband complex AWGN across the whole captured span.
# in-band SNR is referenced to one FFT bin's noise: scale so that the tone power
# vs per-bin noise floor hits the requested SNR.
bin_noise = (amp**2) / (10**(snr/10)) * (1.0/nfft)   # noise power that lands in tone bin
sigma = math.sqrt(bin_noise * nfft / 2.0)            # total complex noise std per component
noise = (rng.standard_normal(N) + 1j*rng.standard_normal(N)) * sigma
z = tone + noise

A = 70.0
i8 = np.clip(np.round(z.real*A), -127, 127).astype(np.int8)
q8 = np.clip(np.round(z.imag*A), -127, 127).astype(np.int8)
iq = np.empty(2*N, np.int8); iq[0::2]=i8; iq[1::2]=q8
iq.tofile('/tmp/beacon_detect_in.s8')

f_abs = (fc + foff) / 1e6

# Expected POST-DETECTION SNR (dB): replicate the detector's measurement on the
# quantized int8 samples so ground truth matches what beacon_detect.rail reports.
# Welch-average |FFT|^2 of Hann-windowed signed-int8 frames, peak-bin vs mean off-peak.
ii = i8.astype(np.float64); qq = q8.astype(np.float64)
zc = ii + 1j*qq
nfr_have = len(zc)//nfft
w = 0.5 - 0.5*np.cos(2*np.pi*np.arange(nfft)/(nfft-1))   # Hann
acc = np.zeros(nfft)
for fidx in range(nfr_have):
    fr = zc[fidx*nfft:(fidx+1)*nfft]*w
    acc += np.abs(np.fft.fft(fr))**2
acc = np.fft.fftshift(acc)
# DC guard +/-6, edges 8
half = nfft//2
acc[half-6:half+7] = 0.0
mask = np.ones(nfft, bool); mask[:8]=False; mask[-8:]=False
peakm = half-6 + np.argmax(acc[half-6:half+7+1]) if acc[half-6:half+7+1].max()>0 else 0
peakm = int(np.argmax(acc))
pkpow = acc[peakm]
nmask = mask.copy()
nmask[half-6:half+7] = False
nmask[max(0,peakm-3):peakm+4] = False
nfloor = acc[nmask].mean()
det_snr_db = 10*np.log10(pkpow/nfloor)

np.save('/tmp/beacon_detect_truth.npy',
        np.array([foff, f_abs, det_snr_db, fc, fs, nfft, snr], dtype=np.float64))
print(f'wrote {N} IQ samples, beacon offset={foff:.1f}Hz -> {f_abs:.6f}MHz, '
      f'in-band(per-bin) SNR={snr}dB, expected detector SNR={det_snr_db:.1f}dB, '
      f'fs={fs}, nfft={nfft} -> /tmp/beacon_detect_in.s8')
