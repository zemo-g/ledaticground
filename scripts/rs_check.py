#!/usr/bin/env python3
# Validate src/rs.rail: each de-interleaved RS(255,223) codeword must correct back to the
# original 223-byte data block, and report corrected_ok=1.
import sys, json, numpy as np
gt = json.load(open('/tmp/rs_truth.json'))
data_blocks = gt['data_blocks']; I = gt['I']
got = {}
ok_flags = {}
for l in open(sys.argv[1]):
    if l.startswith('CW'):
        # "CW<k> nerr=.. [L=.. corrected_ok=..] DATA b0 b1 ..."
        k = int(l[2:l.index(' ')])
        di = l.index('DATA ')
        data = [int(x) for x in l[di+5:].split()]
        got[k] = data
        ok_flags[k] = ('corrected_ok=1' in l) or ('nerr=0' in l)
allok = True
for k in range(I):
    if k not in got:
        print(f"CW{k} missing = FAIL"); allok=False; continue
    match = got[k][:223] == data_blocks[k]
    cok = ok_flags.get(k, False)
    print(f"CW{k} data matches original = {match}  syndrome_clean = {cok}")
    if not (match and cok): allok = False
print("PASS" if allok else "FAIL")
sys.exit(0 if allok else 1)
