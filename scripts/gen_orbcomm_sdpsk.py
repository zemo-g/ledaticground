#!/usr/bin/env python3
# Reference synthetic vector for src/orbcomm_sdpsk.rail — Orbcomm-style Symmetric Differential
# PSK (SD-PSK) demod rung at ~4800 sym/s.
#
# PUBLIC-KNOWLEDGE BASIS (explicit honesty boundary):
#   Orbcomm subscriber/gateway DOWNLINK lives at 137.2-137.8 MHz, ~25 kHz channels, and uses
#   Symmetric Differential PSK at ~4800 sym/s. The PHYSICAL layer (differential PSK, a
#   recognizable preamble/unique-word, a self-sync scrambler) is documented in old FCC filings
#   + SDR reverse-engineering. The user-message PAYLOAD above the link layer is PROPRIETARY and
#   not publicly specified, so this rung decodes the *bits*, not message semantics. The unique
#   word here is an illustrative synthetic stand-in, NOT the real proprietary value.
#
# SD-PSK == differential BPSK in the symmetric (antipodal) convention: the BIT is carried in
# the phase TRANSITION between consecutive symbols (0 -> no phase change, 1 -> pi flip). A
# differential-coherent receiver recovers bits from sign(Re(z[k] * conj(z[k-1]))), which is
# invariant to the absolute carrier phase (the whole point of differential PSK). We add a small
# residual carrier freq offset + AWGN; the differential demod tolerates the offset with no PLL.
#
# Frame: PREAMBLE (alternating ...0101...) + 16-bit UNIQUE WORD + DATA bits. The Rail demod
# correlates against the unique word to find the frame start (frame sync). 1 sample/symbol
# (carrier/timing recovery is isolated to the differential demod; an upstream symbol-timing
# rung would supply symbol-spaced samples in a real receiver).
#
# int8 IQ -> /tmp/orbcomm_in.s8 ; ground truth (decoded bits, UW, data, UW offset) -> .npy
import numpy as np, sys

rng = np.random.default_rng(13)
NDATA = int(sys.argv[sys.argv.index('--n')+1])      if '--n'    in sys.argv else 512
foff  = float(sys.argv[sys.argv.index('--foff')+1]) if '--foff' in sys.argv else 0.0010  # cyc/sym
snr   = float(sys.argv[sys.argv.index('--snr')+1])  if '--snr'  in sys.argv else 12.0

# 16-bit unique word (illustrative synthetic sync pattern, good autocorrelation; NOT the real
# proprietary Orbcomm UW). MUST match set_uw in src/orbcomm_sdpsk.rail.
UW  = [1,0,1,1,0,0,1,0,0,1,1,1,0,0,0,1]
PRE = ([0,1] * 24)                                   # 48-symbol alternating preamble
data_bits = rng.integers(0, 2, NDATA).tolist()

# full transmitted bit sequence that DRIVES the differential modulator
tx_bits = PRE + UW + data_bits
nbits = len(tx_bits)

# Differential BPSK modulate: bit=1 flips the phase by pi; bit=0 keeps it.
# Start reference symbol at phase 0. symbol value s[k] in {+1,-1} (BPSK on I).
phase = 0.0
syms = []
for b in tx_bits:
    phase += (np.pi if b == 1 else 0.0)
    syms.append(np.exp(1j*phase))
syms = np.array(syms)

# apply a residual carrier frequency offset + an unknown absolute phase (phase-blind demod)
n = np.arange(len(syms))
phi0 = 0.9
rx = syms * np.exp(1j*(2*np.pi*foff*n + phi0))

# AWGN at the requested SNR (signal power = 1)
sigma = (1.0/np.sqrt(2)) / (10**(snr/20))
rx = rx + (rng.standard_normal(len(rx)) + 1j*rng.standard_normal(len(rx))) * sigma

A = 90.0
i8 = np.clip(np.round(rx.real*A), -127, 127).astype(np.int8)
q8 = np.clip(np.round(rx.imag*A), -127, 127).astype(np.int8)
iq = np.empty(2*len(rx), np.int8); iq[0::2] = i8; iq[1::2] = q8
iq.tofile('/tmp/orbcomm_in.s8')

# Differentially-decoded bit stream the Rail demod should produce:
#   diff_bit[k] = tx_bits[k] for k>=1 (since the bit drives the phase TRANSITION). The
#   reference symbol (k=0) carries no decoded bit, so decoded length = nbits-1.
decoded_truth = np.array(tx_bits[1:], dtype=np.uint8)
np.save('/tmp/orbcomm_sdpsk_truth.npy', decoded_truth)
np.save('/tmp/orbcomm_sdpsk_uw.npy', np.array(UW, dtype=np.uint8))
np.save('/tmp/orbcomm_sdpsk_databits.npy', np.array(data_bits, dtype=np.uint8))
# UW START in the DECODED stream: decoded[k] corresponds to tx_bits[k+1].
# tx_bits = PRE(48) + UW(16) + DATA. UW starts at tx index 48 -> decoded index 47.
uw_decoded_start = len(PRE) - 1
np.save('/tmp/orbcomm_sdpsk_uw_start.npy', np.array([uw_decoded_start], dtype=np.int64))
print(f'{nbits} tx bits ({len(PRE)} preamble + {len(UW)} UW + {NDATA} data), '
      f'foff={foff} cyc/sym snr={snr}dB sigma={sigma:.3f}; UW@decoded[{uw_decoded_start}]')
