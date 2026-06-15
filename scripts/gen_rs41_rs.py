#!/usr/bin/env python3
# RS41-4 generator: reference vector for src/rs41_rs.rail -- RS(255,231) FCR=0, I=2
# interleave, with up to 12 byte errors injected per codeword. Clone of gen_rs.py with
# NPAR=24 FCR=0 I=2. Writes /tmp/rs41_rs_in.s8 (510 CADU bytes) + /tmp/rs41_rs_truth.json
# (the 231-byte data blocks, ground truth). SYNTHETIC-ONLY.
import numpy as np, sys, json

PRIM = 0x11D
exp = [0]*512; log = [0]*256
x = 1
for i in range(255):
    exp[i] = x; log[x] = i
    x <<= 1
    if x & 0x100: x ^= PRIM
for i in range(255, 512): exp[i] = exp[i-255]
def gmul(a, b):
    if a == 0 or b == 0: return 0
    return exp[(log[a]+log[b]) % 255]

NPAR = 24   # RS(255,231): 24 parity, corrects up to 12 byte errors
FCR  = 0
I    = 2
g = [1]
for i in range(NPAR):
    root = exp[(FCR+i) % 255]
    ng = [0]*(len(g)+1)
    for j in range(len(g)):
        ng[j]   ^= gmul(g[j], root)
        ng[j+1] ^= g[j]
    g = ng

def rs_encode(data):   # 231 data bytes -> 255 codeword (systematic LFSR division)
    assert len(data) == 231
    par = [0]*NPAR
    for d in data:
        fb = d ^ par[NPAR-1]
        for j in range(NPAR-1, 0, -1):
            par[j] = par[j-1] ^ gmul(g[j], fb)
        par[0] = gmul(g[0], fb)
    return list(data) + par[::-1]

rng = np.random.default_rng(41)
NERR = int(sys.argv[sys.argv.index('--nerr')+1]) if '--nerr' in sys.argv else 12

cws = []; data_blocks = []
for k in range(I):
    data = rng.integers(0, 256, 231).astype(int).tolist()
    cws.append(rs_encode(data)); data_blocks.append(data)

cadu = [0]*(255*I)
for i in range(255):
    for k in range(I):
        cadu[i*I + k] = cws[k][i]

cadu_err = list(cadu); truth_errs = []
for k in range(I):
    pos = rng.choice(255, NERR, replace=False)
    for p in pos:
        idx = p*I + k
        cadu_err[idx] = (cadu_err[idx] ^ int(rng.integers(1, 256))) & 0xFF
    truth_errs.append(sorted(int(p) for p in pos))

np.array(cadu_err, np.uint8).tofile('/tmp/rs41_rs_in.s8')
json.dump({"I": I, "npar": NPAR, "fcr": FCR, "nerr": NERR,
           "data_blocks": data_blocks, "truth_err_pos": truth_errs},
          open('/tmp/rs41_rs_truth.json', 'w'))
print(f"RS(255,231) x I={I} interleave, {NERR} byte errors/codeword injected, "
      f"{len(cadu_err)} bytes, field 0x{PRIM:X} FCR={FCR}")
