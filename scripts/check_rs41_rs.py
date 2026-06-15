#!/usr/bin/env python3
# RS41-4 checker: the Rail RS41-RS decoder must recover the planted 231-byte data block
# of EACH codeword byte-exact, with corrected_ok=1 (post-correction syndromes all zero).
# Cross-validates against the Python ref encoder (gen_rs41_rs.py truth).
import sys, json

truth = json.load(open('/tmp/rs41_rs_truth.json'))
blocks = truth['data_blocks']
ndata = 231

got = {}; cok = {}
for l in open(sys.argv[1]):
    if l.startswith('CW') and ' DATA ' in l:
        head, data = l.split(' DATA ')
        k = int(head[2:head.index(' ')])
        vals = [int(t) for t in data.split()]
        got[k] = vals[:ndata]
        for tok in head.split():
            if tok.startswith('corrected_ok='):
                cok[k] = int(tok.split('=')[1])

ok = True; msgs = []
for k in range(truth['I']):
    if k not in got:
        ok = False; msgs.append(f'CW{k}=missing'); continue
    nmatch = sum(1 for i in range(ndata) if i < len(got[k]) and got[k][i] == blocks[k][i])
    corr = cok.get(k, 0)
    if nmatch == ndata and corr == 1:
        msgs.append(f'CW{k}=ok({nmatch}/{ndata})')
    else:
        ok = False; msgs.append(f'CW{k}=FAIL({nmatch}/{ndata} corrected_ok={corr})')

print(f'rs41_rs: {"PASS" if ok else "FAIL"}  nerr/cw={truth["nerr"]}  ' + '  '.join(msgs))
sys.exit(0 if ok else 1)
