#!/usr/bin/env python3
# ===========================================================================
# ORBCOMM rung: orbcomm_gen — PYTHON end-to-end SYNTHETIC Orbcomm-like SD-PSK
# chain generator. Builds the FULL transmit chain from an opaque framed message
# down to int8 baseband IQ, so the whole receive chain (orbcomm_demod ->
# orbcomm_descram -> orbcomm_frame rungs) is testable offline against a known
# ground truth. This is the "gen.py" for the Orbcomm chain: it ties together
# every stage the individual decoder rungs reverse, in the exact same parameters
# (same 16-bit unique word, same self-sync scrambler taps x^7+x^4+1, same
# CRC-16/X-25 framing) the existing src/orbcomm_*.rail modules use.
#
# THE PUBLIC/PROPRIETARY BOUNDARY (enforced honestly, NOT fabricated):
#   DECODABLE FROM PUBLIC KNOWLEDGE (FCC filings + SDR reverse-engineering):
#     band 137.2-137.8 MHz / ~25 kHz channels, the PHY (differential/SD-PSK,
#     a preamble + unique word, a self-synchronizing scrambler), symbol rate
#     ~4800 sym/s, and link-layer FRAMING STRUCTURE (sync position, header
#     region, payload length, integrity over the framed region).
#   NOT PUBLIC / NOT FABRICATED: the SEMANTIC meaning of header/payload fields
#     (message-type codes, subscriber IDs, routing). Orbcomm user data is a
#     commercial, partly-encrypted system with no public message dictionary.
#     This generator therefore fills the header+payload with OPAQUE pseudo-random
#     bits (a recoverable test pattern), NEVER an invented "message" with field
#     semantics. The receiver reports them as opaque bits + a recomputable CRC.
#
# TRANSMIT CHAIN (each stage's matching receive rung in [ ]):
#   opaque header(24) + payload(144) bits
#     -> CRC-16/X-25 over (header+payload), MSB-first 16-bit FCS appended  [orbcomm_frame]
#     -> framed message = HEADER + PAYLOAD + FCS                           [orbcomm_frame]
#     -> self-sync (multiplicative) scramble: y[n]=x[n]^y[n-4]^y[n-7]      [orbcomm_descram]
#     -> prepend PREAMBLE(48 alternating) + UNIQUE WORD(16)               [orbcomm_demod sync]
#        (UW is NOT scrambled -- it must survive to drive frame sync)
#     -> differential / SD-PSK modulate (bit rides the phase TRANSITION:
#        0=no change, 1=pi flip), 1 sample/symbol                          [orbcomm_demod]
#     -> residual carrier freq offset + phase + AWGN                       [orbcomm_demod]
#     -> quantize to int8 interleaved IQ  -> /tmp/orbcomm_in.s8
#
# Ground truth (the opaque header+payload bits, the scrambled framed bits, the
# CRC, and all modulation params) is written to /tmp/orbcomm_gen_truth.json so
# check_orbcomm_gen.py can run the FULL inverse chain (differential demod -> UW
# frame sync -> self-sync descramble -> CRC verify) and confirm the generated
# signal decodes back to the original opaque framed bits AND that the CRC checks.
#
# HONESTY: this is a SYNTHETIC vector (numpy-generated), as expected for the
# rung. No real off-air Orbcomm pass is decoded and no attestation is produced.
# The unique word, scrambler taps, and header/payload layout are illustrative
# synthetic stand-ins for the PUBLIC architecture; the modulation/sync/scrambler/
# framing MECHANISM is the public part, the exact proprietary constants and
# user-payload semantics are deliberately not invented.
# ===========================================================================
import numpy as np, json, sys, math

def argf(flag, d):
    return float(sys.argv[sys.argv.index(flag)+1]) if flag in sys.argv else d

# ----------------------------------------------------------------- parameters
# Operating point note: the checker enforces an EXACT CRC over the full 168-bit
# framed region (no FEC in this chain, 1 sample/symbol). A single residual bit
# error therefore breaks integrity, so the clean operating point is ~12 dB
# (matches the demod rung's documented clean-at-12-dB result). Lower SNR is an
# honest failure of the exact-integrity check, not a bug; pass --snr to explore.
snr  = argf('--snr',  12.0)     # dB operating point (differential demod tolerant)
foff = argf('--foff', 0.0008)   # residual carrier freq offset, cycles/symbol
A    = argf('--amp',  90.0)     # int8 quantization scale
rng  = np.random.default_rng(23)

# 16-bit unique word (matches src/orbcomm_demod.rail / orbcomm_frame.rail set_uw).
# Illustrative synthetic sync pattern (good autocorrelation), NOT the real
# proprietary Orbcomm UW.
UW  = [0,0,1,0,0,1,1,0,1,1,1,0,1,0,0,1]
PRE = ([0,1] * 24)              # 48-symbol alternating preamble
T1, T2 = 4, 7                   # self-sync scrambler taps x^7 + x^4 + 1 (match orbcomm_descram)
HDR_LEN = 24                    # header REGION (opaque)
PAY_LEN = 144                   # payload REGION (opaque)

# ------------------------------------------- opaque framed message (no semantics)
header  = rng.integers(0, 2, HDR_LEN).astype(np.uint8)
payload = rng.integers(0, 2, PAY_LEN).astype(np.uint8)

