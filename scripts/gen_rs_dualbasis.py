#!/usr/bin/env python3
# Reference vector for src/rs_dualbasis.rail -- RS(255,223) CCSDS with the dual-basis byte
# transform. Encodes a known message, converts to the dual basis (CCSDS wire format), I=4
# column-interleaves, injects <=16 byte errors per codeword. The Rail decoder must convert
# dual->conventional, RS-correct, and recover the original message bytes.
#
# FIELD: GF(256), poly 0x11D (x^8+x^4+x^3+x^2+1), alpha=2, FCR=1 -- the CONVENTIONAL RS field.
# DUAL BASIS: a fixed invertible 8x8 GF(2) change of basis (delta=0x7b trace-dual) satisfying
# the 4 CCSDS canonical anchors (0x00<->0x00, conv 0x01<->dual 0x7b, conv 0x55<->dual 0xc1,
# conv 0xff<->dual 0xa3) and bijective over all 256 values. This is the SAME transform the
# Rail module builds from its 8-byte column vectors. HONESTY: the anchors are rank-deficient
# (cannot uniquely pin a rank-8 map), so this is a structurally-valid CCSDS dual basis but is
# NOT yet byte-verified against Karn libfec's exact Taltab/Tal1tab -- that final wire-bit match
# is pinned by a real Meteor-M2 capture (the remaining step). What this vector PROVES: the
# transform is a clean invertible bijection and end-to-end recovery works through <=16 errors.
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
def trace(z):
    acc = 0; p = z
    for _ in range(8):
        acc ^= p; p = gmul(p, p)
    return acc & 1

# --- dual-basis transform (must match src/rs_dualbasis.rail exactly) ---
# conv->dual columns C2D[i] = image of conventional basis vector e_i; dual->conv = inverse.
DELTA = 0x7b
PERM = (3, 1, 0, 5, 6, 7, 4, 2)
def conv_to_dual(z):
    cs = [trace(gmul(gmul(DELTA, z), exp[j])) for j in range(8)]
    b = 0
    for j in range(8): b |= (cs[j] << PERM[j])
    return b
Taltab = [conv_to_dual(c) for c in range(256)]          # conv -> dual
assert len(set(Taltab)) == 256, "conv->dual not bijective"
Tal1tab = [0]*256
for c in range(256): Tal1tab[Taltab[c]] = c             # dual -> conv
# round-trip + anchor sanity (the same acceptance test the Rail module runs)
assert all(Tal1tab[Taltab[v]] == v and Taltab[Tal1tab[v]] == v for v in range(256))
for conv, dual in [(0x00,0x00),(0x01,0x7b),(0x55,0xc1),(0xff,0xa3)]:
    assert Taltab[conv] == dual, f"anchor conv 0x{conv:02x} -> 0x{Taltab[conv]:02x} != 0x{dual:02x}"

NPAR = 32; FCR = 1
g = [1]
for i in range(NPAR):
    root = exp[(FCR+i) % 255]
    ng = [0]*(len(g)+1)
    for j in range(len(g)):
        ng[j]   ^= gmul(g[j], root)
        ng[j+1] ^= g[j]
    g = ng

def rs_encode(data):   # 223 conventional bytes -> 255-byte conventional codeword (systematic)
    assert len(data) == 223
    par = [0]*NPAR
    for d in data:
        fb = d ^ par[NPAR-1]
        for j in range(NPAR-1, 0, -1):
            par[j] = par[j-1] ^ gmul(g[j], fb)
        par[0] = gmul(g[0], fb)
    return list(data) + par[::-1]

rng = np.random.default_rng(13)
I = 4
NERR = int(sys.argv[sys.argv.index('--nerr')+1]) if '--nerr' in sys.argv else 12  # per codeword (<=16)

cws_dual = []                 # 4 codewords in DUAL basis (wire format)
data_blocks_dual = []         # the 223-byte payloads, in DUAL basis (ground truth for the check)
for k in range(I):
    data_conv = rng.integers(0, 256, 223).astype(int).tolist()   # conventional message bytes
    cw_conv = rs_encode(data_conv)                               # conventional codeword
    cw_dual = [Taltab[b] for b in cw_conv]                       # convert whole codeword to dual
    cws_dual.append(cw_dual)
    data_blocks_dual.append([Taltab[b] for b in data_conv])      # original data, in dual basis

# I=4 column interleave on the DUAL-basis codewords: cadu[i*I + k] = cws_dual[k][i]
cadu = [0]*(255*I)
for i in range(255):
    for k in range(I):
        cadu[i*I + k] = cws_dual[k][i]

# inject up to NERR byte errors per de-interleaved codeword (random positions/vals)
cadu_err = list(cadu)
truth_errs = []
for k in range(I):
    pos = rng.choice(255, NERR, replace=False)
    for p in pos:
        idx = p*I + k
        cadu_err[idx] = (cadu_err[idx] ^ int(rng.integers(1, 256))) & 0xFF
    truth_errs.append(sorted(int(p) for p in pos))

np.array(cadu_err, np.uint8).tofile('/tmp/rs_in.s8')
json.dump({"I": I, "npar": NPAR, "nerr": NERR,
           "data_blocks_dual": data_blocks_dual,   # 223-byte DUAL-basis payloads, ground truth
           "truth_err_pos": truth_errs},
          open('/tmp/rs_dualbasis_truth.json', 'w'))
print(f"RS(255,223) dual-basis x I={I} interleave, {NERR} byte errors/codeword injected, "
      f"{len(cadu_err)} CADU bytes (dual basis), field 0x{PRIM:X} FCR={FCR}, delta=0x{DELTA:02X}")
