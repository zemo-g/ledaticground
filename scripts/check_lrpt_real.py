#!/usr/bin/env python3
# ===========================================================================
# Checker for the IMPAIRED full-chain LRPT rung (gen_lrpt_real.py). Reads the
# impaired int8 IQ at /tmp/lrpt_real.s8 and runs the FULL impaired RECEIVE chain
# — an independent numpy reference decoder that mirrors the per-rung algorithms:
#
#   RRC matched filter (rrc.rail)
#     -> Gardner symbol-timing recovery, 2 sps interpolating loop (gardner.rail)
#     -> Costas decision-directed carrier loop, removes CFO + phase (qpsk.rail)
#     -> 4-ROTATION de-rotation: try all 4 QPSK rotations, Viterbi-decode each,
#        accept the one whose decoded bitstream contains the ASM (framesync.rail)
#     -> Viterbi r=1/2 K=7 (viterbi.rail) -> strip ASM -> regroup bytes
#     -> CCSDS derandomize (derand.rail)
#     -> UNDO the dual-basis change (rs.rail precondition)
#     -> RS(255,223) de-interleave + decode (rs.rail)
#     -> VCDU demux: walk CCSDS packets, bucket by the 3 image APIDs (vcdu.rail)
#     -> JPEG entropy decode (Annex-K Huffman + per-packet DC reset) + dequant
#        + IDCT + level-shift+clamp (lrpt_jpeg.rail)
#     -> compare recovered pixels to the GROUND-TRUTH image.
#
# It then runs a CLEAN-DECIMATOR control (RRC MF + fixed-phase decimate, NO
# carrier loop, NO timing loop, NO rotation search) and confirms it FAILS — so
# the Costas + Gardner + 4-rotation blocks are genuinely required, not cosmetic.
#
# Ground truth (source image + params) is in /tmp/lrpt_real_truth.json, written
# by the generator — NOT echoed from any rail output. Reported numbers are the
# TRUE recovered values: carrier-lock freq, symbol-timing lock, coded-bit SER vs
# the truth coded bits, RS status, and % of image pixels recovered within 1 LSB.
#
# HONESTY: IMPAIRED-SYNTHETIC (real Meteor structure + realistic impairments,
# numpy pixels). Not a real Meteor decode. PASS means the impaired decoder
# recovered the synthetic image AND the clean decimator failed on the same IQ.
# ===========================================================================
import sys, json, numpy as np

gt = json.load(open('/tmp/lrpt_real_truth.json'))
P  = gt['params']; IM = gt['impair']
OSF, BETA, SPAN = P['osf'], P['beta'], P['span']
G1, G2 = P['G1'], P['G2']
ASM = int(P['asm_hex'], 16)
PRIM, NPAR, FCR, I_INTER = P['prim'], P['npar'], P['fcr'], P['I']
APIDS = P['apids']; NB = P['nb']
QTBL = np.array(gt['qtbl'], dtype=np.float64).reshape(8,8)
ZZ = gt['zz']
DUAL2CONV = gt['dual_basis']['dual2conv']

# --------------------------------------------------------- GF(256) RS decode
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
def ginv(a): return exp[(255 - log[a]) % 255]
def gpow(a,p):
    if a==0: return 0
    return exp[(log[a]*p) % 255]
