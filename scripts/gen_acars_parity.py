#!/usr/bin/env python3
# Synthetic vector for src/acars_parity.rail — the focused ACARS parity rung:
#   per-character ODD parity + block-check sequence (BCS) over the message.
#
# This is a TIGHTER rung than the full ARINC 618 deframe: no SYN/SOH/STX framing
# search, just the integrity layer. The "message" is the run of body characters that
# the BCS covers (per ARINC 618 / acarsdec: every char after SOH through ETX inclusive).
# We hand the rail module that exact run of chars + the 2 trailing BCS bytes.
#
# Each message char is 7-bit ASCII with an 8th ODD-parity bit (total set bits per byte
# is ODD). The BCS is the reflected CRC-CCITT (poly 0x8408, init 0) over the 7-bit DATA
# of every message char. The 2 BCS bytes are RAW 8-bit, low byte first, and are NOT
# parity-protected and NOT part of their own CRC.
#
# Emits:
#   /tmp/acars_parity_clean.bytes  message chars (8-bit) + BCS_lo + BCS_hi  (clean frame)
#   /tmp/acars_parity_flip.bytes   same, but with ONE data bit flipped in one char
#   /tmp/acars_parity_truth.json   ground truth (n_msg, bcs, flip position) for the check
#
# Test contract: the rail module decodes BOTH files and must report:
#   clean -> PARITY_ERRORS 0, BCS_OK 1
#   flip  -> PARITY_ERRORS 1 (the flipped char now has even parity) AND BCS_OK 0
# i.e. a single bit flip is DETECTED by both the parity check and the BCS.
import numpy as np, json, sys

ETX = 0x03

def odd_parity_byte(c7):
    """7-bit value -> 8-bit byte with bit7 set so the total number of 1s is ODD."""
    ones = bin(c7 & 0x7F).count('1')
    p = 0 if (ones % 2 == 1) else 1   # parity bit makes the total odd
    return (p << 7) | (c7 & 0x7F)

def crc_ccitt_acars(data7):
    """Reflected CRC-16-CCITT (poly 0x8408), init 0 — the acarsdec BCS convention.
    Computed over the low 7 bits of each char."""
    crc = 0
    for c in data7:
        crc ^= (c & 0x7F)
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if (crc & 1) else (crc >> 1)
    return crc & 0xFFFF

# The message the BCS covers. Realistic short ACARS body (downlink-ish), incl. ETX terminus.
text = 'OPS NORMAL FL350'
msg7 = [ord(c) for c in text] + [ETX]          # 7-bit data chars, ETX as the terminus
bcs  = crc_ccitt_acars(msg7)
bcs_lo, bcs_hi = bcs & 0xFF, (bcs >> 8) & 0xFF  # acarsdec sends the low byte first

# --- clean frame: parity'd chars + raw BCS bytes ---
msg8  = [odd_parity_byte(c) for c in msg7]
clean = msg8 + [bcs_lo, bcs_hi]
np.array(clean, np.uint8).tofile('/tmp/acars_parity_clean.bytes')

# --- corrupted frame: flip ONE data bit (bit 0) in one message char (index 5: the space) ---
FLIP_IDX = 5      # which message char to corrupt
FLIP_BIT = 0      # which data bit to flip (a low data bit, never the parity bit)
flip = list(clean)
flip[FLIP_IDX] = flip[FLIP_IDX] ^ (1 << FLIP_BIT)   # single-bit error in the data field
np.array(flip, np.uint8).tofile('/tmp/acars_parity_flip.bytes')

truth = dict(text=text, n_msg=len(msg7), bcs=bcs, bcs_lo=bcs_lo, bcs_hi=bcs_hi,
             flip_idx=FLIP_IDX, flip_bit=FLIP_BIT, n_bytes=len(clean))
json.dump(truth, open('/tmp/acars_parity_truth.json', 'w'))

# sanity: confirm the flip actually breaks parity on that char and BCS on the message
flip_msg7 = [b & 0x7F for b in flip[:len(msg7)]]
flip_bcs  = crc_ccitt_acars(flip_msg7)
flip_char_ones = bin(flip[FLIP_IDX]).count('1')
print(f'acars_parity: msg={len(msg7)} chars, BCS=0x{bcs:04x} (lo=0x{bcs_lo:02x} hi=0x{bcs_hi:02x})')
print(f'  clean -> /tmp/acars_parity_clean.bytes ({len(clean)} bytes)')
print(f'  flip  -> /tmp/acars_parity_flip.bytes  (char[{FLIP_IDX}] bit{FLIP_BIT}; '
      f'now {flip_char_ones} ones={"ODD->bad" if flip_char_ones%2 else "EVEN->parity err"}, '
      f'recomputed BCS=0x{flip_bcs:04x} != 0x{bcs:04x})')
