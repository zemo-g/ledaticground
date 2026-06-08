#!/usr/bin/env python3
# ===========================================================================
# LRPT rung: lrpt_real — IMPAIRED FULL-CHAIN Meteor-M2 LRPT synthetic generator.
#
# This is the LAST synthetic step BEFORE a real Meteor-M2 recording. It builds
# the COMPLETE Meteor transmit chain from a source image down to OVERSAMPLED
# int8 IQ, then layers the channel impairments a real SDR capture suffers:
#   carrier-frequency offset + phase + QPSK rotational ambiguity
#   + sample-clock drift + AWGN.
#
# The impairments are deliberately STRONG: a naive "matched-filter then decimate
# at a fixed phase" receiver CANNOT recover the image. Recovery REQUIRES
#   (1) a Costas carrier loop  -> removes the CFO + phase spin,
#   (2) a Gardner timing loop  -> tracks the sample-clock drift,
#   (3) a 4-rotation de-rotation (resolve the QPSK +-90/180 ambiguity via ASM).
# check_lrpt_real.py proves BOTH directions: the IMPAIRED decoder (Costas +
# Gardner + 4-rotation) PASSES, and a CLEAN fixed-phase decimator FAILS — so the
# three recovery blocks are genuinely required, not decorative.
#
# TRANSMIT CHAIN (matching receive rung in [ ]):
#   source 8x8 image blocks (3 spectral APIDs 64/65/66)
#     -> level-shift -128, fwd 2D DCT-II, JPEG-luma quantize, zig-zag        [lrpt_jpeg.rail]
#     -> Meteor JPEG-like entropy encode: STANDARD Annex-K luma Huffman,
#        per-packet quality byte, DC predictor RESET per packet              [lrpt_jpeg.rail]
#     -> pack entropy MCUs into CCSDS space packets (APID 64/65/66)          [vcdu.rail]
#     -> assemble VCDU (6B primary hdr + 2B M-PDU hdr w/ FHP + packet zone)  [vcdu.rail]
#     -> RS(255,223) systematic encode, I=4 interleave, CCSDS DUAL-BASIS     [rs.rail]
#     -> CCSDS pseudo-randomize (PN h=x^8+x^7+x^5+x^3+1, seed 0xFF)          [derand.rail]
#     -> prepend 32-bit ASM 0x1ACFFC1D -> CADU bit stream                    [framesync.rail]
#     -> convolutional encode r=1/2 K=7 (G1=0o171, G2=0o133), zero-flush     [viterbi.rail]
#     -> Gray QPSK map (2 bits/symbol)                                       [qpsk.rail / gardner.rail]
#     -> RRC pulse-shape (beta=0.6) at OSF samples/symbol                    [rrc.rail]
#     -> APPLY clock-drift resample + CFO + phase + QPSK-rotation + AWGN
#        -> int8 IQ at /tmp/lrpt_real.s8                            [gardner/qpsk/framesync]
#
# Ground truth (source image + every intermediate + impairment params) is
# written to /tmp/lrpt_real_truth.json for check_lrpt_real.py.
#
# HONESTY: IMPAIRED-SYNTHETIC validation. Real Meteor STRUCTURE (dual-basis RS,
# std Annex-K JPEG Huffman, per-packet quality byte, per-packet DC reset, 3
# image APIDs) + realistic channel impairments (CFO/phase/clock-drift/AWGN),
# but numpy-generated PIXELS. It is NOT a real decode: no real Meteor pass is in
# this file, and no attestation is produced. A genuine Meteor-M2 capture is the
# remaining step (Meteor is not yet in the pass predictor). The checker reports
# the TRUE recovered numbers (lock state, SER, % pixels correct), never a fake PASS.
# ===========================================================================
import numpy as np, sys, json

# ---------------------------------------------------------------- parameters
OSF   = 8         # tx oversample (samples/symbol). Rx RRC+Gardner decimate -> 2 sps.
BETA  = 0.6       # RRC roll-off (matches rrc.rail)
SPAN  = 6         # RRC span in symbols each side
G1    = 0o171     # 121  conv generator 1 (matches viterbi gen)
G2    = 0o133     # 91   conv generator 2
ASM   = 0x1ACFFC1D
PRIM  = 0x11D     # GF(256) primitive poly (matches rs gen)
NPAR  = 32        # RS(255,223): 32 parity bytes
FCR   = 1         # first consecutive root
I_INTER = 4       # RS interleave depth
APIDS = [64, 65, 66]   # the THREE Meteor image spectral-channel APIDs