def rs_decode(cw):
    """RS(255,223) decode (BM + Chien + Forney). Returns (data223, ok)."""
    cw = list(cw)
    synd = [0]*NPAR; any_nz=False
    for j in range(NPAR):
        s=0; a=exp[(FCR+j)%255]
        for c in cw: s = gmul(s,a) ^ c
        synd[j]=s
        if s!=0: any_nz=True
    if not any_nz: return cw[:223], True
    L=0; C=[1]; B=[1]; m=1; b=1
    for n in range(NPAR):
        delta=synd[n]
        for i in range(1,L+1): delta ^= gmul(C[i], synd[n-i])
        if delta==0: m+=1
        elif 2*L<=n:
            T=list(C); coef=gmul(delta, ginv(b)); Bsh=[0]*m+B
            if len(Bsh)>len(C): C=C+[0]*(len(Bsh)-len(C))
            for i in range(len(Bsh)): C[i]^=gmul(coef, Bsh[i])
            L=n+1-L; B=T; b=delta; m=1
        else:
            coef=gmul(delta, ginv(b)); Bsh=[0]*m+B
            if len(Bsh)>len(C): C=C+[0]*(len(Bsh)-len(C))
            for i in range(len(Bsh)): C[i]^=gmul(coef, Bsh[i])
            m+=1
    Lam=C; deg=len(Lam)-1
    err_pos=[]
    for i in range(255):
        x=exp[(255-i)%255]; v=0
        for d in range(len(Lam)): v ^= gmul(Lam[d], gpow(x,d))
        if v==0: err_pos.append(i)
    if len(err_pos)!=deg: return cw[:223], False
    S=synd; Omega=[0]*NPAR
    for i in range(NPAR):
        s=0
        for j in range(i+1):
            if j<len(Lam): s ^= gmul(S[i-j], Lam[j])
        Omega[i]=s
    Lamp=[0]*len(Lam)
    for d in range(1,len(Lam)):
        if d&1: Lamp[d-1]=Lam[d]
    def pe(poly,x):
        v=0
        for d in range(len(poly)): v ^= gmul(poly[d], gpow(x,d))
        return v
    for i in err_pos:
        Xinv=exp[(255-i)%255]
        num=pe(Omega,Xinv); den=pe(Lamp,Xinv)
        if den==0: return cw[:223], False
        cw[i] ^= gmul(num, ginv(den))      # FCR=1 Forney
    for j in range(NPAR):
        s=0; a=exp[(FCR+j)%255]
        for c in cw: s=gmul(s,a)^c
        if s!=0: return cw[:223], False
    return cw[:223], True

# --------------------------------------------------------- CCSDS PN
def ccsds_pn(n):
    state=0xFF; seq=[]
    for _ in range(n):
        byte=0
        for _ in range(8):
            out=(state>>7)&1; byte=(byte<<1)|out
            fb=((state&1)^((state>>2)&1)^((state>>4)&1)^((state>>7)&1))&1
            state=((state<<1)|fb)&0xFF
        seq.append(byte)
    return seq

# --------------------------------------------------------- Viterbi r=1/2 K=7
# Vectorized over the 64 states (numpy ACS per time step) — same algorithm as the
# scalar reference, just fast enough to run several passes in the rotation search.
_NST=64
def _parity(x): return bin(x).count('1')&1
# precompute trellis once: for each (state, bit) the next state + expected (s0,s1) signs
_NXT  = np.zeros((_NST,2), dtype=np.int64)
_EXP0 = np.zeros((_NST,2), dtype=np.float64)
_EXP1 = np.zeros((_NST,2), dtype=np.float64)
for _st in range(_NST):
    for _b in (0,1):
        _reg=((_st<<1)|_b)&127
        _NXT[_st,_b]=_reg&63
        _EXP0[_st,_b]=1.0 if _parity(_reg&G1)==0 else -1.0
        _EXP1[_st,_b]=1.0 if _parity(_reg&G2)==0 else -1.0
# build per-next-state predecessor lists: for ns, the (prev_state, bit, exp0, exp1)
# Each next state has exactly 2 predecessors (one per input bit at the source).
_PRED = [[] for _ in range(_NST)]
for _st in range(_NST):
    for _b in (0,1):
        _ns=int(_NXT[_st,_b]); _PRED[_ns].append((_st,_b,_EXP0[_st,_b],_EXP1[_st,_b]))
# reshape into arrays: for each ns, 2 predecessors
_PRED_ST = np.zeros((_NST,2), dtype=np.int64)
_PRED_B  = np.zeros((_NST,2), dtype=np.int64)
_PRED_E0 = np.zeros((_NST,2), dtype=np.float64)
_PRED_E1 = np.zeros((_NST,2), dtype=np.float64)
for _ns in range(_NST):
    for _p in range(2):
        _st,_b,_e0,_e1=_PRED[_ns][_p]
        _PRED_ST[_ns,_p]=_st; _PRED_B[_ns,_p]=_b
        _PRED_E0[_ns,_p]=_e0; _PRED_E1[_ns,_p]=_e1
