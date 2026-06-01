#!/usr/bin/env python3
import sys, numpy as np
pn=np.load('/tmp/derand_pn.npy'); exp=np.load('/tmp/derand_expected.npy')
pn_line=None; out=None
for l in open(sys.argv[1]):
    if l.startswith('PN '): pn_line=[int(x,16) for x in l.split()[1:9]]
    if l.startswith('OUT '): out=np.array([int(x) for x in l.split()[1:]],np.uint8)
ok_pn = pn_line==list(pn[:8])
ok_out = out is not None and len(out)==len(exp) and bool(np.all(out==exp))
print(f'PN matches CCSDS published = {ok_pn}  ({" ".join(f"{x:02x}" for x in (pn_line or []))})')
print(f'derandomized payload matches expected = {ok_out}')
sys.exit(0 if (ok_pn and ok_out) else 1)