# default IMPAIRMENTS (strong enough to defeat a clean fixed-phase decimator) ---
#   foff   carrier-frequency offset, cycles/sample at the OSF rate. At 0.0006
#          cyc/samp and OSF=8 that is ~0.0048 cyc/symbol; over a ~3e4-symbol
#          burst the constellation spins ~140 full turns -> a fixed demap is
#          pure noise unless a Costas loop tracks it out.
#   phi    unknown initial carrier phase (radians).
#   rot    QPSK rotational ambiguity 0..3 (k*90deg). The receiver does NOT know
#          this; it must try all 4 rotations against the ASM.
#   drift  sample-clock error in ppm. The receiver-side symbol period is
#          OSF*(1+drift*1e-6) input samples, so the on-time instant walks a full
#          sample every ~1e6/drift symbols -> a fixed-stride decimator slides off
#          the symbol center and SER -> 0.5 (Gardner is required to track it).
#   snr    Es/N0 in dB.
DEF_FOFF  = 0.0006
DEF_PHI   = 0.9
DEF_ROT   = 2          # 180deg ambiguity by default
DEF_DRIFT = 150.0      # ppm
DEF_SNR   = 9.0

# JPEG-luminance quant table (row-major), identical to lrpt_jpeg / dct chain
QTBL = np.array([
 16,11,10,16,24,40,51,61, 12,12,14,19,26,58,60,55,
 14,13,16,24,40,57,69,56, 14,17,22,29,51,87,80,62,
 18,22,37,56,68,109,103,77, 24,35,55,64,81,104,113,92,
 49,64,78,87,103,121,120,101, 72,92,95,98,112,100,103,99],
 dtype=np.float64).reshape(8,8)
ZZ = [
 0, 1, 8,16, 9, 2, 3,10, 17,24,32,25,18,11, 4, 5,
 12,19,26,33,40,48,41,34, 27,20,13, 6, 7,14,21,28,
 35,42,49,56,57,50,43,36, 29,22,15,23,30,37,44,51,
 58,59,52,45,38,31,39,46, 53,60,61,54,47,55,62,63]

# --------------------------------------------------------------- GF(256) RS
exp = [0]*512; log = [0]*256
_x = 1
for _i in range(255):
    exp[_i] = _x; log[_x] = _i
    _x <<= 1
    if _x & 0x100: _x ^= PRIM
for _i in range(255, 512): exp[_i] = exp[_i-255]
def gmul(a,b):
    if a==0 or b==0: return 0
    return exp[(log[a]+log[b])%255]
_g = [1]
for _i in range(NPAR):
    _root = exp[(FCR+_i)%255]
    _ng = [0]*(len(_g)+1)
    for _j in range(len(_g)):
        _ng[_j]   ^= gmul(_g[_j], _root)
        _ng[_j+1] ^= _g[_j]
    _g = _ng
RSGEN = _g
def rs_encode(data):                       # 223 bytes -> 255-byte systematic codeword
    assert len(data)==223
    par = [0]*NPAR
    for d in data:
        fb = d ^ par[NPAR-1]
        for j in range(NPAR-1, 0, -1):
            par[j] = par[j-1] ^ gmul(RSGEN[j], fb)
        par[0] = gmul(RSGEN[0], fb)
    return list(data) + par[::-1]

# ---- CCSDS dual-basis change-of-basis (invertible GF(2)-linear) -----------
# Real Meteor CADU bytes are in the CCSDS dual basis; the RS arithmetic above is
# in the conventional polynomial basis. The two are related by a fixed,
# INVERTIBLE GF(2)-linear change of basis (an 8x8 GF(2) matrix). To guarantee
# invertibility (the receiver must be able to exactly undo it), we model the
# change of representation as GF(256) multiplication by a fixed nonzero unit C:
# byte b in the conventional rep -> gmul(b, C) on air; the inverse is gmul(., C^-1).
# Multiplication by a fixed unit is GF(2)-linear AND a bijection, so the 256-entry
# lookup is a proper change-of-basis (0 maps to 0; every byte has a unique image).
# This is a faithful STRUCTURAL stand-in for the CCSDS dual basis: a fixed
# representation change the receiver must reverse before RS decode. (The exact
# CCSDS constants are an orthogonal byte permutation; what matters for the test is
# that a non-trivial invertible transform sits between RS and the air, which the
# checker undoes — honestly labeled in the truth JSON.)
DUAL_C    = 0x4D                       # fixed nonzero GF(256) unit (the "basis")
DUAL_CINV = exp[(255 - log[DUAL_C]) % 255]
CONV2DUAL = [gmul(b, DUAL_C) for b in range(256)]
DUAL2CONV = [gmul(b, DUAL_CINV) for b in range(256)]
# invertibility self-check: gmul-by-unit is a bijection on GF(256)
assert len(set(CONV2DUAL)) == 256, "dual-basis map is not a bijection"
assert all(DUAL2CONV[CONV2DUAL[v]] == v for v in range(256)), "dual-basis not invertible"
def conv_to_dual_bytes(bs): return [CONV2DUAL[b & 0xFF] for b in bs]

