#!/usr/bin/env python3
# ===========================================================================
# LRPT rung: lrpt_chain_gen — PYTHON end-to-end SYNTHETIC Meteor-M2 LRPT chain
# generator. Builds the FULL transmit chain from a small source image down to
# int8 baseband IQ, so the whole receive chain is testable offline against a
# known ground-truth image. This is the "gen.py" for the LRPT chain: it ties
# together every stage the individual rungs (rrc / gardner / qpsk / framesync /
# viterbi / derand / rs / vcdu / dct) reverse, in the exact same parameters.
#
# TRANSMIT CHAIN (each stage's matching receive rung in [ ]):
#   source 8x8 image blocks
#     -> level-shift -128, forward 2D DCT-II, JPEG-luma quantize, zig-zag   [dct.rail]
#     -> pack zig-zag int16 coeff blocks into CCSDS space packets (APID 64)
#     -> assemble VCDU (6B primary hdr + 2B M-PDU hdr w/ FHP + packet zone)  [vcdu.rail]
#     -> RS(255,223) systematic encode each of 4 codewords, I=4 interleave   [rs.rail]
#     -> CCSDS pseudo-randomize (additive PN, h(x)=x^8+x^7+x^5+x^3+1)        [derand.rail]
#     -> prepend 32-bit ASM 0x1ACFFC1D  -> CADU bit stream                   [framesync.rail]
#     -> convolutional encode r=1/2 K=7 (G1=0o171, G2=0o133), zero-flush     [viterbi.rail]
#     -> map coded bits to Gray QPSK symbols (2 bits/symbol)                 [qpsk.rail / gardner.rail]
#     -> RRC pulse-shape (beta=0.6, sps=4)                                   [rrc.rail]
#     -> quantize to int8 interleaved IQ  -> /tmp/lrpt_chain.s8
#
# Ground truth (the source image + every intermediate) is written to
# /tmp/lrpt_chain_truth.json so check_lrpt_chain_gen.py can run the inverse
# chain and compare the recovered image to truth. HONESTY: this is a SYNTHETIC
# vector (numpy-generated), as expected for the rung; no real Meteor pass is
# decoded and no attestation is produced. The RS field is the CONVENTIONAL
# (Berlekamp) field, NOT the CCSDS dual-basis byte representation (an orthogonal
# fixed change-of-basis); the conv/RS/PN/ASM/DCT params match the per-rung gens.
# ===========================================================================
import numpy as np, sys, json

# ---------------------------------------------------------------- parameters
SPS  = 4        # samples/symbol for RRC (matches rrc.rail / gardner test)
BETA = 0.6      # RRC roll-off (matches rrc.rail)
SPAN = 4        # RRC span in symbols each side
G1   = 0o171    # 121  conv generator 1 (matches viterbi gen)
G2   = 0o133    # 91   conv generator 2
ASM  = 0x1ACFFC1D
PRIM = 0x11D    # GF(256) primitive poly (matches rs gen)
NPAR = 32       # RS(255,223): 32 parity bytes
FCR  = 1        # first consecutive root
I_INTER = 4     # RS interleave depth
APID = 64       # Meteor image APID family

# JPEG-luminance quant table (row-major), identical to dct.rail / gen_dct.py
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
# generator poly g(x) = prod (x - alpha^(FCR+i))
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
    return h

def u16(b): return [(b>>8)&0xFF, b&0xFF]
def u24(b): return [(b>>16)&0xFF,(b>>8)&0xFF,b&0xFF]

def make_packet(apid, seqc, payload):
    h  = u16((0<<13)|(0<<12)|(0<<11)|(apid & 0x7FF))    # ver/type/sec=0, APID
    h += u16((3<<14)|(seqc & 0x3FFF))                   # seq flags=3 (unsegmented)
    h += u16((len(payload)-1) & 0xFFFF)                 # data length - 1
    return h + list(payload)

