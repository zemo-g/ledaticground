#!/usr/bin/env python3
# Rung 3 checker: validate src/beacon_ax25.rail output vs ground truth.
# PASS if: CRC_OK==1, recovered byte count == content_len, and the recovered HEX
# bytes exactly match the ground-truth (frame + FCS) bytes.
import sys, numpy as np

truth = np.load('/tmp/beacon_ax25_truth.npy').astype(np.uint8)   # frame + fcs
content_len = len(truth)

crc_ok = None; hexbytes = None; nbytes = None
for l in open(sys.argv[1]):
    if l.startswith('CRC_OK='): crc_ok = int(l.strip().split('=')[1])
    if l.startswith('BYTES'):
        for tok in l.split():
            if tok.startswith('bytes='): nbytes = int(tok.split('=')[1])
    if l.startswith('HEX '):
        hexbytes = bytes(int(h,16) for h in l.strip()[4:].split())

ok = True; msgs = []
if crc_ok != 1:
    ok = False; msgs.append(f'CRC_OK={crc_ok} (want 1)')
else:
    msgs.append('CRC_OK=1')
if nbytes != content_len:
    ok = False; msgs.append(f'nbytes={nbytes} (want {content_len})')
if hexbytes is None:
    ok = False; msgs.append('no HEX line')
else:
    nb = min(len(hexbytes), content_len)
    nmatch = sum(1 for i in range(nb) if hexbytes[i] == truth[i])
    msgs.append(f'bytematch={nmatch}/{content_len}')
    if nmatch != content_len or len(hexbytes) != content_len:
        ok = False

print(f'beacon_ax25: {"PASS" if ok else "FAIL"}  ' + '  '.join(msgs))
print(f'  truth frame+fcs ({content_len}B): ' + ' '.join(f'{b:02x}' for b in truth[:16]) +
      (' ...' if content_len > 16 else ''))
sys.exit(0 if ok else 1)