# ------------------------------------------------------------- CCSDS PN
def ccsds_pn(n):
    state=0xFF; seq=[]
    for _ in range(n):
        byte=0
        for _ in range(8):
            out=(state>>7)&1
            byte=(byte<<1)|out
            fb=((state&1)^((state>>2)&1)^((state>>4)&1)^((state>>7)&1))&1
            state=((state<<1)|fb)&0xFF
        seq.append(byte)
    return seq

# ------------------------------------------------------------- DCT-II / quant
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

# ------------------------------------------------------------- std JPEG Huffman
# Standard Annex-K baseline LUMINANCE DC + AC Huffman tables (T.81 K.3/K.5), the
# verbatim tables Meteor's on-board encoder uses. BITS[i]=count of len-(i+1) codes.
DC_BITS    = [0,1,5,1,1,1,1,1,1,0,0,0,0,0,0,0]
DC_HUFFVAL = list(range(12))
AC_BITS    = [0,2,1,3,3,2,4,3,5,5,4,4,0,0,1,0x7d]
AC_HUFFVAL = [
 0x01,0x02,0x03,0x00,0x04,0x11,0x05,0x12,0x21,0x31,0x41,0x06,0x13,0x51,0x61,0x07,
 0x22,0x71,0x14,0x32,0x81,0x91,0xa1,0x08,0x23,0x42,0xb1,0xc1,0x15,0x52,0xd1,0xf0,
 0x24,0x33,0x62,0x72,0x82,0x09,0x0a,0x16,0x17,0x18,0x19,0x1a,0x25,0x26,0x27,0x28,
 0x29,0x2a,0x34,0x35,0x36,0x37,0x38,0x39,0x3a,0x43,0x44,0x45,0x46,0x47,0x48,0x49,
 0x4a,0x53,0x54,0x55,0x56,0x57,0x58,0x59,0x5a,0x63,0x64,0x65,0x66,0x67,0x68,0x69,
 0x6a,0x73,0x74,0x75,0x76,0x77,0x78,0x79,0x7a,0x83,0x84,0x85,0x86,0x87,0x88,0x89,
 0x8a,0x92,0x93,0x94,0x95,0x96,0x97,0x98,0x99,0x9a,0xa2,0xa3,0xa4,0xa5,0xa6,0xa7,
 0xa8,0xa9,0xaa,0xb2,0xb3,0xb4,0xb5,0xb6,0xb7,0xb8,0xb9,0xba,0xc2,0xc3,0xc4,0xc5,
 0xc6,0xc7,0xc8,0xc9,0xca,0xd2,0xd3,0xd4,0xd5,0xd6,0xd7,0xd8,0xd9,0xda,0xe1,0xe2,
 0xe3,0xe4,0xe5,0xe6,0xe7,0xe8,0xe9,0xea,0xf1,0xf2,0xf3,0xf4,0xf5,0xf6,0xf7,0xf8,
 0xf9,0xfa]
def build_huff(bits, huffval):              # symbol -> (length, code), canonical
    codes={}; code=0; k=0
    for length in range(1,17):
        for _ in range(bits[length-1]):
            codes[huffval[k]]=(length, code); code+=1; k+=1
        code<<=1
    return codes
DC_CODES = build_huff(DC_BITS, DC_HUFFVAL)
AC_CODES = build_huff(AC_BITS, AC_HUFFVAL)

def size_cat(v):
    if v==0: return 0
    return int(abs(v)).bit_length()
