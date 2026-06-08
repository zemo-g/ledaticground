#!/usr/bin/env python3
# Validate src/rs_dualbasis.rail: the dual-basis round-trip self-test must PASS, and each
# de-interleaved RS(255,223) codeword must correct back to the original 223-byte message
# (compared in DUAL basis, since the Rail module re-encodes corrected data to dual on output)
# and report corrected_ok=1.
import sys, json
gt = json.load(open('/tmp/rs_dualbasis_truth.json'))
data_blocks_dual = gt['data_blocks_dual']; I = gt['I']
got = {}; ok_flags = {}; selftest_pass = None
for l in open(sys.argv[1]):
    if l.startswith('SELFTEST'):
        selftest_pass = ('PASS=1' in l)
    if l.startswith('CW'):
        # "CW<k> nerr=.. [L=.. corrected_ok=..] DATA b0 b1 .."  (DATA bytes are DUAL basis)
        k = int(l[2:l.index(' ')])
        di = l.index('DATA ')
        got[k] = [int(x) for x in l[di+5:].split()]
        ok_flags[k] = ('corrected_ok=1' in l) or ('nerr=0' in l)

allok = True
if selftest_pass is None:
    print("SELFTEST line missing = FAIL"); allok = False
else:
    print(f"dualbasis selftest (256-value round-trip + 4 anchors) = {'PASS' if selftest_pass else 'FAIL'}")
    if not selftest_pass: allok = False
for k in range(I):
    if k not in got:
        print(f"CW{k} missing = FAIL"); allok = False; continue
    match = got[k][:223] == data_blocks_dual[k]
    cok = ok_flags.get(k, False)
    print(f"CW{k} recovered message (dual basis) matches original = {match}  syndrome_clean = {cok}")
    if not (match and cok): allok = False
print("PASS" if allok else "FAIL")
sys.exit(0 if allok else 1)
