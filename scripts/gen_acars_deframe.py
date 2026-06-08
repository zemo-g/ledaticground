#!/usr/bin/env python3
# Synthetic ARINC 618 ACARS character-block generator for the acars_deframe rung
# (src/acars_deframe.rail). Exercises the deframe spec end to end:
#   pre-key detect, bit/char sync, 7-bit characters, SOH..STX..text..ETX/ETB structure.
#
# Wire layout (one entry per character, MSB-first bits within each char):
#   pre-key (0x00 idle) ... bit-sync '+' (0x2B) ... SYN SYN (0x16 0x16) ... SOH (0x01)
#   mode(1) reg/address(7) ack(1) label(2) block-id(1)  STX(0x02) text... ETX(0x03)
#   BCS_lo BCS_hi  DEL(0x7F)
#
# Every 7-bit ASCII char carries an 8th ODD-parity bit (total set bits per byte is odd),
# EXCEPT the two raw BCS bytes (sent as raw 8-bit, low byte first, per the acarsdec
# convention). The BCS is a 16-bit reflected CRC-CCITT (poly 0x8408, init 0) over the
# 7-bit DATA of every char from the one after SOH through ETX inclusive.
#
# Writes:
#   /tmp/acars_block.bytes        one byte/char (8-bit: 7 data + parity) — deframer input
#   /tmp/acars_block.bits         one byte/bit, MSB-first per char (for a bit-level deframer)
#   /tmp/acars_deframe_truth.json ground truth fields + framing offsets + BCS
#
# --corrupt flips ONE data bit inside the text so the checker can confirm the deframer
# actually flags parity + BCS failure (non-vacuous PASS).
import numpy as np, json, sys

def odd_parity_byte(c7):
    """7-bit char -> 8-bit with bit7 set so total set bits is ODD."""
    ones = bin(c7 & 0x7F).count('1')
    p = 0 if (ones % 2 == 1) else 1
    return (p << 7) | (c7 & 0x7F)

def crc_ccitt_acars(data7):
    """reflected CRC-16-CCITT (poly 0x8408), init 0 — acarsdec block-check convention,
    over the 7-bit DATA of each char."""
    crc = 0
    for c in data7:
        crc ^= (c & 0x7F)
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if (crc & 1) else (crc >> 1)
    return crc & 0xFFFF

SOH, STX, ETX, ETB, SYN, BS, DEL = 0x01, 0x02, 0x03, 0x17, 0x16, 0x2B, 0x7F

corrupt = '--corrupt' in sys.argv

# ARINC 618 header fields (7-bit ASCII)
mode  = ord('2')                 # mode char
reg   = '.N827NN'                # aircraft registration / address (7 chars)
ack   = 0x15                     # NAK (no tech-ack)
label = 'H1'                     # message label (2 chars)
blkid = ord('3')                 # block id (1 char)
text  = 'OPS NORMAL FL350 ETA 1423Z'

# DATA over which the BCS is computed: every char AFTER SOH up to & incl ETX.
hdr = [mode] + [ord(x) for x in reg] + [ack] + [ord(x) for x in label] + [blkid]
data_for_crc = hdr + [STX] + [ord(x) for x in text] + [ETX]
bcs = crc_ccitt_acars(data_for_crc)
bcs_lo, bcs_hi = bcs & 0xFF, (bcs >> 8) & 0xFF   # low byte first on the wire

# framing controls + body carry parity; BCS bytes are RAW (no parity); DEL parity'd
prekey = [0x00] * 4
sync   = [BS, BS, BS, SYN, SYN, SOH]
sync8  = [odd_parity_byte(c) for c in sync]
body8  = [odd_parity_byte(c) for c in data_for_crc]
tail8  = [bcs_lo, bcs_hi, odd_parity_byte(DEL)]

chars = prekey + sync8 + body8 + tail8

# framing offsets (in CHAR units) for the deframe-structure assertions
# layout: prekey(4) + [BS BS BS SYN SYN SOH](6) + body + tail
syn_off = len(prekey) + 3            # first SYN char index (after BS BS BS)
soh_off = len(prekey) + 5            # SOH char index
hdr_off = soh_off + 1                # first header char (mode) index
stx_off = hdr_off + len(hdr)         # STX char index
etx_off = stx_off + 1 + len(text)    # ETX char index

if corrupt:
    # flip one DATA bit of a text char so parity breaks AND the BCS no longer matches.
    # pick a char well inside the text; toggle bit0 of its 7-bit data, keep stored byte.
    tgt = stx_off + 5                 # 6th text char
    chars[tgt] = chars[tgt] ^ 0x01    # flip data bit0 -> parity now even -> error

np.array(chars, np.uint8).tofile('/tmp/acars_block.bytes')

# bit-level stream: MSB-first per byte (bit7..bit0)
bits = []
for c in chars:
    for k in range(7, -1, -1):
        bits.append((c >> k) & 1)
np.array(bits, np.uint8).tofile('/tmp/acars_block.bits')

truth = dict(
    mode=chr(mode), reg=reg, ack=ack, label=label, blkid=chr(blkid),
    text=text, bcs=bcs, bcs_lo=bcs_lo, bcs_hi=bcs_hi,
    n_chars=len(chars), prekey=len(prekey),
    syn_off=syn_off, soh_off=soh_off, stx_off=stx_off, etx_off=etx_off,
    corrupt=corrupt,
)
json.dump(truth, open('/tmp/acars_deframe_truth.json', 'w'))
tag = ' [CORRUPT]' if corrupt else ''
print(f'acars block{tag}: {len(chars)} chars, syn@{syn_off} soh@{soh_off} '
      f'stx@{stx_off} etx@{etx_off}, BCS=0x{bcs:04x} '
      f'(lo=0x{bcs_lo:02x} hi=0x{bcs_hi:02x}), text="{text}" '
      f'-> /tmp/acars_block.bytes + .bits')
