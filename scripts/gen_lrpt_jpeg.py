#!/usr/bin/env python3
# Reference vector for src/lrpt_jpeg.rail — the FULL Meteor LRPT JPEG-like image
# decompress path (entropy decode -> dequant -> 2D IDCT -> level-shift -> grayscale).
#
# The dct.rail rung already verifies the numerically-heavy CORE (dequant+IDCT) given
# coefficients AS THEY LEAVE the entropy decoder. THIS rung adds the missing upstream
# stage: the JPEG-baseline ENTROPY DECODER (DC differential + DC/AC Huffman + run-length
# over the zig-zag stream), so the rung goes all the way from a compressed BITSTREAM to
# grayscale pixels. We synthesize the bitstream by running the JPEG-baseline FORWARD
# entropy ENCODER here in Python (forward DCT -> quantize -> zig-zag -> RLE -> Huffman ->
# bitstream); the Rail module reverses the whole thing; ground truth is numpy idct of the
# (lossy) dequantized coeffs, exact to within 1 LSB.
#
# Honesty: this uses a SMALL FIXED canonical-style Huffman code (a real, self-consistent
# prefix code), not the exact Meteor on-board tables. The algorithm — variable-length DC
# size category + AC (run,size) symbols + magnitude-bit value coding + EOB/ZRL — is the
# real JPEG-baseline entropy scheme. Swapping in Meteor's actual code lengths is a table
# change, not an algorithm change. Documented identically in src/lrpt_jpeg.rail.
import numpy as np, sys, json

# JPEG standard luminance quantization table (Meteor uses a similar fixed table family).
QTBL = np.array([
 16,11,10,16,24,40,51,61, 12,12,14,19,26,58,60,55,
 14,13,16,24,40,57,69,56, 14,17,22,29,51,87,80,62,
 18,22,37,56,68,109,103,77, 24,35,55,64,81,104,113,92,
 49,64,78,87,103,121,120,101, 72,92,95,98,112,100,103,99],dtype=np.float64).reshape(8,8)

# zig-zag scan order (JPEG)
ZZ = [
 0, 1, 8,16, 9, 2, 3,10,
17,24,32,25,18,11, 4, 5,
12,19,26,33,40,48,41,34,
27,20,13, 6, 7,14,21,28,
35,42,49,56,57,50,43,36,
29,22,15,23,30,37,44,51,
58,59,52,45,38,31,39,46,
53,60,61,54,47,55,62,63]

def dct2(block):
    M=np.zeros((8,8))
    for u in range(8):
        cu=np.sqrt(1/8) if u==0 else np.sqrt(2/8)
        for v in range(8):
            cv=np.sqrt(1/8) if v==0 else np.sqrt(2/8)
            s=0.0
            for x in range(8):
                for y in range(8):
                    s+=block[x,y]*np.cos((2*x+1)*u*np.pi/16)*np.cos((2*y+1)*v*np.pi/16)
            M[u,v]=cu*cv*s
    return M

def idct2(C):
    out=np.zeros((8,8))
    for x in range(8):
        for y in range(8):
            s=0.0
            for u in range(8):
                cu=np.sqrt(1/8) if u==0 else np.sqrt(2/8)
                for v in range(8):
                    cv=np.sqrt(1/8) if v==0 else np.sqrt(2/8)
                    s+=cu*cv*C[u,v]*np.cos((2*x+1)*u*np.pi/16)*np.cos((2*y+1)*v*np.pi/16)
            out[x,y]=s
    return out

# --- Canonical-style Huffman codes (real, self-consistent prefix codes) ---
# DC: keyed by size category s in 0..11. AC: keyed by (run<<4)|size byte (0x00=EOB,0xF0=ZRL).
# We assign each symbol a fixed code LENGTH then build a canonical prefix code from those
# lengths (the standard JPEG/DEFLATE canonical construction): symbols sorted by (length,
# symbol value), codes assigned in increasing order, +1 per symbol and <<1 when length grows.
# This guarantees a valid prefix code (no codeword prefixes another) for EVERY symbol the
# encoder can emit, with no hand-maintained collision risk. Rail rebuilds the IDENTICAL code
# from the same (symbol,length) list shipped in the truth JSON.
def canonical(length_of):
    # length_of: dict symbol->bit-length. Returns dict symbol->(length,code).
    items = sorted(length_of.items(), key=lambda kv:(kv[1],kv[0]))
    codes={}; code=0; prev_len=items[0][1]
    for sym,L in items:
        code <<= (L-prev_len)
        codes[sym]=(L,code)
        code += 1
        prev_len = L
    return codes

# DC size categories 0..11: short codes for small sizes (most common).
DC_LEN = {0:2,1:2,2:2,3:3,4:4,5:5,6:6,7:7,8:8,9:9,10:10,11:11}
DC_CODES = canonical(DC_LEN)