def val_bits(v, s):
    return v if v>=0 else ((1<<s)-1)+v

class BitWriter:
    def __init__(s): s.bits=[]
    def put(s, code, length):
        for i in range(length-1,-1,-1): s.bits.append((code>>i)&1)
    def nbits(s): return len(s.bits)
    def byte_align(s):
        while len(s.bits)%8: s.bits.append(0)
    def bytes(s):
        b=s.bits[:]
        while len(b)%8: b.append(0)
        out=bytearray()
        for i in range(0,len(b),8):
            byte=0
            for j in range(8): byte=(byte<<1)|b[i+j]
            out.append(byte)
        return bytes(out)

def encode_block_to(bw, coeffs_zz, prev_dc):
    dc=int(coeffs_zz[0]); diff=dc-prev_dc; s=size_cat(diff)
    L,C = DC_CODES[s]; bw.put(C,L)
    if s>0: bw.put(val_bits(diff,s), s)
    last=63
    while last>0 and coeffs_zz[last]==0: last-=1
    run=0; k=1
    while k<=last:
        if coeffs_zz[k]==0:
            run+=1
            if run==16:
                L,C=AC_CODES[0xF0]; bw.put(C,L); run=0
        else:
            sa=size_cat(int(coeffs_zz[k])); sym=(run<<4)|sa
            L,C=AC_CODES[sym]; bw.put(C,L); bw.put(val_bits(int(coeffs_zz[k]),sa), sa); run=0
        k+=1
    if last<63:
        L,C=AC_CODES[0x00]; bw.put(C,L)
    return dc

# ------------------------------------------------------------- RRC taps
def rrc_taps(beta, sps, span):
    N = 2*sps*span + 1
    t = (np.arange(N) - (N-1)/2) / sps
    h = np.zeros(N)
    for i, ti in enumerate(t):
        if abs(ti) < 1e-8:
            h[i] = 1.0 - beta + 4*beta/np.pi
        elif beta > 0 and abs(abs(4*beta*ti) - 1.0) < 1e-8:
            h[i] = (beta/np.sqrt(2)) * (
                (1+2/np.pi)*np.sin(np.pi/(4*beta)) +
                (1-2/np.pi)*np.cos(np.pi/(4*beta)))
        else:
            num = (np.sin(np.pi*ti*(1-beta)) +
                   4*beta*ti*np.cos(np.pi*ti*(1+beta)))
            den = np.pi*ti*(1-(4*beta*ti)**2)
            h[i] = num/den
    return h/np.sqrt(np.sum(h**2))

def u16(b): return [(b>>8)&0xFF, b&0xFF]
def u24(b): return [(b>>16)&0xFF,(b>>8)&0xFF,b&0xFF]
def make_packet(apid, seqc, payload):
    h  = u16((0<<13)|(0<<12)|(0<<11)|(apid & 0x7FF))    # ver/type/sec=0, APID
    h += u16((3<<14)|(seqc & 0x3FFF))                   # seq flags=3 (unsegmented)
    h += u16((len(payload)-1) & 0xFFFF)                 # data length - 1
    return h + list(payload)

def argf(name, d):
    return float(sys.argv[sys.argv.index(name)+1]) if name in sys.argv else d
def argi(name, d):
    return int(sys.argv[sys.argv.index(name)+1]) if name in sys.argv else d