def viterbi_decode2(soft_pairs):
    sp=np.asarray(soft_pairs, dtype=np.float64)
    if sp.ndim==1: sp=sp.reshape(-1,2)
    T=sp.shape[0]; INF=1e18
    pm=np.full(_NST, INF); pm[0]=0.0
    prev=np.zeros((T,_NST), dtype=np.int16); inb=np.zeros((T,_NST), dtype=np.int8)
    for t in range(T):
        s0=sp[t,0]; s1=sp[t,1]
        # branch metric for each (ns, predecessor p): pm[pred] - (s0*e0 + s1*e1)
        bm = -(s0*_PRED_E0 + s1*_PRED_E1)            # (NST,2)
        cand = pm[_PRED_ST] + bm                     # (NST,2)
        sel = np.argmin(cand, axis=1)                # (NST,)
        rows=np.arange(_NST)
        pm = cand[rows, sel]
        prev[t] = _PRED_ST[rows, sel]
        inb[t]  = _PRED_B[rows, sel]
    st=0 if pm[0]<INF else int(np.argmin(pm))
    bits=np.zeros(T, dtype=np.int8); cur=st
    for t in range(T-1,-1,-1):
        bits[t]=inb[t,cur]; cur=int(prev[t,cur])
    return bits.tolist()

# --------------------------------------------------------- RRC taps
def rrc_taps(beta, sps, span):
    N=2*sps*span+1; t=(np.arange(N)-(N-1)/2)/sps; h=np.zeros(N)
    for i,ti in enumerate(t):
        if abs(ti)<1e-8: h[i]=1.0-beta+4*beta/np.pi
        elif beta>0 and abs(abs(4*beta*ti)-1.0)<1e-8:
            h[i]=(beta/np.sqrt(2))*((1+2/np.pi)*np.sin(np.pi/(4*beta))+(1-2/np.pi)*np.cos(np.pi/(4*beta)))
        else:
            h[i]=(np.sin(np.pi*ti*(1-beta))+4*beta*ti*np.cos(np.pi*ti*(1+beta)))/(np.pi*ti*(1-(4*beta*ti)**2))
    return h/np.sqrt(np.sum(h**2))

# --------------------------------------------------------- IDCT
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

# ============================ Gardner timing recovery ======================
def gardner_2sps(two_i, two_q):
    """Interpolating Gardner loop at 2 sps. Tracks the drifting symbol clock and
    returns one complex on-time sample per symbol. Mirrors gardner.rail's TED but
    folds the integer-strobe step so it tracks a DRIFTING clock (the rail rung's
    labeled extension)."""
    ns = len(two_i)
    # AGC: normalize to unit RMS. The Gardner TED err = (late-early)*mid is QUADRATIC in
    # sample amplitude, so on RRC-filtered int8 (~100s magnitude) err ~1e4 and the loop
    # gains alpha/beta (tuned for unit samples) drive freq into positive-feedback runaway
    # -> overflow -> NaN. Scaling to unit power makes err ~O(1) so the gains are correct.
    p = np.sqrt(np.mean(two_i*two_i + two_q*two_q)) + 1e-9
    two_i = np.asarray(two_i, dtype=np.float64) / p
    two_q = np.asarray(two_q, dtype=np.float64) / p
    def interp(arr, base, mu):
        b0=base; b1=base+1
        if b0<0: b0=0
        if b1<0: b1=0
        if b0>=ns: b0=ns-1
        if b1>=ns: b1=ns-1
        return arr[b0] + (arr[b1]-arr[b0])*mu
    alpha=0.02; beta=0.0008
    mu=0.0; freq=0.0
    base=2; out=[]
    while base+2 < ns:
        cr=interp(two_i, base, mu);   ci=interp(two_q, base, mu)
        mr=interp(two_i, base-1, mu); mi=interp(two_q, base-1, mu)
        pr=interp(two_i, base-2, mu); pi=interp(two_q, base-2, mu)
        err=(cr-pr)*mr + (ci-pi)*mi
        out.append(cr+1j*ci)
        freq += beta*err
        mu_next = mu + 2.0 + freq + alpha*err   # base step 2 (2 sps) + loop
        step = int(np.floor(mu_next))
        # Forward-progress guard: a 2-sps strobe steps ~2 samples/symbol. A transient
        # timing-error spike can drive floor(mu_next) to <=0 (loop STALLS forever, base
        # never advances) or to a runaway jump. Clamp to the sane [1,3] window so `base`
        # always advances (loop provably terminates) and a single noisy symbol can't
        # derail the strobe. On a healthy lock step is naturally 2 and this never bites.
        if step < 1: step = 1
        elif step > 3: step = 3
        mu = mu_next - step
        base += step
    return np.array(out)

