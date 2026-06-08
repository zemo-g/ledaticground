#!/usr/bin/env python3
# Reference vector for src/orbcomm_sync.rail — the Orbcomm "carrier/timing recovery +
# frame sync on the preamble/sync word" rung.
#
# PUBLIC-KNOWLEDGE BASIS (be explicit, do NOT fabricate proprietary constants):
#   Orbcomm subscriber/gateway DOWNLINK lives at 137.2-137.8 MHz, ~25 kHz channels, and
#   uses Symmetric Differential PSK (SD-PSK == differential BPSK in the antipodal
#   convention) at ~4800 sym/s. The PHYSICAL layer (differential PSK, a recognizable
#   preamble + unique word for sync, a small carrier offset on a Doppler-smeared LEO
#   downlink) is documented in old FCC filings + SDR reverse-engineering. The exact
#   proprietary unique-word VALUE and the user-payload SEMANTICS are NOT public; the
#   16-bit sync pattern below is an illustrative synthetic stand-in for the public
#   ARCHITECTURE (sync via a known unique word), not the real Orbcomm UW.
#
# WHAT THIS RUNG DOES (distinct from orbcomm_demod.rail, which assumed 1 sample/symbol
# with clean timing handed in): it takes an OVERSAMPLED IQ stream (sps samples/symbol)
# that contains the packet at an UNKNOWN SAMPLE OFFSET (leading idle/noise), with a
# residual CARRIER FREQUENCY OFFSET. It must:
#   (a) recover symbol TIMING (integrate-and-dump per symbol over the best phase),
#   (b) DIFFERENTIAL-demod (carrier-phase-invariant -> no Costas loop needed; this is the
#       structural reason diff-PSK is used on a phase-unknown LEO downlink), and
#   (c) locate the 16-bit UNIQUE WORD (frame sync) at its decoded offset.
# Test = the sync word is found at the expected position in the stream (the offset).
#
# I/O: int8 interleaved IQ -> /tmp/orbcomm_sync_in.s8 ; ground truth -> /tmp/*.npy
import numpy as np, sys

rng = np.random.default_rng(11)

def argf(name, dflt):
    return float(sys.argv[sys.argv.index(name)+1]) if name in sys.argv else dflt
def argi(name, dflt):
    return int(sys.argv[sys.argv.index(name)+1]) if name in sys.argv else dflt

NDATA   = argi('--n', 256)
SPS     = argi('--sps', 8)                 # samples per symbol (oversampled IQ)
PKT_OFF = argi('--pktoff', 37)             # packet starts this many SYMBOLS into the stream
TOFF    = argi('--toff', 0)                 # extra SAMPLE shift inside the symbol (0..sps-1)
FOFF    = argf('--foff', 0.0006)           # residual carrier offset, cycles per SAMPLE
SNR     = argf('--snr', 12.0)              # dB

# 16-bit unique word (illustrative synthetic value w/ good autocorrelation; NOT the real
# proprietary Orbcomm UW). Must match set_uw in src/orbcomm_sync.rail.
UW  = [0,0,1,0,0,1,1,0,1,1,1,0,1,0,0,1]
PRE = [0,1] * 24                           # 48-symbol alternating preamble (carrier wake-up)
data_bits = rng.integers(0, 2, NDATA).tolist()

# transmitted bit sequence: PREAMBLE + UNIQUE WORD + DATA
tx_bits = PRE + UW + data_bits
nbits = len(tx_bits)

# Differential BPSK modulate: bit=1 flips the carrier phase by pi (the bit rides the
# TRANSITION). Start the reference symbol at phase 0; one symbol per bit.
phase = 0.0
sym = []
for b in tx_bits:
    phase += (np.pi if b == 1 else 0.0)
    sym.append(np.exp(1j*phase))
sym = np.array(sym)

# Upsample to SPS samples/symbol (rectangular hold — this rung's timing recovery is an
# integrate-and-dump, no pulse shaping assumed; characterization, not a full RRC chain).
up = np.repeat(sym, SPS)

# Prepend leading idle (noise only) so the packet starts at an UNKNOWN SAMPLE OFFSET.
# TOFF adds an extra intra-symbol SAMPLE shift so the optimal integrate-and-dump timing
# phase is (PKT_OFF*sps + TOFF) % sps -- i.e. NON-ZERO -- which genuinely exercises the
# Rail rung's timing-phase search instead of always landing on phase 0.
lead_syms = PKT_OFF
lead = np.zeros(lead_syms*SPS + TOFF, dtype=complex)
tail = np.zeros(8*SPS, dtype=complex)       # small trailing idle
clean = np.concatenate([lead, up, tail])