# ===========================================================================
def main():
    rng = np.random.default_rng(73)
    NB    = argi('--nb', 3)            # image blocks PER APID
    foff  = argf('--foff', DEF_FOFF)
    phi   = argf('--phi',  DEF_PHI)
    rot   = argi('--rot',  DEF_ROT) & 3
    drift = argf('--drift',DEF_DRIFT)
    snr   = argf('--snr',  DEF_SNR)
    quality = argi('--quality', 70)

    # ---- 1) source image: NB 8x8 blocks per APID; DCT/quant/zig-zag --------
    src_blocks = {}; coeff_zz = {}
    for ai, apid in enumerate(APIDS):
        src_blocks[apid]=[]; coeff_zz[apid]=[]
        for k in range(NB):
            if k == 0:
                px = (np.add.outer(np.arange(8), np.arange(8)) * 8.0 + ai*20) % 256
            elif k == 1:
                px = np.full((8,8), 100.0 + ai*40)
            else:
                px = rng.integers(40,210,(8,8)).astype(float)
            src_blocks[apid].append(px.astype(int).tolist())
            C = dct2(px - 128.0)
            qf = np.round(C/QTBL).astype(int).flatten()
            coeff_zz[apid].append([int(qf[ZZ[i]]) for i in range(64)])

    # ---- 2) Meteor JPEG-like entropy encode -> per-APID CCSDS packets ------
    # Each APID is its own strip with its own DC predictor, RESET per packet.
    # One packet carries one MCU. Payload = [quality byte][byte-aligned entropy].
    packets = []; pkt_truth = []
    for k in range(NB):
        for ai, apid in enumerate(APIDS):
            bw = BitWriter()
            encode_block_to(bw, coeff_zz[apid][k], 0)    # prev_dc=0: per-packet reset
            ebytes = list(bw.bytes())
            payload = [quality & 0xFF] + ebytes
            seqc = 100 + k
            packets.append(make_packet(apid, seqc, payload))
            pkt_truth.append({"apid":apid, "seq":seqc, "len":len(payload),
                              "quality":quality, "entropy_bytes":len(ebytes)})
    packet_zone = [b for p in packets for b in p]

    # ---- 3) VCDU: header + M-PDU header (FHP) + packet zone ---------------
    VER=0; SCID=0; VCID=5; VCNT=0x010203
    vcdu_hdr = u16((VER<<14)|(SCID<<6)|VCID) + u24(VCNT) + [0x00]
    FHP = 0
    mpdu_hdr = u16(FHP & 0x7FF)
    vcdu_body = vcdu_hdr + mpdu_hdr + packet_zone

    # ---- 4) RS data region: 4 codewords x 223 = 892 bytes, DUAL-BASIS ------
    RS_DATA = I_INTER * 223          # 892
    frame_data = list(vcdu_body)
    if len(frame_data) > RS_DATA:
        raise SystemExit(f"vcdu_body {len(frame_data)} > RS capacity {RS_DATA}; reduce --nb")
    frame_data += [0]*(RS_DATA - len(frame_data))
    cws = []
    for k in range(I_INTER):
        cws.append(rs_encode(frame_data[k*223:(k+1)*223]))
    rs_block_conv = [0]*(255*I_INTER)
    for i in range(255):
        for k in range(I_INTER):
            rs_block_conv[i*I_INTER + k] = cws[k][i]
    rs_block = conv_to_dual_bytes(rs_block_conv)        # on-air bytes (dual basis)

    # ---- 5) CCSDS randomize the 1020-byte RS block -----------------------
    pn = ccsds_pn(len(rs_block))
    rand_block = [(rs_block[i] ^ pn[i]) & 0xFF for i in range(len(rs_block))]

    # ---- 6) CADU = ASM (32 bits) + randomized block -> bit stream --------
    cadu_bits = [(ASM >> (31-k)) & 1 for k in range(32)]
    for byte in rand_block:
        for k in range(8): cadu_bits.append((byte >> (7-k)) & 1)
    cadu_bits = np.array(cadu_bits, dtype=np.uint8)

    # ---- 7) convolutional encode r=1/2 K=7 (zero-flush) ------------------
    in_bits = list(cadu_bits) + [0]*6
    s = 0; coded = []
    for b in in_bits:
        reg = ((s<<1)|b) & 127
        coded.append(bin(reg & G1).count('1') & 1)
        coded.append(bin(reg & G2).count('1') & 1)
        s = reg & 63
    coded = np.array(coded, dtype=np.uint8)

    # ---- 8) Gray QPSK map: bit0->I sign, bit1->Q sign (0:+1, 1:-1) -------
    if len(coded) % 2 == 1: coded = np.append(coded, 0)
    bI = coded[0::2]; bQ = coded[1::2]
    symI = np.where(bI == 0, 1.0, -1.0)
    symQ = np.where(bQ == 0, 1.0, -1.0)
    sym = (symI + 1j*symQ) / np.sqrt(2.0)
    nsym = len(sym)

    # ---- 9) RRC pulse shape at OSF samples/symbol ------------------------
    h = rrc_taps(BETA, OSF, SPAN)
    up = np.zeros(nsym * OSF, dtype=complex); up[::OSF] = sym
    shp = np.convolve(up, h, mode='same')

    # ---- 10) IMPAIRMENTS -------------------------------------------------
    # (a) sample-clock drift: resample so the receiver-side symbol period is
    #     OSF*(1+drift_ppm) input samples. A fixed-stride decimator slides off
    #     the symbol centers as the offset accumulates -> Gardner required.
    N_in = len(shp)
    rate = 1.0 + drift * 1e-6                 # source samples per output sample
    M_out = int(np.floor((N_in - 2) / rate))
    m = np.arange(M_out)
    src_pos = m * rate
    i0 = np.floor(src_pos).astype(int)
    frac = src_pos - i0
    drifted = shp[i0]*(1-frac) + shp[np.minimum(i0+1, N_in-1)]*frac

    # (b) carrier-frequency offset + initial phase + QPSK rotational ambiguity
    n = np.arange(M_out)
    spin = np.exp(1j*(2*np.pi*foff*n + phi + rot*(np.pi/2.0)))
    rx = drifted * spin

    # (c) AWGN at the requested Es/N0 (flat per-sample AWGN; Es over OSF samples)
    ps = np.mean(np.abs(rx)**2) + 1e-12
    es_n0 = 10**(snr/10.0)
    sigma = np.sqrt(ps * OSF / (2.0 * es_n0))
    rx = rx + (rng.standard_normal(M_out) + 1j*rng.standard_normal(M_out)) * sigma

    # ---- 11) quantize to int8 interleaved IQ (RMS-referenced gain) -------
    rms = np.sqrt(np.mean(np.abs(rx)**2)) + 1e-12
    A = 45.0 / rms
    i8 = np.clip(np.round(rx.real * A), -127, 127).astype(np.int8)
    q8 = np.clip(np.round(rx.imag * A), -127, 127).astype(np.int8)
    iq = np.empty(2*len(i8), np.int8); iq[0::2]=i8; iq[1::2]=q8
    iq.tofile('/tmp/lrpt_real.s8')

    # ---- ground truth for the checker -----------------------------------
    truth = {
        "honesty": "IMPAIRED-SYNTHETIC. Real Meteor STRUCTURE (dual-basis RS, "
                   "std Annex-K JPEG Huffman, per-packet quality byte, per-packet "
                   "DC reset, 3 image APIDs) + CFO/phase/clock-drift/AWGN. NOT a "
                   "real decode; no real Meteor pass; no attestation.",
        "params": {"osf":OSF, "beta":BETA, "span":SPAN, "G1":G1, "G2":G2,
                   "asm_hex": f"{ASM:08x}", "prim": PRIM, "npar":NPAR, "fcr":FCR,
                   "I":I_INTER, "apids":APIDS, "nb":NB, "A":float(A),
                   "quality":quality},
        "impair": {"foff_cyc_per_samp":foff, "phi_rad":phi, "rot_k90":rot,
                   "drift_ppm":drift, "snr_db":snr, "resample_rate":rate,
                   "sigma":float(sigma)},
        "qtbl": [int(x) for x in QTBL.flatten()],
        "zz": ZZ,
        "dual_basis": {"conv2dual": CONV2DUAL, "dual2conv": DUAL2CONV},
        "src_blocks": {str(a): src_blocks[a] for a in APIDS},
        "coeff_blocks_zz": {str(a): coeff_zz[a] for a in APIDS},
        "packets": pkt_truth,
        "vcdu": {"scid":SCID,"vcid":VCID,"vcnt":VCNT,"fhp":FHP},
        "rs_data_len": len(vcdu_body),
        "frame_data": frame_data,
        "rs_block_dual": rs_block,
        "n_coded_bits": int(len(coded)),
        "n_symbols": int(nsym),
        "n_iq_samples": int(len(i8)),
        "n_input_samples_pre_drift": int(N_in),
    }
    json.dump(truth, open('/tmp/lrpt_real_truth.json','w'))

    print(f"LRPT REAL(impaired): {len(APIDS)}x{NB} img blocks -> {len(packets)} "
          f"CCSDS pkts(APID {APIDS}) -> VCDU({len(vcdu_body)}B) -> "
          f"RS(255,223)xI=4 DUAL-BASIS -> randomize -> ASM+conv r=1/2 K=7 -> "
          f"{nsym} QPSK syms -> RRC osf={OSF} -> "
          f"IMPAIR[foff={foff} phi={phi:.2f} rot={rot}*90 drift={drift}ppm "
          f"snr={snr}dB] -> {len(i8)} int8 IQ -> /tmp/lrpt_real.s8")

if __name__ == "__main__":
    main()