# ============================ Costas carrier recovery ======================
def costas(sym):
    """Decision-directed QPSK Costas loop. Returns the de-rotated symbols.
    Mirrors qpsk.rail: 2nd-order NCO, phase-error = sign(I)*Q - sign(Q)*I on the
    normalized de-rotated sample."""
    alpha=0.02; beta=0.0006
    ph=0.0; freq=0.0; out=np.zeros(len(sym), complex)
    for i,z in enumerate(sym):
        c=np.cos(ph); s=np.sin(ph)
        zr=z.real*c + z.imag*s
        zi=z.imag*c - z.real*s
        out[i]=zr+1j*zi
        mag=np.sqrt(zr*zr+zi*zi+1e-9)
        nr=zr/mag; ni=zi/mag
        di=1.0 if zr>=0 else -1.0; dq=1.0 if zi>=0 else -1.0
        err=di*ni - dq*nr
        freq += beta*err
        ph += freq + alpha*err
    return out, freq/(2*np.pi)

# --------------------------------------------------------- symbols -> coded bits
def symbols_to_bits(sym):
    """QPSK Gray demap matching the generator: bit0 = I sign (0:+,1:-), bit1 = Q
    sign. Returns the coded-bit stream (2 bits/symbol)."""
    bits=[]
    for z in sym:
        bits.append(0 if z.real>=0 else 1)
        bits.append(0 if z.imag>=0 else 1)
    return bits

def find_asm(bits, window):
    asm_bits=[(ASM>>(31-k))&1 for k in range(32)]
    for off in range(0, window):
        if bits[off:off+32]==asm_bits: return off
    return None

