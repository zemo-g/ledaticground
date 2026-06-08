#!/usr/bin/env python3
# Validates src/orbcomm_descramble.rail -- DESCRAMBLE + deframe -> packet bytes.
#
# PASS requires ALL of:
#   (1) MATCH=1            -- the scrambled known sequence descrambles back to plaintext exactly
#                            (the headline rung test), AND mismatches=0.
#   (2) SYNC uw_at == 24   -- frame sync locks the unique word at the expected descrambled offset
#       with score 16/16.
#   (3) PKT bytes == oracle region bytes (UW + payload packed MSB-first) -- the deframed packet.
#   (4) CRC_OK=1           -- CRC-16/X-25 over the framed region matches the trailing FCS.
import sys, numpy as np

plain   = np.load('/tmp/orbcomm_descramble_plain.npy')
uwpos   = int(np.load('/tmp/orbcomm_descramble_uwpos.npy')[0])
pkt_exp = np.load('/tmp/orbcomm_descramble_pkt.npy')

descram = None
match = None
mismatches = None
uw_at = uw_sc = None
pkt = None
crc_ok = None

for l in open(sys.argv[1]):
    l = l.strip()
    if l.startswith('DESCRAM '):
        descram = np.array([int(c) for c in l[8:]], dtype=np.uint8)
    elif l.startswith('MATCH='):
        # MATCH=1 mismatches=0
        toks = l.split()
        match = int(toks[0].split('=')[1])
        mismatches = int(toks[1].split('=')[1])
    elif l.startswith('SYNC '):
        toks = l.split()
        uw_at = int(toks[1].split('=')[1])
        uw_sc = int(toks[2].split('=')[1])
    elif l.startswith('PKT '):
        # PKT <hex bytes...>
        hx = l[4:].split()
        pkt = np.array([int(h, 16) for h in hx], dtype=np.uint8)
    elif l.startswith('CRC_OK='):
        crc_ok = int(l.split('=')[1])

fails = []
# (1) round-trip
if descram is None:
    fails.append('no DESCRAM line')
else:
    # compare descrambled output to plaintext from the self-sync transient onward
    nb = min(len(descram), len(plain))
    rt = bool(np.array_equal(descram[7:nb], plain[7:nb]))
    if not rt:
        fails.append(f'descramble does not round-trip (post-transient mismatch)')
if match != 1:
    fails.append(f'MATCH={match} (want 1)')
if mismatches not in (0, None) and mismatches != 0:
    fails.append(f'mismatches={mismatches} (want 0)')
# (2) frame sync
if uw_at != uwpos:
    fails.append(f'uw_at={uw_at} (want {uwpos})')
if uw_sc != 16:
    fails.append(f'uw score={uw_sc}/16 (want 16/16)')
# (3) deframed packet bytes
if pkt is None:
    fails.append('no PKT line')
elif not np.array_equal(pkt, pkt_exp):
    fails.append(f'packet bytes mismatch: got {pkt.tolist()} want {pkt_exp.tolist()}')
# (4) CRC
if crc_ok != 1:
    fails.append(f'CRC_OK={crc_ok} (want 1)')

print(f'round-trip MATCH={match} mismatches={mismatches}  uw_at={uw_at} score={uw_sc}/16  '
      f'pkt_bytes={None if pkt is None else len(pkt)} (want {len(pkt_exp)})  CRC_OK={crc_ok}')
if fails:
    for f in fails:
        print('  -', f)
    print('FAIL')
    sys.exit(1)
print('PASS')
sys.exit(0)
