#!/usr/bin/env python3
# BEACON beacon_gen checker: validate the END-TO-END decode of src/beacon_gen.rail
# against the ground truth written by gen_beacon_gen.py.
#
# PASS requires ALL of:
#   - CRC_OK == 1
#   - recovered byte count == content_len (frame + FCS)
#   - recovered HEX bytes exactly match the truth frame+FCS bytes
#   - the AX.25 SOURCE CALLSIGN (decoded from the recovered address field: each addr byte
#     is the callsign char << 1, so >>1 to recover it) matches the ground-truth callsign
#   - the recovered ASCII info field contains the beacon text
import sys, numpy as np

def ax25_src_call(frame_bytes):
    # AX.25: dest addr = bytes 0..6, source addr = bytes 7..13 (6 callsign chars + SSID).
    # Each callsign char is shifted left 1 on the wire -> >>1 to recover. Pad/space stripped.
    if len(frame_bytes) < 14:
        return ''
    src6 = frame_bytes[7:13]
    return ''.join(chr(b >> 1) for b in src6).rstrip()

truth = np.load('/tmp/beacon_gen_truth.npy').astype(np.uint8)   # frame + fcs
content_len = len(truth)
meta = {}
for l in open('/tmp/beacon_gen_meta.txt'):
    if '=' in l:
        k, v = l.strip().split('=', 1)
        meta[k] = v
src_call = meta.get('src_call', '')
payload  = meta.get('payload', '')

crc_ok = None; hexbytes = None; nbytes = None; ascii_line = None
for l in open(sys.argv[1]):
    if l.startswith('CRC_OK='): crc_ok = int(l.strip().split('=')[1])
    if l.startswith('BYTES'):
        for tok in l.split():
            if tok.startswith('bytes='): nbytes = int(tok.split('=')[1])
    if l.startswith('HEX '):
        toks = l.strip()[4:].split()
        hexbytes = bytes(int(h, 16) for h in toks) if toks else b''
    if l.startswith('ASCII '):
        ascii_line = l[6:].rstrip('\n')

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

# source callsign decoded from the recovered AX.25 address field (>>1 per char)
rec_call = ax25_src_call(hexbytes) if hexbytes else ''
have_call = (rec_call == src_call) if src_call else True
if not have_call:
    ok = False
msgs.append(f'src_call={rec_call!r}{"" if have_call else f" != {src_call!r}"}')

if ascii_line is None:
    ok = False; msgs.append('no ASCII line')
else:
    have_text = payload in ascii_line if payload else True
    msgs.append(f'payload_text={"y" if have_text else "n"}')
    if not have_text:
        ok = False

print(f'beacon_gen: {"PASS" if ok else "FAIL"}  ' + '  '.join(msgs))
print(f'  truth: src={src_call} content={content_len}B payload="{payload}"')
if ascii_line is not None:
    print(f'  recovered ASCII: "{ascii_line.strip()}"')
sys.exit(0 if ok else 1)