# ===========================================================================
def main():
    rng = np.random.default_rng(23)
    NB = int(sys.argv[sys.argv.index('--nb')+1]) if '--nb' in sys.argv else 3
    snr = float(sys.argv[sys.argv.index('--snr')+1]) if '--snr' in sys.argv else None

    # ---- 1) source image: NB 8x8 blocks (gradient / flat / random) --------
    src_blocks = []
    coeff_blocks_zz = []        # int16 zig-zag quantized coeffs per block
    for k in range(NB):
        if k == 0:
            px = np.add.outer(np.arange(8), np.arange(8)) * 8.0
        elif k == 1:
            px = np.full((8,8), 128.0)
        else:
            px = rng.integers(40,210,(8,8)).astype(float)
        src_blocks.append(px.astype(int).tolist())
        shifted = px - 128.0
        C = dct2(shifted)
        q = np.round(C/QTBL).astype(int)
        qf = q.flatten()
        coeff_blocks_zz.append([int(qf[ZZ[i]]) for i in range(64)])

    # ---- 2) entropy/packet stream: serialize zig-zag coeffs as int16 LE ---
    # (This rung does NOT implement the JPEG entropy coder; it carries the
    #  quantized coeffs as raw int16 LE in the packet payloads — exactly the
    #  form dct.rail consumes. Honestly labeled; the Huffman/RLE stage is the
    #  one upstream piece not modeled here.)
    def coeffs_to_bytes(blk):
        out = []
        for c in blk:
            cu = c & 0xFFFF
            out += [cu & 0xFF, (cu >> 8) & 0xFF]   # little-endian int16
        return out
    # one CCSDS packet per image block (payload = 64 int16 = 128 bytes)
    packets = []
    pkt_truth = []
    for k in range(NB):
        pay = coeffs_to_bytes(coeff_blocks_zz[k])
        seqc = 100 + k
        packets.append(make_packet(APID, seqc, pay))
        pkt_truth.append({"apid":APID, "seq":seqc, "len":len(pay)})
    packet_zone = [b for p in packets for b in p]

    # ---- 3) VCDU: header + M-PDU header (FHP) + packet zone ---------------
    VER=0; SCID=0; VCID=5; VCNT=0x010203
    vcdu_hdr = u16((VER<<14)|(SCID<<6)|VCID) + u24(VCNT) + [0x00]
    FHP = 0                         # first packet starts at the top of the zone
    mpdu_hdr = u16(FHP & 0x7FF)
    vcdu_body = vcdu_hdr + mpdu_hdr + packet_zone   # this is the "frame data" pre-RS

    # ---- 4) build the RS data region: 4 codewords x 223 = 892 data bytes --
    # CADU = ASM(4) + 4*255 interleaved RS bytes (1020) = 1024 bytes.
    # The 892-byte RS data field carries vcdu_body (padded to 892).
    RS_DATA = I_INTER * 223          # 892
    frame_data = list(vcdu_body)
    if len(frame_data) > RS_DATA:
        raise SystemExit(f"vcdu_body {len(frame_data)} > RS data capacity {RS_DATA}; reduce --nb")
    frame_data += [0]*(RS_DATA - len(frame_data))   # pad with zeros

    # split into 4 data blocks of 223, RS-encode each
    cws = []
    for k in range(I_INTER):
        data = frame_data[k*223:(k+1)*223]
        cws.append(rs_encode(data))
    # column-wise interleave: rs_block[i*I + k] = cws[k][i]
    rs_block = [0]*(255*I_INTER)
    for i in range(255):
        for k in range(I_INTER):
            rs_block[i*I_INTER + k] = cws[k][i]

    # ---- 5) CCSDS randomize the 1020-byte RS block -----------------------
    pn = ccsds_pn(len(rs_block))
    rand_block = [(rs_block[i] ^ pn[i]) & 0xFF for i in range(len(rs_block))]

    # ---- 6) CADU = ASM (32 bits) + randomized block -> bit stream --------
    cadu_bits = [(ASM >> (31-k)) & 1 for k in range(32)]
    for byte in rand_block:
        for k in range(8):
            cadu_bits.append((byte >> (7-k)) & 1)
    cadu_bits = np.array(cadu_bits, dtype=np.uint8)

    # ---- 7) convolutional encode r=1/2 K=7 (zero-flush) ------------------
    in_bits = list(cadu_bits) + [0]*6          # 6 zero flush -> terminate
    s = 0; coded = []
    for b in in_bits:
        reg = ((s<<1)|b) & 127
        coded.append(bin(reg & G1).count('1') & 1)
        coded.append(bin(reg & G2).count('1') & 1)
        s = reg & 63
    coded = np.array(coded, dtype=np.uint8)    # 2*(len(cadu_bits)+6) coded bits

    # ---- 8) Gray QPSK map: pair coded bits -> (I,Q) symbols --------------
    # bit0 -> I sign, bit1 -> Q sign, 0->+1 / 1->-1 (matches qpsk gen mapping)
    if len(coded) % 2 == 1:
        coded = np.append(coded, 0)
    bI = coded[0::2]; bQ = coded[1::2]
    symI = np.where(bI == 0, 1.0, -1.0)
    symQ = np.where(bQ == 0, 1.0, -1.0)
    nsym = len(symI)

    # ---- 9) RRC pulse shape (upsample by SPS, convolve with taps) --------
    h = rrc_taps(BETA, SPS, SPAN)
    up_i = np.zeros(nsym * SPS); up_i[::SPS] = symI
    up_q = np.zeros(nsym * SPS); up_q[::SPS] = symQ
    shp_i = np.convolve(up_i, h, mode='same')
    shp_q = np.convolve(up_q, h, mode='same')

    # ---- 10) optional AWGN, then quantize to int8 IQ ---------------------
    A = 64.0                                    # scale; RRC peak ~ a few, fits int8
    sig_i = shp_i * A
    sig_q = shp_q * A
    if snr is not None:
        ps = np.mean(sig_i**2 + sig_q**2)
        sigma = np.sqrt(ps / (2 * 10**(snr/10)))
        sig_i = sig_i + rng.standard_normal(len(sig_i))*sigma
        sig_q = sig_q + rng.standard_normal(len(sig_q))*sigma
    i8 = np.clip(np.round(sig_i), -127, 127).astype(np.int8)
    q8 = np.clip(np.round(sig_q), -127, 127).astype(np.int8)
    iq = np.empty(2*len(i8), np.int8); iq[0::2]=i8; iq[1::2]=q8
    iq.tofile('/tmp/lrpt_chain.s8')

    # ---- ground truth for the checker -----------------------------------
    truth = {
        "params": {"sps":SPS, "beta":BETA, "span":SPAN, "G1":G1, "G2":G2,
                   "asm_hex": f"{ASM:08x}", "prim": PRIM, "npar":NPAR, "fcr":FCR,
                   "I":I_INTER, "apid":APID, "nb":NB, "A":A, "snr":snr},
        "qtbl": [int(x) for x in QTBL.flatten()],
        "zz": ZZ,
        "src_blocks": src_blocks,             # ground-truth image (NB 8x8 blocks)
        "coeff_blocks_zz": coeff_blocks_zz,   # zig-zag quant coeffs (entropy-decoder output)
        "packets": pkt_truth,
        "vcdu": {"scid":SCID,"vcid":VCID,"vcnt":VCNT,"fhp":FHP},
        "rs_data_len": len(vcdu_body),        # real frame-data length before padding
        "frame_data": frame_data,             # the 892-byte RS data field (padded)
        "n_coded_bits": int(len(coded)),
        "n_symbols": int(nsym),
        "n_iq_samples": int(len(i8)),
    }
    json.dump(truth, open('/tmp/lrpt_chain_truth.json','w'))

    print(f"LRPT chain: {NB} img blocks -> {len(packets)} CCSDS pkts -> VCDU "
          f"({len(vcdu_body)}B body) -> RS(255,223)xI=4 -> randomize -> "
          f"ASM+conv r=1/2 K=7 -> {nsym} QPSK syms -> RRC sps={SPS} -> "
          f"{len(i8)} int8 IQ samples -> /tmp/lrpt_chain.s8"
          + (f" (snr={snr}dB)" if snr is not None else " (noiseless)"))

if __name__ == "__main__":
    main()
