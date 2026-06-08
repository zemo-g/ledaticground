#!/usr/bin/env python3
# Synthetic ACARS MSK audio for src/acars_msk.rail (rung 2: bit recovery + bit sync).
# This is the DC-removed envelope audio that rung 1 produces: a 2400 bps MSK waveform,
# bit 1 -> 1200 Hz tone, bit 0 -> 2400 Hz tone (MSK h=0.5). We emit it as mono s16 LE
# @48 kHz to /tmp/acars_msk.s16 (matching the repo's .s16 convention) with a random
# sub-sample timing offset + a pre-key, plus the truth bits for the BER check.
import numpy as np, sys
fs, baud = 48000, 2400
sps = fs // baud
def argf(f,d): return float(sys.argv[sys.argv.index(f)+1]) if f in sys.argv else d
def argi(f,d): return int(sys.argv[sys.argv.index(f)+1]) if f in sys.argv else d
N   = argi('--n', 256)
snr = argf('--snr', 14.0)
off = argi('--off', 7)          # sub-bit timing offset in samples (clock-recovery test)
rng = np.random.default_rng(21)

bits = rng.integers(0, 2, N).astype(np.uint8)
f1, f0 = 1200.0, 2400.0
inst = np.where(np.repeat(bits, sps) == 1, f1, f0).astype(np.float64)
ph   = 2*np.pi*np.cumsum(inst)/fs
msk  = np.cos(ph)

pre = np.cos(2*np.pi*1800.0*np.arange(3*sps)/fs)   # pre-key-ish carrier-tone settling
sig = np.concatenate([pre[:off], msk])             # inject the timing offset at the head

p   = np.mean(sig**2); npow = p/(10**(snr/10))
sig = sig + rng.standard_normal(len(sig))*np.sqrt(npow)

s16 = np.clip(np.round(sig*12000.0), -32767, 32767).astype('<i2')
s16.tofile('/tmp/acars_msk.s16')
np.save('/tmp/acars_msk_truth.npy', bits)
np.save('/tmp/acars_msk_meta.npy', np.array([fs, baud, sps, off, N]))
print(f'{N} bits, sps={sps}, off={off}, snr={snr}dB -> /tmp/acars_msk.s16 ({len(sig)} samples)')
