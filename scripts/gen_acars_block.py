#!/usr/bin/env python3
# Synthetic ARINC 618 ACARS BLOCK for src/acars_deframe.rail (rungs 3-5: deframe, parity,
# block-check, field parse). Builds a complete uplink/downlink character frame:
#
#   pre-key(0x00) ... BS BS BS (0x2B bit-sync '+') ... SYN SYN (0x16) ... SOH (0x01)
#   <mode 1 char> <addr/registration 7> <ack 1> <label 2> <block-id 1>
#   STX (0x02) <text ...> ETX (0x03)  BCS_hi BCS_lo  DEL (0x7F)
#
# Each character is 7-bit ASCII + an 8th ODD-parity bit (so total ones per byte is odd).
# The BCS is a 16-bit CRC-CCITT (poly 0x1021, reflected per the acarsdec convention:
# bitwise with reversed poly 0x8408, init 0) computed over the 7-bit DATA of every char
# from the one after SOH through ETX inclusive. The BCS bytes themselves carry NO parity.
#
# Emits, all little/MSB as noted:
#   /tmp/acars_block.bytes   one byte per char (8-bit: 7 data + parity), the deframer input
#   /tmp/acars_block.bits    one byte/bit, MSB-first per char, NRZ (for a bit-level deframer)
#   /tmp/acars_block_truth.json   ground truth fields + BCS for the check
import numpy as np, json, sys

def odd_parity_byte(c7):
    # c7 is 7-bit; set bit7 so total number of set bits is ODD
    ones = bin(c7 & 0x7F).count('1')
    p = 0 if (ones % 2 == 1) else 1     # parity bit makes total odd
    return (p << 7) | (c7 & 0x7F)

def crc_ccitt_acars(data7):
    # reflected CRC-16-CCITT (poly 0x8408), init 0 — acarsdec block-check convention
    crc = 0
    for c in data7:
        crc ^= (c & 0x7F)
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if (crc & 1) else (crc >> 1)
    return crc & 0xFFFF

SOH, STX, ETX, ETB, SYN, BS, DEL = 0x01, 0x02, 0x03, 0x17, 0x16, 0x2B, 0x7F

# header fields (7-bit ASCII)
mode  = ord('2')                 # mode char
reg   = '.N827NN'                # aircraft registration / address (7 chars)
ack   = 0x15                     # NAK (no ack) -> often 0x15; printable ack would be a tech ack
label = 'H1'                     # message label (2 chars)
blkid = ord('3')                 # block id (1 char)
text  = 'OPS NORMAL FL350 ETA 1423Z'

# the data over which the BCS is computed: every char AFTER SOH up to & incl ETX.
hdr = [mode] + [ord(x) for x in reg] + [ack] + [ord(x) for x in label] + [blkid]
data_for_crc = hdr + [STX] + [ord(x) for x in text] + [ETX]
bcs = crc_ccitt_acars(data_for_crc)
bcs_lo, bcs_hi = bcs & 0xFF, (bcs >> 8) & 0xFF   # acarsdec sends low byte first

# full character sequence (parity applied to every 7-bit char; BCS + framing controls
# carry parity too in real ACARS except BCS, which is raw — we keep BCS raw)
prekey = [0x00]*4
sync   = [BS, BS, BS, SYN, SYN, SOH]
body7  = data_for_crc                      # mode..ETX (7-bit)
body8  = [odd_parity_byte(c) for c in body7]
sync8  = [odd_parity_byte(c) for c in sync]
tail8  = [bcs_lo, bcs_hi, odd_parity_byte(DEL)]   # BCS raw (no parity), DEL parity'd

chars  = prekey + sync8 + body8 + tail8
np.array(chars, np.uint8).tofile('/tmp/acars_block.bytes')

# bit-level stream: MSB-first per byte (bit7..bit0)
bits = []
for c in chars:
    for k in range(7, -1, -1):
        bits.append((c >> k) & 1)
np.array(bits, np.uint8).tofile('/tmp/acars_block.bits')

truth = dict(mode=chr(mode), reg=reg, ack=ack, label=label, blkid=chr(blkid),
             text=text, bcs=bcs, bcs_lo=bcs_lo, bcs_hi=bcs_hi,
             n_chars=len(chars), prekey=len(prekey))
json.dump(truth, open('/tmp/acars_block_truth.json','w'))
print(f'block: {len(chars)} chars, BCS=0x{bcs:04x} (lo=0x{bcs_lo:02x} hi=0x{bcs_hi:02x}), '
      f'text="{text}" -> /tmp/acars_block.bytes + .bits')