def crc16_x25_bits(bits):
    # pack bits MSB-first into bytes (pad to byte boundary), reflected CRC-16/X-25
    # poly 0x8408, init 0xFFFF, xorout 0xFFFF  (matches src/orbcomm_frame.rail)
    nbytes = (len(bits) + 7) // 8
    by = bytearray(nbytes)
    for i, b in enumerate(bits):
        by[i // 8] |= (int(b) & 1) << (7 - (i % 8))
    crc = 0xFFFF
    for byte in by:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if (crc & 1) else (crc >> 1)
    return crc ^ 0xFFFF

framed_region = np.concatenate([header, payload])          # CRC is over header+payload
crc = crc16_x25_bits(framed_region)
crc_bits = np.array([(crc >> (15 - i)) & 1 for i in range(16)], np.uint8)   # MSB-first FCS

# message bits that get SCRAMBLED = header + payload + FCS (the UW is NOT scrambled)
msg_bits = np.concatenate([framed_region, crc_bits]).astype(np.uint8)

# ------------------------------------------- self-sync (multiplicative) scramble
def scramble(x):
    y = np.zeros(len(x), np.uint8)
    for n in range(len(x)):
        a = y[n - T1] if n - T1 >= 0 else 0
        b = y[n - T2] if n - T2 >= 0 else 0
        y[n] = x[n] ^ a ^ b
    return y

scr = scramble(msg_bits)

# ------------------------------------------- full transmitted bit sequence
# PRE(48) + UW(16) + scrambled(header+payload+FCS).  The differential modulator
# is driven by these bits: bit=1 flips the carrier phase by pi.
tx_bits = np.concatenate([np.array(PRE, np.uint8), np.array(UW, np.uint8), scr]).astype(np.uint8)
nbits = len(tx_bits)

# ------------------------------------------- differential / SD-PSK modulate
phase = 0.0
syms = []
for b in tx_bits:
    phase += (math.pi if int(b) == 1 else 0.0)
    syms.append(np.exp(1j * phase))
syms = np.array(syms)

# residual carrier frequency offset + phase
n   = np.arange(len(syms))
phi0 = 0.9
rx  = syms * np.exp(1j * (2 * np.pi * foff * n + phi0))

# AWGN at the requested SNR (signal power = 1)
sigma = (1.0 / np.sqrt(2)) / (10 ** (snr / 20))
rx = rx + (rng.standard_normal(len(rx)) + 1j * rng.standard_normal(len(rx))) * sigma

# ------------------------------------------- quantize to int8 interleaved IQ
i8 = np.clip(np.round(rx.real * A), -127, 127).astype(np.int8)
q8 = np.clip(np.round(rx.imag * A), -127, 127).astype(np.int8)
iq = np.empty(2 * len(rx), np.int8); iq[0::2] = i8; iq[1::2] = q8
iq.tofile('/tmp/orbcomm_in.s8')

# ------------------------------------------- ground truth
# decoded[k] (differential decode) corresponds to tx_bits[k+1]; decoded length = nbits-1.
# UW starts at tx index len(PRE) -> decoded index len(PRE)-1.
uw_decoded_start = len(PRE) - 1
# the scrambled message starts in the DECODED stream right after the UW:
scr_decoded_start = uw_decoded_start + len(UW)
truth = dict(
    snr=snr, foff=foff, amp=A,
    pre_len=len(PRE), uw_len=len(UW),
    hdr_len=HDR_LEN, pay_len=PAY_LEN,
    t1=T1, t2=T2,
    crc=crc,
    nbits_tx=nbits,
    uw_decoded_start=uw_decoded_start,
    scr_decoded_start=scr_decoded_start,
    uw=[int(x) for x in UW],
    header=[int(x) for x in header],
    payload=[int(x) for x in payload],
    msg_bits=[int(x) for x in msg_bits],      # plaintext header+payload+FCS (descram target)
    scr_bits=[int(x) for x in scr],           # scrambled message bits (after UW in tx)
    tx_bits=[int(x) for x in tx_bits],        # full driver sequence
)
json.dump(truth, open('/tmp/orbcomm_gen_truth.json', 'w'))
np.save('/tmp/orbcomm_gen_msg.npy', msg_bits)
np.save('/tmp/orbcomm_gen_scr.npy', scr)
np.save('/tmp/orbcomm_gen_tx.npy', tx_bits)

# Rail-friendly ground-truth sidecars (1 byte/bit, the repo's .bits convention) so the
# pure-Rail integrated decoder src/orbcomm_decode.rail can self-check bit-for-bit WITHOUT
# parsing JSON/npy. These mirror msg_bits exactly (header+payload+FCS plaintext) and the
# UW decoded-start index. Additive only -- the IQ vector + existing truth files are unchanged.
msg_bits.astype(np.uint8).tofile('/tmp/orbcomm_truth_msg.bits')
np.array([uw_decoded_start], np.uint8).tofile('/tmp/orbcomm_truth_uwpos.bits')

print(f'orbcomm_gen: opaque frame hdr({HDR_LEN})+pay({PAY_LEN})+fcs(16) = {len(msg_bits)} bits, '
      f'CRC-16/X-25=0x{crc:04x}')
print(f'  scramble x^7+x^4+1 (taps {T1},{T2}); tx = PRE({len(PRE)})+UW({len(UW)})+scr({len(scr)}) '
      f'= {nbits} symbols')
print(f'  SD-PSK foff={foff} cyc/sym snr={snr}dB amp={A} -> /tmp/orbcomm_in.s8 ({len(rx)} IQ samples)')
print(f'  UW@decoded[{uw_decoded_start}]  scrambled-msg@decoded[{scr_decoded_start}]')
