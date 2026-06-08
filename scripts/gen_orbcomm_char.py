#!/usr/bin/env python3
# Reference vector for src/orbcomm_char.rail -- the Orbcomm HONEST CHARACTERIZATION rung.
#
# THE WHOLE POINT OF THIS RUNG (the honesty fallback):
#   When the protocol above the link layer is NOT publicly specified (Orbcomm user data is a
#   commercial, partly-encrypted system with no public message dictionary), a receiver must
#   NOT invent payload content. Instead it reports only what it can physically MEASURE from
#   the off-air IQ: that a carrier is present, the carrier offset, the symbol rate, the packet
#   timing (burst start/length/gap), and the SNR. NO fabricated bits, NO invented message
#   fields. That is the difference between honest characterization and made-up "decode".
#
# PUBLIC-KNOWLEDGE BASIS: Orbcomm subscriber/gateway downlink lives at 137.2-137.8 MHz,
#   ~25 kHz channels, SD-PSK ~4800 sym/s (FCC filings + SDR reverse-engineering). The PHY
#   class + symbol rate are public; the user-message payload is not, and is not invented here.
#
# This generator builds a SYNTHETIC SD-PSK burst (random bits drive a differential BPSK
# modulator; the bits themselves are irrelevant -- they are NOT the thing being tested) at a
# known carrier offset, oversampled so the symbol rate is genuinely MEASURABLE from the IQ,
# with leading + trailing silence (the inter-burst gap) so packet timing is measurable too.
#
# The Rail module reads the IQ and MEASURES (it never sees these ground-truth numbers):
#   - carrier present?            (in-burst SNR above a detection floor)
#   - carrier offset (Hz)         (squared-symbol differential-product angle / 4pi)
#   - symbol rate (sym/s)         (autocorrelation peak of the per-sample transition energy;
#                                  the symbol-boundary periodicity reveals samples/symbol)
#   - packet timing (samples)     (6x-noise-floor power thresholding -> start/len/gap)
#   - SNR (dB)                    (in-burst mean power over the noise floor)
#
# int8 IQ -> /tmp/orbcomm_char_in.s8 ; ground-truth observables -> .npy
import numpy as np, sys

rng = np.random.default_rng(29)

# ---- known channel/PHY observables (public class; offset/SNR are the synthetic test truth) ----
FS       = 38400.0     # sample rate (Hz) -> 8 samples/symbol at 4800 sym/s
SPS      = 8           # samples per symbol
SYM_RATE = FS / SPS    # = 4800 sym/s (the documented Orbcomm symbol rate)
FOFF_HZ  = float(sys.argv[sys.argv.index('--foff')+1]) if '--foff' in sys.argv else 750.0
SNR_DB   = float(sys.argv[sys.argv.index('--snr')+1])  if '--snr'  in sys.argv else 14.0
NSYM     = int(sys.argv[sys.argv.index('--nsym')+1])   if '--nsym' in sys.argv else 220
GAP      = 256         # leading + trailing silence (samples) -> packet-timing measurable

# Random bits drive a differential BPSK modulator. The bits are NOT the deliverable; this
# rung deliberately does NOT recover them. They only give the carrier realistic structure.
bits = rng.integers(0, 2, NSYM).tolist()
phase = 0.0
syms = []
for b in bits:
    phase += (np.pi if b == 1 else 0.0)
    syms.append(np.exp(1j*phase))
syms = np.repeat(np.array(syms), SPS)              # oversample -> symbol periodicity in IQ

burst = np.concatenate([np.zeros(GAP, complex), syms, np.zeros(GAP, complex)])
n = np.arange(len(burst))
burst = burst * np.exp(1j*(2*np.pi*(FOFF_HZ/FS)*n + 0.3))

sigma = (1.0/np.sqrt(2)) / (10**(SNR_DB/20))
burst = burst + (rng.standard_normal(len(burst)) + 1j*rng.standard_normal(len(burst)))*sigma

A = 80.0
i8 = np.clip(np.round(burst.real*A), -127, 127).astype(np.int8)
q8 = np.clip(np.round(burst.imag*A), -127, 127).astype(np.int8)
iq = np.empty(2*len(burst), np.int8); iq[0::2] = i8; iq[1::2] = q8
iq.tofile('/tmp/orbcomm_char_in.s8')

true_burst_start = GAP
true_burst_len   = len(syms)        # = NSYM * SPS
# ground-truth observables (FLOAT array): the Rail module must MEASURE these, not be told them
np.save('/tmp/orbcomm_char_truth.npy', np.array([
    FS, SYM_RATE, FOFF_HZ, SNR_DB, SPS,
    true_burst_start, true_burst_len, GAP, NSYM], np.float64))

print(f'char burst: FS={FS} SPS={SPS} sym_rate={SYM_RATE} foff={FOFF_HZ}Hz '
      f'snr={SNR_DB}dB nsym={NSYM} burst_start={true_burst_start} burst_len={true_burst_len} gap={GAP}')
print('NOTE: no payload ground-truth is emitted -- this rung characterizes, it does not decode.')
