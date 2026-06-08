#!/usr/bin/env python3
# Reference vector for src/rs.rail — Reed-Solomon RS(255,223) error correction with
# CCSDS I=4 interleaving, the block AFTER frame sync + derandomization in LRPT.
#
# A CCSDS CADU carries 4 interleaved RS(255,223) codewords (interleave depth I=4):
# the 1020-byte RS block is the column-wise interleave of 4 codewords of 255 bytes each
# (223 data + 32 parity). The receiver de-interleaves into 4 codewords and RS-corrects
# each (up to 16 byte errors per codeword).
#
# We use the CONVENTIONAL (Berlekamp) RS field here, NOT the CCSDS dual-basis byte
# representation -- the dual-basis is a fixed 8x8 GF(2) change-of-basis applied to every
# byte before/after, an orthogonal add-on. The core algebra (GF(256), poly 0x11D,
# generator with first-consecutive-root FCR=1) is identical. We document this honestly;
# the rung proves the RS error-correction engine + the I=4 interleave.
import numpy as np, sys, json

# GF(256) with primitive poly 0x11D (x^8+x^4+x^3+x^2+1), alpha=2. (Conventional RS field.)
PRIM = 0x11D
exp = [0]*512; log = [0]*256
x = 1
for i in range(255):
    exp[i] = x; log[x] = i
    x <<= 1
    if x & 0x100: x ^= PRIM
for i in range(255, 512): exp[i] = exp[i-255]
def gmul(a,b):
    if a==0 or b==0: return 0
    return exp[(log[a]+log[b])%255]

NPAR = 32   # RS(255,223): 32 parity bytes, corrects up to 16 byte errors
FCR  = 1    # first consecutive root alpha^1
# generator poly g(x) = prod_{i=0}^{NPAR-1} (x - alpha^(FCR+i))
g = [1]
for i in range(NPAR):
    root = exp[(FCR+i)%255]
    ng = [0]*(len(g)+1)
    for j in range(len(g)):
        ng[j]   ^= gmul(g[j], root)
        ng[j+1] ^= g[j]
    g = ng

def rs_encode(data):    # data: 223 bytes -> 255-byte codeword (systematic, LFSR division)
    assert len(data)==223
    # g[0..NPAR] with g[NPAR]=1 (monic). LFSR remainder of data*x^NPAR by g.
    par = [0]*NPAR
    for d in data:
        fb = d ^ par[NPAR-1]
        for j in range(NPAR-1, 0, -1):
            par[j] = par[j-1] ^ gmul(g[j], fb)
        par[0] = gmul(g[0], fb)
    return list(data) + par[::-1]   # parity emitted high-order first

rng = np.random.default_rng(13)
I = 4
NERR = int(sys.argv[sys.argv.index('--nerr')+1]) if '--nerr' in sys.argv else 12  # per codeword (<=16)

cws = []        # the 4 clean codewords
data_blocks = []
for k in range(I):
    data = rng.integers(0,256,223).astype(int).tolist()
    cw = rs_encode(data)
    cws.append(cw); data_blocks.append(data)

# interleave column-wise: cadu[i*I + k] = cws[k][i]  (i in 0..254, k in 0..3)
cadu = [0]*(255*I)
for i in range(255):
    for k in range(I):
        cadu[i*I + k] = cws[k][i]

# inject up to NERR byte errors into each de-interleaved codeword (random positions/vals)
cadu_err = list(cadu)
truth_errs = []
for k in range(I):
    pos = rng.choice(255, NERR, replace=False)
    for p in pos:
        idx = p*I + k
        bad = (cadu_err[idx] ^ int(rng.integers(1,256))) & 0xFF
        cadu_err[idx] = bad
    truth_errs.append(sorted(int(p) for p in pos))

np.array(cadu_err, np.uint8).tofile('/tmp/rs_in.s8')
json.dump({"I":I,"npar":NPAR,"nerr":NERR,
           "data_blocks":data_blocks,         # the 223-byte payloads, ground truth
           "truth_err_pos":truth_errs},
          open('/tmp/rs_truth.json','w'))
print(f"RS(255,223) x I={I} interleave, {NERR} byte errors/codeword injected, "
      f"{len(cadu_err)} CADU bytes, field poly 0x{PRIM:X} FCR={FCR} (conventional)")