# AC symbols: EOB(0x00) and all (run 0..15, size 1..11) plus ZRL(0xF0). Give common low-run
# low-size symbols shorter lengths; rarer ones longer. All lengths chosen so the canonical
# code stays a valid prefix code (Kraft inequality satisfied with this length distribution).
AC_LEN = {0x00:2, 0xF0:8}
for run in range(16):
    for size in range(1,12):
        sym=(run<<4)|size
        # heuristic length: base on run+size, clamped to a sane range
        AC_LEN[sym] = min(16, 4 + run//2 + size//2 + (run+size))
AC_CODES = canonical(AC_LEN)

def size_cat(v):
    if v==0: return 0
    return int(np.floor(np.log2(abs(v))))+1

def val_bits(v, s):
    # JPEG magnitude-bit value coding: for s bits, positive v -> v; negative -> v+(2^s -1)
    if v >= 0:
        return v & ((1<<s)-1)
    else:
        return (v + (1<<s) - 1) & ((1<<s)-1)

class BitWriter:
    def __init__(self): self.bits=[]
    def put(self, code, length):
        for i in range(length-1,-1,-1):
            self.bits.append((code>>i)&1)
    def bytes(self):
        b=self.bits[:]
        while len(b)%8: b.append(0)  # pad with zeros
        out=bytearray()
        for i in range(0,len(b),8):
            byte=0
            for j in range(8): byte=(byte<<1)|b[i+j]
            out.append(byte)
        return bytes(out), len(self.bits)

def encode_block(bw, qcoeffs_zz, prev_dc):
    # DC: differential, size + value bits
    dc = int(qcoeffs_zz[0])
    diff = dc - prev_dc
    s = size_cat(diff)
    L,C = DC_CODES[s]
    bw.put(C,L)
    if s>0: bw.put(val_bits(diff,s), s)
    # AC: run-length over coeffs 1..63
    run = 0
    last = 63
    while last>0 and qcoeffs_zz[last]==0: last-=1
    k = 1
    while k <= last:
        if qcoeffs_zz[k]==0:
            run += 1
            if run==16:
                L,C = AC_CODES[0xF0]; bw.put(C,L); run=0  # ZRL
        else:
            sa = size_cat(int(qcoeffs_zz[k]))
            sym = (run<<4)|sa
            L,C = AC_CODES[sym]; bw.put(C,L)
            bw.put(val_bits(int(qcoeffs_zz[k]),sa), sa)
            run=0
        k+=1
    if last < 63:
        L,C = AC_CODES[0x00]; bw.put(C,L)  # EOB
    return dc

rng=np.random.default_rng(31)
NB = int(sys.argv[sys.argv.index('--nb')+1]) if '--nb' in sys.argv else 3
expected=[]; src_blocks=[]
bw = BitWriter()
prev_dc = 0
zz_coeffs=[]
for k in range(NB):
    if k==0:
        px=np.add.outer(np.arange(8),np.arange(8))*8.0       # gradient
    elif k==1:
        px=np.full((8,8),128.0)                              # flat -> DC only
    else:
        px=rng.integers(40,210,(8,8)).astype(float)          # random
    src_blocks.append(px.astype(int).tolist())
    shifted=px-128.0
    C=dct2(shifted)
    q=np.round(C/QTBL).astype(int)
    deq=q*QTBL
    rec=idct2(deq)+128.0
    rec=np.clip(np.round(rec),0,255).astype(int)
    expected.append(rec.tolist())
    qf=q.flatten()
    zzc=[int(qf[ZZ[i]]) for i in range(64)]
    zz_coeffs.append(zzc)
    prev_dc = encode_block(bw, zzc, prev_dc)

payload, nbits = bw.bytes()
open('/tmp/lrpt_jpeg_in.bin','wb').write(payload)

# emit the fixed Huffman tables as flat (symbol,length,code) lists for the Rail side
dc_list=[(s,L,C) for s,(L,C) in sorted(DC_CODES.items())]
ac_list=[(sym,L,C) for sym,(L,C) in sorted(AC_CODES.items())]

# Also write the tables as a space-separated text file that the Rail module parses at
# runtime (same idiom dct.rail uses to load the zig-zag table from a string — avoids a
# 178-arm nested-if). Format: first line "ndc nac nbits nblocks", then for each table a
# flat stream of "symbol length code" triples (DC table first, then AC table).
# NOTE: this synthetic generator uses the RAW QTBL with no quality scaling, which is
# exactly the libjpeg quality=50 case (scale = 200-2*50 = 100 -> Q[i] = std[i]). So it
# emits a single degenerate packet (quality 50, all NB blocks, firstbit 0), keeping it
# compatible with the packet-aware src/lrpt_jpeg.rail (gen_lrpt_real.py is the canonical
# real-Meteor generator; this stays as a quick synthetic smoke).
with open('/tmp/lrpt_jpeg_tables.txt','w') as f:
    f.write(f"{len(dc_list)} {len(ac_list)} {nbits} {NB} 1\n")
    f.write(" ".join(f"{s} {L} {C}" for (s,L,C) in dc_list)+"\n")
    f.write(" ".join(f"{s} {L} {C}" for (s,L,C) in ac_list)+"\n")
    f.write(f"50 {NB} 0\n")   # single packet: quality 50 (=identity qtbl), NB blocks, firstbit 0
json.dump({"nb":NB,"nbits":nbits,"qtbl":[int(x) for x in QTBL.flatten()],"zz":ZZ,
           "expected":expected,"src":src_blocks,"zz_coeffs":zz_coeffs,
           "dc_table":dc_list,"ac_table":ac_list},
          open('/tmp/lrpt_jpeg_truth.json','w'))
print(f"{NB} blocks JPEG-baseline entropy-coded -> /tmp/lrpt_jpeg_in.bin "
      f"({len(payload)} bytes, {nbits} bits)")