# residual carrier frequency offset + a random fixed phase (diff-demod is invariant to it)
n = np.arange(len(clean))
phi0 = 1.3
rx = clean * np.exp(1j*(2*np.pi*FOFF*n + phi0))

# AWGN at the requested SNR over the whole stream (incl. idle, so the idle is pure noise)
sig_p = 1.0
sigma = np.sqrt(sig_p/2.0) / (10**(SNR/20))
rx = rx + (rng.standard_normal(len(rx)) + 1j*rng.standard_normal(len(rx)))*sigma

A = 80.0
i8 = np.clip(np.round(rx.real*A), -127, 127).astype(np.int8)
q8 = np.clip(np.round(rx.imag*A), -127, 127).astype(np.int8)
iq = np.empty(2*len(rx), np.int8); iq[0::2] = i8; iq[1::2] = q8
iq.tofile('/tmp/orbcomm_sync_in.s8')

# --- ground truth ---
# After integrate-and-dump + DIFFERENTIAL decode, the decoded bit at symbol k (>=1) equals
# tx_bits[k] (the bit IS the transition). But the stream is offset by PKT_OFF symbols of
# idle. The decoded-bit stream the Rail rung produces spans the WHOLE oversampled stream;
# the first valid decoded bit corresponding to the packet's first transition appears once
# the packet has started.
#
# Decoded symbol index s in the Rail output = symbol (s) of the full sampled stream. The
# packet's symbols occupy [PKT_OFF, PKT_OFF + nbits). Differential decode of symbol s uses
# symbol s and s-1; the first decoded bit that reflects a real packet transition is at
# decoded index (PKT_OFF) ... but decoded[k] = bit between sampled-symbol k and k-1.
# tx symbol m (0-based within packet) sits at sampled-symbol (PKT_OFF + m). Its driving bit
# tx_bits[m] shows up as the TRANSITION decoded at sampled-symbol (PKT_OFF + m), i.e.
# decoded index (PKT_OFF + m) for m>=1 (m=0 is the reference, no decoded bit, but the
# transition INTO the packet from idle is decoded at PKT_OFF as garbage).
#
# So: UW starts at tx-symbol m = len(PRE). decoded[j] is set from sampled-symbols (j+1, j)
# (i.e. decoded[j] is the transition INTO sampled-symbol j+1). UW[0] = tx_bits[len(PRE)] is
# the transition into the first UW symbol at sampled-symbol (PKT_OFF + len(PRE)), so it is
# decoded at index (PKT_OFF + len(PRE)) - 1:
#   uw_decoded_start = PKT_OFF + len(PRE) - 1
# (decoded[uw_decoded_start .. +16) carries the 16 UW bits).
uw_decoded_start = PKT_OFF + len(PRE) - 1

# Full decoded-bit ground truth for the PACKET region (for BER on the data after the UW).
# decoded[PKT_OFF + m] == tx_bits[m] for m in [1, nbits). (m=0 reference -> no info.)
total_sym = len(clean)//SPS
np.save('/tmp/orbcomm_sync_uw.npy', np.array(UW, dtype=np.uint8))
np.save('/tmp/orbcomm_sync_data.npy', np.array(data_bits, dtype=np.uint8))
# expected best integrate-and-dump timing phase = (leading-idle samples) mod sps = TOFF mod sps
# (PKT_OFF*sps is a whole number of symbols, so only the intra-symbol TOFF shifts the phase).
expected_phase = TOFF % SPS
np.save('/tmp/orbcomm_sync_uwstart.npy', np.array([uw_decoded_start], dtype=np.int64))
np.save('/tmp/orbcomm_sync_meta.npy',
        np.array([SPS, PKT_OFF, len(PRE), len(UW), NDATA, total_sym, expected_phase], dtype=np.int64))
# true carrier offset in cycles/symbol (Rail rung reports cycles/symbol)
foff_per_sym = FOFF * SPS
np.save('/tmp/orbcomm_sync_foff.npy', np.array([foff_per_sym], dtype=np.float64))

print(f'{nbits} tx bits ({len(PRE)} preamble + {len(UW)} UW + {NDATA} data), '
      f'sps={SPS} pkt_off={PKT_OFF}sym toff={TOFF}samp foff={FOFF}cyc/samp '
      f'({foff_per_sym:.4f}cyc/sym) snr={SNR}dB sigma={sigma:.3f}; total_sym={total_sym}; '
      f'UW@decoded[{uw_decoded_start}] expected_phase={expected_phase}')
