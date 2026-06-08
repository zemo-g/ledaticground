#!/usr/bin/env python3
# MULTI-PACKET reference vector for src/lrpt_jpeg.rail — exercises the REAL Meteor
# per-packet machinery that the single-packet gen_lrpt_jpeg.py smoke leaves untested:
#
#   * MULTIPLE image packets, each with a DIFFERENT quality byte -> the rail derives a
#     different runtime quant table per packet via meteor_qtbl (libjpeg quality law).
#   * PER-PACKET DC PREDICTOR RESET — each packet is independently decodable, so the DC
#     differential predictor restarts at 0 at every packet boundary.
#   * BYTE-ALIGNED packet re-seek — each packet's entropy stream starts on a byte
#     boundary; the rail re-seeks its bit reader to the packet's `firstbit`. We pad the
#     bitstream to a byte boundary before each packet and record that bit offset.
#
# Qualities are deliberately chosen to span BOTH branches of the quality law
# (q<50 -> scale=5000/q ; q>=50 -> scale=200-2q), so the runtime quant derivation is
# tested on each side, not just the q=50 identity case.
#
# Ground truth (expected pixels) is computed here with the SAME INTEGER quant table the
# rail derives (meteor_qtbl_int below is a byte-for-byte mirror of src/lrpt_jpeg.rail's
# meteor_qtbl), never echoed from rail output. A pixel match therefore proves the rail
# derived the right per-packet table, reset DC correctly, and re-seeked to each packet.
#
# Honesty: SYNTHETIC structural test (standard Annex-K-family quant base, canonical-style
# Huffman, numpy pixels) — same disclosure as gen_lrpt_jpeg.py. Validate with the existing
# scripts/check_lrpt_jpeg.py (packet-agnostic: compares BLK<k> pixels to truth['expected']).
import numpy as np, sys, json

# Standard luminance quantization BASE table (the q=50 reference; meteor_qtbl scales it).
QTBL = np.array([
 16,11,10,16,24,40,51,61, 12,12,14,19,26,58,60,55,
 14,13,16,24,40,57,69,56, 14,17,22,29,51,87,80,62,
 18,22,37,56,68,109,103,77, 24,35,55,64,81,104,113,92,
 49,64,78,87,103,121,120,101, 72,92,95,98,112,100,103,99],dtype=np.int64).reshape(8,8)

ZZ = [
 0, 1, 8,16, 9, 2, 3,10, 17,24,32,25,18,11, 4, 5,
12,19,26,33,40,48,41,34, 27,20,13, 6, 7,14,21,28,
35,42,49,56,57,50,43,36, 29,22,15,23,30,37,44,51,
58,59,52,45,38,31,39,46, 53,60,61,54,47,55,62,63]

