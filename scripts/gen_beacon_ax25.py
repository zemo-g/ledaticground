#!/usr/bin/env python3
# Rung 3 generator: a real AX.25 UI frame, NRZI-encoded + bit-stuffed + flag-delimited,
# written as a channel bitstream (1 byte per bit) -> /tmp/beacon_ax25_in.bits for
# src/beacon_ax25.rail. This mirrors what an FSK/AFSK beacon demod (rung 2) hands to the
# deframer: raw NRZI channel bits. Ground truth (the de-framed payload bytes + FCS) ->
# /tmp/beacon_ax25_truth.npy. SYNTHETIC TEST VECTOR — not a real packet reception.
import numpy as np, sys

def args(n,d): return sys.argv[sys.argv.index(n)+1] if n in sys.argv else d

# ---- build an AX.25 UI frame ----
def ax25_addr(call, ssid, last=False):
    call = (call.upper() + '      ')[:6]
    out = [ord(c) << 1 for c in call]              # callsign chars shifted left 1
    ssid_byte = 0x60 | ((ssid & 0x0F) << 1) | (1 if last else 0)
    out.append(ssid_byte)
    return out

dest = args('--dest', 'CQ')
src  = args('--src',  'LEDATC')
info = args('--info', 'LEDATICGROUND BEACON TEST 137.500MHZ').encode()

frame = []
frame += ax25_addr(dest, 0, last=False)
frame += ax25_addr(src,  0, last=True)
frame.append(0x03)   # control: UI
frame.append(0xF0)   # PID: no layer 3
frame += list(info)
frame_bytes = bytes(frame)

# ---- CRC-16/X-25 (== AX.25 FCS == AIS FCS) ----
def crc_x25(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if (crc & 1) else (crc >> 1)
    return crc ^ 0xFFFF

fcs = crc_x25(frame_bytes)
# FCS is transmitted LSB-first, low byte first
fcs_bytes = bytes([fcs & 0xFF, (fcs >> 8) & 0xFF])

# ---- serialize bytes -> bits, LSB-first per byte (AX.25 wire order) ----
def bytes_to_bits_lsb(bs):
    out = []
    for b in bs:
        for i in range(8):
            out.append((b >> i) & 1)
    return out

content_bits = bytes_to_bits_lsb(frame_bytes + fcs_bytes)

# ---- bit-stuff: after five consecutive 1s, insert a 0 ----
def stuff(bits):
    out = []; ones = 0
    for b in bits:
        out.append(b)
        if b == 1:
            ones += 1
            if ones == 5:
                out.append(0); ones = 0
        else:
            ones = 0
    return out

stuffed = stuff(content_bits)

# ---- frame with 0x7E flags (LSB-first 0x7E = 0 1 1 1 1 1 1 0) on each side ----
FLAG = [0,1,1,1,1,1,1,0]
logical = FLAG + FLAG + stuffed + FLAG + FLAG

# ---- NRZI encode: logical 1 = no transition, 0 = transition. start level 0 ----
def nrzi_encode(bits, start=0):
    out = []; level = start
    for b in bits:
        if b == 0:
            level ^= 1
        out.append(level)
    return out

channel = nrzi_encode(logical, start=0)
np.array(channel, np.uint8).tofile('/tmp/beacon_ax25_in.bits')

# truth: the de-framed CONTENT bytes (frame+fcs), and the bare frame bytes + fcs
np.save('/tmp/beacon_ax25_truth.npy', np.frombuffer(frame_bytes + fcs_bytes, np.uint8))
np.save('/tmp/beacon_ax25_frame.npy', np.frombuffer(frame_bytes, np.uint8))
with open('/tmp/beacon_ax25_meta.txt','w') as f:
    f.write(f'fcs={fcs:04x}\n')
    f.write(f'frame_len={len(frame_bytes)}\n')
    f.write(f'content_len={len(frame_bytes)+2}\n')

print(f'AX.25 UI frame: dest={dest} src={src} info_len={len(info)} '
      f'frame={len(frame_bytes)}B fcs=0x{fcs:04x}')
print(f'  content_bits={len(content_bits)} stuffed={len(stuffed)} channel_bits={len(channel)}')
print(f'  -> /tmp/beacon_ax25_in.bits  (NRZI channel bits, 1 byte/bit)')