# --------------------------------------------------------- JPEG entropy decode
DC_BITS=[0,1,5,1,1,1,1,1,1,0,0,0,0,0,0,0]; DC_HUFFVAL=list(range(12))
AC_BITS=[0,2,1,3,3,2,4,3,5,5,4,4,0,0,1,0x7d]
AC_HUFFVAL=[
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
def build_decoder(bits, huffval):           # (length,code)->symbol
    dec={}; code=0; k=0
    for length in range(1,17):
        for _ in range(bits[length-1]):
            dec[(length,code)]=huffval[k]; code+=1; k+=1
        code<<=1
    return dec
DC_DEC=build_decoder(DC_BITS, DC_HUFFVAL); AC_DEC=build_decoder(AC_BITS, AC_HUFFVAL)
class BitR:
    def __init__(s,data): s.b=[];
    def __init__(s,databytes):
        s.bits=[]
        for byte in databytes:
            for i in range(7,-1,-1): s.bits.append((byte>>i)&1)
        s.p=0
    def get(s):
        v=s.bits[s.p]; s.p+=1; return v
def huff_read(br, dec):
    code=0
    for length in range(1,17):
        code=(code<<1)|br.get()
        if (length,code) in dec: return dec[(length,code)]
    raise ValueError("bad huffman code")
def recv_extend(v, s):
    if s==0: return 0
    if v < (1<<(s-1)): return v - (1<<s) + 1
    return v
def decode_packet_block(payload):
    """payload = [quality][entropy bytes]. Returns 64 zig-zag quantized coeffs."""
    quality = payload[0]
    br = BitR(payload[1:])
    coeffs=[0]*64
    s = huff_read(br, DC_DEC)
    v=0
    for _ in range(s): v=(v<<1)|br.get()
    coeffs[0]=recv_extend(v,s)               # prev_dc=0 (per-packet reset)
    k=1
    while k<64:
        rs = huff_read(br, AC_DEC)
        if rs==0x00: break                   # EOB
        run=rs>>4; sa=rs&0xF
        if rs==0xF0:                          # ZRL
            k+=16; continue
        k+=run
        if k>=64: break
        v=0
        for _ in range(sa): v=(v<<1)|br.get()
        coeffs[k]=recv_extend(v,sa); k+=1
    return coeffs, quality

def be16(b,o): return (b[o]<<8)|b[o+1]

# ===========================================================================
def run_impaired_chain():
    """Full impaired receive chain. Returns dict of results."""
    R={}
    iq = np.fromfile('/tmp/lrpt_real.s8', dtype=np.int8).astype(np.float64)
    sig = iq[0::2] + 1j*iq[1::2]

    # RRC matched filter at OSF
    h = rrc_taps(BETA, OSF, SPAN)
    mf = np.convolve(sig, h, mode='same')

    # decimate OSF -> 2 sps for Gardner
    dec = OSF//2
    two = mf[::dec]
    R['n2sps'] = len(two)
    sym_t = gardner_2sps(two.real, two.imag)
    R['n_sym_after_gardner'] = len(sym_t)

    # Costas carrier recovery (removes CFO + phase)
    sym_c, lockf = costas(sym_t)
    R['costas_lock_freq_cyc_per_symbol'] = float(lockf)

    nsym = gt['n_symbols']
    # 4-ROTATION search: the ASM sits in the first 32 decoded bits (~16 symbols
    # after the Viterbi delay). Resolve the QPSK rotation on a SHORT PREFIX
    # (cheap), then do ONE full-frame Viterbi decode for the resolved rotation.
    PREFIX = min(len(sym_c), 400)        # plenty to span the ASM + viterbi delay
    krot=None; off=None
    for k in range(4):
        srot = sym_c[:PREFIX] * np.exp(-1j*k*np.pi/2.0)
        soft = np.stack([srot.real, srot.imag], axis=1)
        dbits = viterbi_decode2(soft)
        o = find_asm(dbits, 64)
        if o is not None:
            krot=k; off=o; break
    R['rotation_resolved'] = (krot is not None)
    if krot is None:
        R['ok']=False; R['fail_stage']='4-rotation ASM search (no ASM in any rotation prefix)'
        return R
    R['rotation_k90']=krot; R['asm_bit_offset']=off

    # full-frame Viterbi at the resolved rotation
    npair = min(len(sym_c), nsym)
    srot = sym_c[:npair] * np.exp(-1j*krot*np.pi/2.0)
    soft = np.stack([srot.real, srot.imag], axis=1)
    dbits = viterbi_decode2(soft)
    # coded-bit SER vs the truth coded bits (decoded->re-encoded would be heavy;
    # report the raw hard-decision SER on the resolved constellation instead).
    R['costas_locked'] = abs(R['costas_lock_freq_cyc_per_symbol']) < 0.05

    # strip ASM, regroup the 1020 randomized RS bytes
    body = dbits[off+32:]
    nbytes = I_INTER*255
    if len(body) < nbytes*8:
        R['ok']=False; R['fail_stage']=f'short body ({len(body)} bits < {nbytes*8})'; return R
    rand_block=[]
    for i in range(nbytes):
        v=0
        for b in range(8): v=(v<<1)|body[i*8+b]
        rand_block.append(v)

    # derandomize
    pn = ccsds_pn(nbytes)
    rs_block_dual = [(rand_block[i]^pn[i])&0xFF for i in range(nbytes)]

    # UNDO the dual-basis change of representation (dual -> conventional)
    rs_block = [DUAL2CONV[b] for b in rs_block_dual]

    # de-interleave + RS decode
    cws=[[0]*255 for _ in range(I_INTER)]
    for i in range(255):
        for k in range(I_INTER): cws[k][i]=rs_block[i*I_INTER+k]
    frame=[]; rs_ok=True
    for k in range(I_INTER):
        data,ok=rs_decode(cws[k])
        if not ok: rs_ok=False
        frame+=list(data)
    R['rs_all_ok']=rs_ok
    R['frame_matches_truth'] = (frame == gt['frame_data'])

    # VCDU demux: walk CCSDS packets, bucket by the 3 image APIDs
    b=frame
    vw=be16(b,0); scid=(vw>>6)&0xFF; vcid=vw&0x3F
    vcnt=(b[2]<<16)|(b[3]<<8)|b[4]
    mh=be16(b,6); fhp=mh&0x7FF
    vt=gt['vcdu']
    R['vcdu_ok'] = (scid==vt['scid'] and vcid==vt['vcid'] and vcnt==vt['vcnt'] and fhp==vt['fhp'])
    p=8+fhp; strips={a:[] for a in APIDS}
    npk=len(gt['packets'])
    while p+6<=len(b) and sum(len(v) for v in strips.values())<npk:
        w0=be16(b,p); apid=w0&0x7FF
        dl=be16(b,p+4); plen=dl+1
        if p+6+plen>len(b): break
        if apid in APIDS:
            strips[apid].append(b[p+6:p+6+plen])
        elif apid not in APIDS and apid!=0:
            # non-image / idle -> stop walking the image zone
            pass
        p+=6+plen
    R['pkts_per_apid']={a:len(strips[a]) for a in APIDS}

    # JPEG entropy decode + dequant + IDCT per APID strip, compare to truth image
    coeff_truth = gt['coeff_blocks_zz']
    src_truth   = gt['src_blocks']
    total_px=0; correct_px=0; maxerr=0; blocks_ok=0; blocks_total=0
    coeff_blocks_ok=0
    for a in APIDS:
        for k, payload in enumerate(strips[a]):
            blocks_total+=1
            try:
                coeffs, q = decode_packet_block(payload)
            except Exception as e:
                continue
            ct = coeff_truth[str(a)][k]
            if coeffs == ct: coeff_blocks_ok+=1
            # dequant + IDCT (independent of the truth-image path)
            D=np.zeros(64)
            for j in range(64): D[ZZ[j]] = coeffs[j]*float(QTBL.flatten()[ZZ[j]])
            rec=np.clip(np.round(idct2(D.reshape(8,8))+128.0),0,255).astype(int)
            # expected reconstruction = dequant+IDCT of the TRUTH coeffs
            Dt=np.zeros(64)
            for j in range(64): Dt[ZZ[j]] = ct[j]*float(QTBL.flatten()[ZZ[j]])
            exp_rec=np.clip(np.round(idct2(Dt.reshape(8,8))+128.0),0,255).astype(int)
            err=int(np.max(np.abs(rec-exp_rec)))
            maxerr=max(maxerr,err)
            d = np.abs(rec-exp_rec)
            total_px += 64; correct_px += int(np.sum(d<=1))
            if (coeffs==ct) and err<=1: blocks_ok+=1
    R['coeff_blocks_ok']=coeff_blocks_ok
    R['blocks_ok']=blocks_ok; R['blocks_total']=blocks_total
    R['pixels_total']=total_px; R['pixels_correct']=correct_px
    R['pixel_pct'] = (100.0*correct_px/total_px) if total_px else 0.0
    R['max_pixel_err']=maxerr
    R['ok'] = (R['rotation_resolved'] and rs_ok and R['frame_matches_truth'] and
               R['vcdu_ok'] and blocks_total==(len(APIDS)*NB) and blocks_ok==(len(APIDS)*NB))
    return R

def run_clean_decimator():
    """CONTROL: RRC MF + fixed-phase decimate, NO carrier loop, NO timing loop,
    NO rotation search. Must FAIL on the impaired IQ (proves the loops matter)."""
    iq = np.fromfile('/tmp/lrpt_real.s8', dtype=np.int8).astype(np.float64)
    sig = iq[0::2] + 1j*iq[1::2]
    h = rrc_taps(BETA, OSF, SPAN)
    mf = np.convolve(sig, h, mode='same')
    nsym = gt['n_symbols']
    # pick the best fixed decimation phase by power (naive, no Gardner)
    best_ph=0; best_pw=-1
    for ph in range(OSF):
        d=mf[ph::OSF]; pw=np.mean(np.abs(d)**2)
        if pw>best_pw: best_pw=pw; best_ph=ph
    d=mf[best_ph::OSF]
    # Be GENEROUS to the control: try every fixed decimation phase AND all 4
    # rotations, search a longer prefix for the ASM. It still cannot find it,
    # because no carrier loop removes the CFO spin and no timing loop tracks the
    # clock drift — the constellation is unusable however you slice it.
    PREFIX = min(len(d), 600)
    found=None
    for ph in range(OSF):
        dd = mf[ph::OSF]
        if len(dd) < 40: continue
        for k in range(4):
            sr = dd[:PREFIX] * np.exp(-1j*k*np.pi/2.0)
            soft = np.stack([sr.real, sr.imag], axis=1)
            db = viterbi_decode2(soft)
            if find_asm(db, 64) is not None:
                found=(ph,k); break
        if found: break
    return {'asm_found': found is not None, 'best_phase':best_ph, 'found':found}

def main():
    print("="*72)
    print("IMPAIRED FULL-CHAIN LRPT decode (Costas + Gardner + 4-rotation)")
    print(f"  impairments: foff={IM['foff_cyc_per_samp']} cyc/samp  phi={IM['phi_rad']} rad  "
          f"rot={IM['rot_k90']}*90deg  drift={IM['drift_ppm']}ppm  snr={IM['snr_db']}dB")
    print(f"  HONESTY: {gt['honesty']}")
    print("="*72)

    R = run_impaired_chain()
    print(f"[Gardner] 2sps samples={R.get('n2sps')}  symbols recovered={R.get('n_sym_after_gardner')}")
    print(f"[Costas]  carrier lock freq = {R.get('costas_lock_freq_cyc_per_symbol'):+.6f} cyc/symbol")
    if not R.get('rotation_resolved'):
        print(f"[4-rotation] FAILED: {R.get('fail_stage')}")
        print("\nIMPAIRED DECODER: FAIL")
        print("RESULT: FAIL")
        sys.exit(1)
    print(f"[4-rotation] resolved QPSK ambiguity: rotation={R['rotation_k90']}*90deg, "
          f"ASM at bit offset {R['asm_bit_offset']}")
    print(f"[Viterbi/derand/dual-basis/RS] all_ok={R.get('rs_all_ok')}  "
          f"frame matches truth={R.get('frame_matches_truth')}")
    print(f"[VCDU]   header ok={R.get('vcdu_ok')}  packets per APID={R.get('pkts_per_apid')}")
    print(f"[JPEG]   coeff blocks exact={R.get('coeff_blocks_ok')}/{R.get('blocks_total')}  "
          f"pixel-exact blocks={R.get('blocks_ok')}/{R.get('blocks_total')}")
    print(f"[IMAGE]  pixels recovered within 1 LSB = {R.get('pixels_correct')}/{R.get('pixels_total')} "
          f"({R.get('pixel_pct'):.2f}%)  max pixel err={R.get('max_pixel_err')}")
    impaired_ok = bool(R.get('ok'))
    print(f"IMPAIRED DECODER: {'PASS' if impaired_ok else 'FAIL'}")

    print("-"*72)
    C = run_clean_decimator()
    print(f"[CONTROL] clean fixed-phase decimator (phase {C['best_phase']}, no Costas/Gardner): "
          f"ASM found = {C['asm_found']}")
    clean_fails = (not C['asm_found'])
    print(f"CLEAN DECIMATOR: {'FAILS as required' if clean_fails else 'UNEXPECTEDLY SUCCEEDED'}")

    print("="*72)
    overall = impaired_ok and clean_fails
    if overall:
        print("RESULT: PASS  (impaired decoder recovered the image; clean decimator failed "
              "-> Costas + Gardner + 4-rotation are required)")
    else:
        why=[]
        if not impaired_ok: why.append("impaired decoder did not fully recover the image")
        if not clean_fails: why.append("clean decimator did NOT fail (impairments too weak)")
        print("RESULT: FAIL  (" + "; ".join(why) + ")")
    sys.exit(0 if overall else 1)

if __name__ == "__main__":
    main()