# ---- byte-for-byte mirror of src/lrpt_jpeg.rail meteor_qtbl (INTEGER arithmetic) ----
#   qclamp_q q = clamp(q,1,100)
#   qscale  q  = (q<50) ? 5000//q : 200 - 2*q          (integer)
#   Q[j]       = clamp( (base*scale + 50)//100 , 1, 255 )
def qscale_int(q):
    qq = 1 if q <= 0 else (100 if q > 100 else q)
    return (5000 // qq) if qq < 50 else (200 - 2 * qq)
def meteor_qtbl_int(quality):
    sc = qscale_int(quality)
    Q = np.empty((8,8), dtype=np.int64)
    for x in range(8):
        for y in range(8):
            v = (int(QTBL[x,y]) * sc + 50) // 100
            Q[x,y] = 1 if v < 1 else (255 if v > 255 else v)
    return Q

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

# ---- canonical Huffman (identical construction to gen_lrpt_jpeg.py) ----
def canonical(length_of):
    items=sorted(length_of.items(), key=lambda kv:(kv[1],kv[0]))
    codes={}; code=0; prev=items[0][1]
    for sym,L in items:
        code <<= (L-prev); codes[sym]=(L,code); code+=1; prev=L
    return codes
DC_LEN={0:2,1:2,2:2,3:3,4:4,5:5,6:6,7:7,8:8,9:9,10:10,11:11}
DC_CODES=canonical(DC_LEN)
AC_LEN={0x00:2, 0xF0:8}
for run in range(16):
    for size in range(1,12):
        AC_LEN[(run<<4)|size]=min(16, 4 + run//2 + size//2 + (run+size))
AC_CODES=canonical(AC_LEN)

def size_cat(v):
    if v==0: return 0
    return int(np.floor(np.log2(abs(v))))+1
def val_bits(v,s):
    return (v & ((1<<s)-1)) if v>=0 else ((v+(1<<s)-1) & ((1<<s)-1))

class BitWriter:
    def __init__(s): s.bits=[]
    def put(s,code,length):
        for i in range(length-1,-1,-1): s.bits.append((code>>i)&1)
    def align(s):            # pad to a byte boundary; returns the new (aligned) bit length
        while len(s.bits)%8: s.bits.append(0)
        return len(s.bits)
    def bytes(s):
        b=s.bits[:]
        while len(b)%8: b.append(0)
        out=bytearray()
        for i in range(0,len(b),8):
            byte=0
            for j in range(8): byte=(byte<<1)|b[i+j]
            out.append(byte)
        return bytes(out), len(s.bits)

def encode_block(bw, zz_q, prev_dc):
    dc=int(zz_q[0]); diff=dc-prev_dc; s=size_cat(diff)
    L,C=DC_CODES[s]; bw.put(C,L)
    if s>0: bw.put(val_bits(diff,s), s)
    last=63
    while last>0 and zz_q[last]==0: last-=1
    run=0; k=1
    while k<=last:
        if zz_q[k]==0:
            run+=1
            if run==16:
                L,C=AC_CODES[0xF0]; bw.put(C,L); run=0
        else:
            sa=size_cat(int(zz_q[k])); L,C=AC_CODES[(run<<4)|sa]; bw.put(C,L)
            bw.put(val_bits(int(zz_q[k]),sa), sa); run=0
        k+=1
    if last<63:
        L,C=AC_CODES[0x00]; bw.put(C,L)   # EOB
    return dc

# --- packet plan: qualities spanning both quality-law branches ---
# default: 4 packets, qualities {80, 30, 92, 50}; 2 blocks each (8 blocks total).
def parse_packets():
    if '--packets' in sys.argv:
        spec=sys.argv[sys.argv.index('--packets')+1]
        return [(int(q),int(n)) for q,n in (p.split(':') for p in spec.split(','))]
    return [(80,2),(30,2),(92,2),(50,2)]
PKTS=parse_packets()

rng=np.random.default_rng(137)
bw=BitWriter()
expected=[]; src_blocks=[]; pkt_dir=[]; gk=0
for (qual,nblk) in PKTS:
    Q=meteor_qtbl_int(qual)
    firstbit=bw.align()                 # byte-align, record this packet's first bit offset
    prev_dc=0                           # PER-PACKET DC reset
    for _ in range(nblk):
        kind=gk%3
        if kind==0:   px=np.add.outer(np.arange(8),np.arange(8))*8.0      # gradient
        elif kind==1: px=np.full((8,8),120.0+gk)                          # near-flat (DC only)
        else:         px=rng.integers(35,215,(8,8)).astype(float)         # random
        src_blocks.append(px.astype(int).tolist())
        C=dct2(px-128.0)
        q=np.round(C/Q).astype(int)     # quantize with THIS packet's integer table
        deq=q*Q
        rec=np.clip(np.round(idct2(deq))+128.0,0,255).astype(int)
        expected.append(rec.tolist())
        qf=q.flatten(); zzc=[int(qf[ZZ[i]]) for i in range(64)]
        prev_dc=encode_block(bw, zzc, prev_dc)
        gk+=1
    pkt_dir.append((qual,nblk,firstbit))

payload, nbits = bw.bytes()
open('/tmp/lrpt_jpeg_in.bin','wb').write(payload)

dc_list=[(s,L,C) for s,(L,C) in sorted(DC_CODES.items())]
ac_list=[(sym,L,C) for sym,(L,C) in sorted(AC_CODES.items())]
NB=gk
with open('/tmp/lrpt_jpeg_tables.txt','w') as f:
    f.write(f"{len(dc_list)} {len(ac_list)} {nbits} {NB} {len(PKTS)}\n")
    f.write(" ".join(f"{s} {L} {C}" for (s,L,C) in dc_list)+"\n")
    f.write(" ".join(f"{s} {L} {C}" for (s,L,C) in ac_list)+"\n")
    for (qual,nblk,fb) in pkt_dir:
        f.write(f"{qual} {nblk} {fb}\n")
json.dump({"nb":NB,"nbits":nbits,"zz":ZZ,"expected":expected,"src":src_blocks,
           "packets":pkt_dir,"dc_table":dc_list,"ac_table":ac_list},
          open('/tmp/lrpt_jpeg_truth.json','w'))
print(f"{len(PKTS)} packets {[ (q,n) for (q,n) in PKTS ]} -> {NB} blocks, "
      f"{len(payload)} bytes, {nbits} bits -> /tmp/lrpt_jpeg_in.bin")
print("  qualities span both quality-law branches: "
      + ", ".join(f"q{q}:scale={qscale_int(q)}" for (q,_) in PKTS))
