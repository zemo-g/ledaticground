#!/usr/bin/env python3
# Reference vector for src/orbcomm_descramble.rail -- DESCRAMBLE + deframe -> packet bytes.
#
# RUNG: the full link-layer chain. We build a PLAINTEXT frame, multiplicatively scramble it,
# and the Rail module must descramble it back, frame-sync on the unique word, deframe the
# payload into PACKET BYTES, and verify the CRC-16/X-25 FCS.
#
# PUBLIC-KNOWLEDGE BASIS / HONESTY: the self-sync scrambler taps (x^7+x^4+1), the 16-bit
# unique word, and the [preamble][UW][payload][FCS] layout are illustrative synthetic stand-ins
# for the PUBLIC Orbcomm architecture (self-sync scrambler + UW frame sync + CRC integrity).
# The semantic meaning of payload fields is NOT public and is NOT invented -- payload is opaque.
# These are SYNTHETIC test vectors, not a real off-air decode.
#
# Frame plaintext layout (bits):
#   [preamble 24][UW 16][payload 144 OPAQUE][FCS 16]   = 200 bits
#   FCS = CRC-16/X-25 over the (UW + payload) region = 160 bits = 20 bytes (byte-aligned).
#
# Multiplicative scrambler (transmitter):  y[n] = x[n] ^ y[n-4] ^ y[n-7]
# Self-sync descrambler  (receiver):       x_hat[n] = y[n] ^ y[n-4] ^ y[n-7]   (received bits only)
#
# Writes scrambled bits (1 byte/bit) -> /tmp/orbcomm_descramble_in.bits
#        plaintext bits (1 byte/bit) -> /tmp/orbcomm_descramble_plain.bits
#        truth .npy for the checker.
import numpy as np, sys

T1, T2 = 4, 7  # self-sync taps (MUST match the Rail module)

# 16-bit unique word (MUST match set_uw in the Rail module)
UW = np.array([0,0,1,0, 0,1,1,0, 1,1,1,0, 1,0,0,1], np.uint8)

def crc16_x25_bytes(bs):
    crc = 0xFFFF
    for b in bs:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if (crc & 1) else (crc >> 1)
    return crc ^ 0xFFFF

def pack_msb(bits):
    assert len(bits) % 8 == 0
    out = []
    for i in range(0, len(bits), 8):
        v = 0
        for k in range(8):
            v = (v << 1) | int(bits[i+k])
        out.append(v)
    return out

rng = np.random.default_rng(2026)

preamble = rng.integers(0, 2, 24).astype(np.uint8)
payload  = rng.integers(0, 2, 144).astype(np.uint8)

# CRC over the framed region = UW(16) + payload(144) = 160 bits = 20 bytes
region_bits = np.concatenate([UW, payload]).astype(np.uint8)
region_bytes = pack_msb(region_bits)
fcs = crc16_x25_bytes(region_bytes)
# FCS as 16 bits MSB-first (matches the Rail fcs_read MSB-first reader)
fcs_bits = np.array([(fcs >> (15 - k)) & 1 for k in range(16)], np.uint8)

# full plaintext frame
x = np.concatenate([preamble, UW, payload, fcs_bits]).astype(np.uint8)
N = len(x)

# multiplicative scrambler: y[n] = x[n] ^ y[n-4] ^ y[n-7]
y = np.zeros(N, np.uint8)
for n in range(N):
    a = y[n-T1] if n - T1 >= 0 else 0
    b = y[n-T2] if n - T2 >= 0 else 0
    y[n] = x[n] ^ a ^ b

# self-sync descramble (oracle, received bits only) -- equals x after the T2-bit transient
def descram(stream):
    out = np.zeros(len(stream), np.uint8)
    for n in range(len(stream)):
        a = stream[n-T1] if n - T1 >= 0 else 0
        b = stream[n-T2] if n - T2 >= 0 else 0
        out[n] = stream[n] ^ a ^ b
    return out

x_hat = descram(y)

# emit
y.astype(np.uint8).tofile('/tmp/orbcomm_descramble_in.bits')
x.astype(np.uint8).tofile('/tmp/orbcomm_descramble_plain.bits')
np.save('/tmp/orbcomm_descramble_plain.npy', x)
np.save('/tmp/orbcomm_descramble_xhat.npy', x_hat)
np.save('/tmp/orbcomm_descramble_uwpos.npy', np.array([24], np.int64))  # UW starts at bit 24
np.save('/tmp/orbcomm_descramble_pkt.npy', np.array(region_bytes, np.uint8))
np.save('/tmp/orbcomm_descramble_fcs.npy', np.array([fcs], np.int64))

# self-check the oracle
match = bool(np.array_equal(x_hat[T2:], x[T2:]))
print(f'N={N} taps={T1},{T2}  UW@24  FCS=0x{fcs:04x}  pkt_bytes={len(region_bytes)}  '
      f'descram round-trips (from bit {T2}): {match}')
