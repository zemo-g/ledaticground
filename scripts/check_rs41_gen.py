#!/usr/bin/env python3
# RS41-1 checker: validate that gen_rs41_gen.py produced a self-consistent vector --
# the descrambled truth frame, when re-parsed (descramble round-trip already proven in
# the generator's own selfcheck), yields the planted serial / frame# / ECEF byte-exact.
# This is the SELFTEST CONTRACT for the generator (no Rail involved -- proves the vector
# is correct before the Rail decoder is asked to reproduce it).
import sys, numpy as np

def crc16_ccitt(data):
    crc = 0xFFFF
    for b in data:
        crc ^= (b << 8); crc &= 0xFFFF
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if (crc & 0x8000) else (crc << 1) & 0xFFFF
    return crc & 0xFFFF

SYNC = [0x86, 0x35, 0xF4, 0x40, 0x93, 0xDF, 0x1A, 0x60]

truth = np.load('/tmp/rs41_truth.npy').astype(int).tolist()       # descrambled frame
scrambled = np.load('/tmp/rs41_scrambled.npy').astype(int).tolist()
mask = np.load('/tmp/rs41_mask.npy').astype(int).tolist()
meta = {}
for l in open('/tmp/rs41_meta.txt'):
    if '=' in l:
        k, v = l.strip().split('=', 1); meta[k] = v

ok = True; msgs = []

# 1. descramble round-trip: scrambled ^ mask(mod64) == truth
rt = [scrambled[i] ^ mask[i % 64] for i in range(len(scrambled))]
if rt == truth:
    msgs.append('descramble_roundtrip=ok')
else:
    ok = False; msgs.append('descramble_roundtrip=FAIL')

# 2. sync header present
if truth[:8] == SYNC:
    msgs.append('sync=ok')
else:
    ok = False; msgs.append(f'sync=FAIL {truth[:8]}')

# 3. deinterleave cw0 data + walk typed blocks + verify CRC + recover facts
cad = truth[8:]
d0 = [cad[i*2+0] for i in range(231)]
found = {}; p = 0
while p < len(d0) and d0[p] == 0x79:
    btype = d0[p+1]; blen = d0[p+2]
    body = d0[p:p+3+blen]
    rxcrc = d0[p+3+blen] | (d0[p+4+blen] << 8)
    calc = crc16_ccitt(body)
    if calc != rxcrc:
        ok = False; msgs.append(f'block_0x{btype:02x}_crc=FAIL')
    found[btype] = body[3:3+blen]
    p += 3 + blen + 2

if 0x28 in found:
    st = found[0x28]
    rec_frame = st[0] | (st[1] << 8)
    rec_serial = bytes(st[2:10]).decode('ascii', 'replace').rstrip()
    if rec_serial == meta['serial']:
        msgs.append(f'serial={rec_serial}')
    else:
        ok = False; msgs.append(f'serial={rec_serial!r}!={meta["serial"]!r}')
    if rec_frame == int(meta['frame_num']):
        msgs.append(f'frame#={rec_frame}')
    else:
        ok = False; msgs.append(f'frame#={rec_frame}!={meta["frame_num"]}')
else:
    ok = False; msgs.append('no STATUS block')

if 0x7C in found:
    gp = found[0x7C]
    def rd32(o): return int.from_bytes(bytes(gp[o:o+4]), 'little', signed=True)
    rx = [rd32(0), rd32(4), rd32(8)]
    want = [int(meta['ecef_x_cm']), int(meta['ecef_y_cm']), int(meta['ecef_z_cm'])]
    if rx == want:
        msgs.append('ecef=byte-exact')
    else:
        ok = False; msgs.append(f'ecef={rx}!={want}')
else:
    ok = False; msgs.append('no GPS-POS block')

print(f'rs41_gen: {"PASS" if ok else "FAIL"}  ' + '  '.join(msgs))
sys.exit(0 if ok else 1)
