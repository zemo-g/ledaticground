#!/usr/bin/env python3
# ===========================================================================
# gen.py for src/lrpt_demod_impaired.rail — the IMPAIRED QPSK front end.
#
# This is the LAST synthetic rung before a real Meteor-M2 recording. It folds
# RRC matched filtering + Gardner symbol-timing recovery + Costas carrier
# recovery into one Rail module, and validates that the joint loop LOCKS and
# recovers symbols on a signal carrying ALL the real-channel impairments at
# once:
#
#   * carrier frequency offset + unknown phase     (LO mismatch / residual Doppler)
#   * symbol-CLOCK drift                            (sample-clock offset, ppm-style)
#   * additive white Gaussian noise                 (thermal / link budget)
#   * RRC pulse shaping at oversampling > 2 sps      (real Meteor matched-filter front)
#
# The Rail module must recover the soft (I,Q) symbols where a NAIVE max-power
# decimator (pick the best fixed phase, decimate, no tracking) fails — that
# contrast is the proof the carrier+timing loops are doing real work.
#
# OUTPUTS (all read directly by the Rail; no JSON parser in Rail):
#   /tmp/lrpt_demod_impaired_in.s8   — int8 interleaved IQ at ~2 sps (impaired)
#   /tmp/lrpt_demod_impaired_truth.s8 — 1 byte/bit, the 2N tx hard bits (I,Q...)
#                                       so Rail can compute SER over a known seq.
#   /tmp/lrpt_demod_impaired_meta.s8  — small int header [nsym_lo, nsym_hi]
#
# HONESTY: SYNTHETIC. numpy-generated impaired baseband with REAL Meteor LRPT
# structure (QPSK, RRC roll-off, 2 sps target). NOT a real decode — a real
# Meteor-M2 capture is the remaining step (Meteor is not in the pass predictor
# yet). Numbers reported by the Rail (lock state, SER, %-correct) are TRUE.
# ===========================================================================
import numpy as np
import sys

rng = np.random.default_rng(20260607)

# ---- params (overridable on the CLI) --------------------------------------
def argf(name, default):
    return float(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else default
def argi(name, default):
    return int(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else default

N    = argi('--n', 1200)          # number of QPSK symbols
OSF  = argi('--osf', 8)           # tx oversample factor (samples/symbol at tx)
BETA = argf('--beta', 0.6)        # RRC roll-off (Meteor uses ~0.6)
SPAN = argi('--span', 6)          # RRC span in symbols (each side)
FOFF = argf('--foff', 0.0035)     # carrier freq offset, cycles/sample (at 2 sps)
PHI  = argf('--phi', 0.9)         # initial carrier phase, radians
PPM  = argf('--ppm', 80.0)        # symbol-clock drift in ppm (sample-rate offset)
SNR  = argf('--snr', 14.0)        # Es/N0-ish in dB

DEC  = OSF // 2                   # decimation factor to reach 2 sps target

# ---- RRC taps (matches rrc.rail / gen_gardner.py closed form) -------------
def rrc_taps(beta, sps, span):
    M = 2 * sps * span + 1
    t = (np.arange(M) - (M - 1) / 2) / sps
    h = np.zeros(M)
    for i, ti in enumerate(t):
        if abs(ti) < 1e-8:
            h[i] = 1 - beta + 4 * beta / np.pi
        elif beta > 0 and abs(abs(4 * beta * ti) - 1) < 1e-8:
            h[i] = (beta / np.sqrt(2)) * (
                (1 + 2 / np.pi) * np.sin(np.pi / (4 * beta))
                + (1 - 2 / np.pi) * np.cos(np.pi / (4 * beta)))
        else:
            h[i] = (np.sin(np.pi * ti * (1 - beta))
                    + 4 * beta * ti * np.cos(np.pi * ti * (1 + beta))) \
                   / (np.pi * ti * (1 - (4 * beta * ti) ** 2))
    return h / np.sqrt(np.sum(h ** 2))

# ---- tx: random QPSK, Gray-mapped, normalized ------------------------------
bits = rng.integers(0, 2, 2 * N)
I = np.where(bits[0::2] == 0, 1.0, -1.0)
Q = np.where(bits[1::2] == 0, 1.0, -1.0)
sym = (I + 1j * Q) / np.sqrt(2)

# pulse-shape at OSF sps
up = np.zeros(N * OSF, complex)
up[::OSF] = sym
h = rrc_taps(BETA, OSF, SPAN)
sig = np.convolve(up, h, mode='same')

# ---- IMPAIRMENT 1: symbol-clock drift (sample-rate offset) ----------------
# Resample the tx waveform onto a clock that drifts: the receiver clock period
# is (1 + ppm*1e-6) of the tx period and ALSO slowly walks, so the symbol
# centers slide across samples over the burst (true clock drift, not a static
# offset). We build the read positions as a cumulative drift and interpolate.
M = len(sig)
ppm = PPM * 1e-6
# fractional per-sample rate error that ramps from -ppm to +ppm across the burst
# (a linear chirp of the clock => a true drift Gardner must track, not just an offset)
n = np.arange(M)
rate = 1.0 + ppm * (2.0 * n / M - 1.0)        # drifts through nominal mid-burst
read_pos = np.cumsum(rate)                     # warped sample positions
read_pos = read_pos - read_pos[0]              # start at 0
# also inject a static fractional offset so the very first symbol is off-grid
read_pos = read_pos + 0.41 * OSF
# linear-interpolate sig at the warped positions (over the original index grid)
base = np.floor(read_pos).astype(int)
frac = read_pos - base
ok = (base >= 0) & (base + 1 < M)
base = base[ok]; frac = frac[ok]
drifted = sig[base] * (1 - frac) + sig[base + 1] * frac

# ---- decimate OSF -> 2 sps -------------------------------------------------
two = drifted[::DEC]
two = two / (np.max(np.abs(two)) + 1e-9)

# ---- IMPAIRMENT 2: carrier frequency offset + phase -----------------------
k = np.arange(len(two))
two = two * np.exp(1j * (2 * np.pi * FOFF * k + PHI))

# ---- IMPAIRMENT 3: AWGN ---------------------------------------------------
es = np.mean(np.abs(two) ** 2)
sigma = np.sqrt(es / 2.0) / (10 ** (SNR / 20.0))
two = two + (rng.standard_normal(len(two)) + 1j * rng.standard_normal(len(two))) * sigma

# renormalize after noise so the int8 quantizer uses full scale
two = two / (np.max(np.abs(two)) + 1e-9)

# ---- int8 quantize, interleave --------------------------------------------
A = 100.0
i8 = np.clip(np.round(two.real * A), -127, 127).astype(np.int8)
q8 = np.clip(np.round(two.imag * A), -127, 127).astype(np.int8)
iq = np.empty(2 * len(two), np.int8)
iq[0::2] = i8
iq[1::2] = q8
iq.tofile('/tmp/lrpt_demod_impaired_in.s8')

# ---- truth: 1 byte/bit, 2N bits (I0,Q0,I1,Q1,...) -------------------------
truth = bits.astype(np.uint8)
truth.tofile('/tmp/lrpt_demod_impaired_truth.s8')

# meta header: number of tx symbols as two little-endian bytes (Rail reads ints)
meta = np.array([N & 0xFF, (N >> 8) & 0xFF], dtype=np.uint8)
meta.tofile('/tmp/lrpt_demod_impaired_meta.s8')

print(f"IMPAIRED QPSK front end vector:")
print(f"  symbols N={N}  tx_osf={OSF}  rrc_beta={BETA} span={SPAN}  -> ~2 sps")
print(f"  carrier foff={FOFF} cyc/samp  phi={PHI} rad")
print(f"  clock drift +-{PPM} ppm (linear chirp through nominal)  + 0.41 sym static offset")
print(f"  AWGN snr={SNR} dB  sigma={sigma:.4f}")
print(f"  iq samples (2 sps) = {len(two)}  -> /tmp/lrpt_demod_impaired_in.s8")
print(f"  truth bits = {len(truth)}  -> /tmp/lrpt_demod_impaired_truth.s8")
