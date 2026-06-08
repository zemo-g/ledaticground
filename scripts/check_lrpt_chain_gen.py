#!/usr/bin/env python3
# ===========================================================================
# Checker for the LRPT rung lrpt_chain_gen. Reads the synthetic IQ produced by
# gen_lrpt_chain_gen.py (/tmp/lrpt_chain.s8) and runs the FULL reverse chain
# (an INDEPENDENT reference decoder, mirroring the per-rung algorithms) to
# recover the source image, then compares pixel-exact to ground truth.
#
# This proves the generator emits a self-consistent, end-to-end decodable
# LRPT bitstream: image == decode(generate(image)). Ground truth is the source
# image stored in /tmp/lrpt_chain_truth.json by the generator (NOT echoed from
# any rail output). PASS criterion: every recovered 8x8 block matches the
# dequant+IDCT reconstruction of the truth coeffs within 1 LSB (the only loss
# is DCT quantization, identical to dct.rail / gen_dct.py), AND the recovered
# CADU data is bit-exact through Viterbi/derand/RS/VCDU.
#
# The reference decoder here is plain numpy/python — it is the oracle that the
# rail rungs (rrc/gardner/qpsk/framesync/viterbi/derand/rs/vcdu/dct) reproduce
# stage by stage; running it confirms the chain the generator built is valid.
# ===========================================================================
import sys, json, numpy as np

gt = json.load(open('/tmp/lrpt_chain_truth.json'))
P  = gt['params']
SPS, BETA, SPAN = P['sps'], P['beta'], P['span']
G1, G2 = P['G1'], P['G2']
ASM = int(P['asm_hex'], 16)
PRIM, NPAR, FCR, I_INTER = P['prim'], P['npar'], P['fcr'], P['I']
NB = P['nb']
QTBL = np.array(gt['qtbl'], dtype=np.float64).reshape(8,8)
ZZ = gt['zz']

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
    """RS(255,223) decode (Berlekamp-Massey + Chien + Forney). Returns (data223, ok)."""
    cw = list(cw)
    # syndromes S_j = r(alpha^(FCR+j)), j=0..NPAR-1
    synd = [0]*NPAR
    any_nz = False
    for j in range(NPAR):
        s = 0
        a = exp[(FCR+j) % 255]
        for c in cw:
            s = gmul(s, a) ^ c
        synd[j] = s
        if s != 0: any_nz = True
    if not any_nz:
        return cw[:223], True
    # Berlekamp-Massey
    L = 0; C = [1]; B = [1]; m = 1; b = 1
    for n in range(NPAR):
        delta = synd[n]
        for i in range(1, L+1):
            delta ^= gmul(C[i], synd[n-i])
        if delta == 0:
            m += 1
        elif 2*L <= n:
            T = list(C)
            coef = gmul(delta, ginv(b))
            Bsh = [0]*m + B
            if len(Bsh) > len(C): C = C + [0]*(len(Bsh)-len(C))
            for i in range(len(Bsh)):
                C[i] ^= gmul(coef, Bsh[i])
            L = n+1-L; B = T; b = delta; m = 1
        else:
            coef = gmul(delta, ginv(b))
            Bsh = [0]*m + B
            if len(Bsh) > len(C): C = C + [0]*(len(Bsh)-len(C))
            for i in range(len(Bsh)):
                C[i] ^= gmul(coef, Bsh[i])
            m += 1
    Lam = C
    deg = len(Lam)-1
    # Chien search: roots of Lam are alpha^{-i} -> error at position i
    err_pos = []
    for i in range(255):
        x = exp[(255 - i) % 255]  # alpha^{-i}
        v = 0
        for d in range(len(Lam)):
            v ^= gmul(Lam[d], gpow(x, d))
        if v == 0:
            err_pos.append(i)
    if len(err_pos) != deg:
        return cw[:223], False
    # Forney: error magnitudes. Omega(x) = S(x)*Lam(x) mod x^NPAR
    S = synd
    Omega = [0]*(NPAR)
    for i in range(NPAR):
        s = 0
        for j in range(i+1):
            if j < len(Lam):
                s ^= gmul(S[i-j], Lam[j])
        Omega[i] = s
    # Lam'(x) -> formal derivative (drop even-degree terms in GF(2))
    Lamp = [0]*(len(Lam))
    for d in range(1, len(Lam)):
        if d & 1:
            Lamp[d-1] = Lam[d]
    def poly_eval(poly, x):
        v = 0
        for d in range(len(poly)):
            v ^= gmul(poly[d], gpow(x, d))
        return v
    for i in err_pos:
        Xi = exp[i % 255]          # alpha^i (error locator value)
        Xinv = exp[(255 - i) % 255]
        num = poly_eval(Omega, Xinv)
        den = poly_eval(Lamp, Xinv)
        if den == 0:
            return cw[:223], False
        # FCR-general Forney: e = X^(1-FCR) * Omega(Xinv)/Lam'(Xinv)
        mag = gmul(gpow(Xi, 1-FCR if FCR<=1 else 0), gmul(num, ginv(den)))
        if FCR == 1:
            mag = gmul(num, ginv(den))
        cw[i] ^= mag
    # verify syndromes clean
    for j in range(NPAR):
        s = 0; a = exp[(FCR+j) % 255]
        for c in cw:
            s = gmul(s, a) ^ c
        if s != 0:
            return cw[:223], False
    return cw[:223], True

# --------------------------------------------------------- CCSDS PN
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

# --------------------------------------------------------- Viterbi r=1/2 K=7
def viterbi_decode2(soft_pairs):
    """soft_pairs: list of (s0,s1) where +amp ~ bit0, -amp ~ bit1. Returns hard bits.
    64-state add-compare-select with full predecessor-state traceback."""
    NST = 64; INF = 1e18
    def parity(x): return bin(x).count('1') & 1
    g1out = {}; g2out = {}; nxt = {}
    for st in range(NST):
        for b in (0,1):
            reg = ((st<<1)|b) & 127
            g1out[(st,b)] = parity(reg & G1)
            g2out[(st,b)] = parity(reg & G2)
            nxt[(st,b)] = reg & 63
    pm = [INF]*NST; pm[0] = 0.0
    T = len(soft_pairs)
    prev_state = np.zeros((T, NST), dtype=np.int16)
    in_bit     = np.zeros((T, NST), dtype=np.int8)
    for t in range(T):
        s0, s1 = soft_pairs[t]
        npm = [INF]*NST
        for st in range(NST):
            if pm[st] >= INF: continue
            for b in (0,1):
                e0 = g1out[(st,b)]; e1 = g2out[(st,b)]
                exp0 = 1.0 if e0==0 else -1.0
                exp1 = 1.0 if e1==0 else -1.0
                m = pm[st] - (s0*exp0 + s1*exp1)
                ns = nxt[(st,b)]
                if m < npm[ns]:
                    npm[ns] = m
                    prev_state[t][ns] = st
                    in_bit[t][ns] = b
        pm = npm
    st = 0 if pm[0] < INF else int(np.argmin(pm))
    bits = []
    cur = st
    for t in range(T-1, -1, -1):
        bits.append(int(in_bit[t][cur]))
        cur = int(prev_state[t][cur])
    bits.reverse()
    return bits

# --------------------------------------------------------- RRC taps
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

def be16(b,o): return (b[o]<<8)|b[o+1]

# ===========================================================================
def main():
    allok = True
    # ---- load IQ ----------------------------------------------------------
    iq = np.fromfile('/tmp/lrpt_chain.s8', dtype=np.int8).astype(np.float64)
    sig_i = iq[0::2]; sig_q = iq[1::2]

    # ---- RRC matched filter -----------------------------------------------
    h = rrc_taps(BETA, SPS, SPAN)
    mf_i = np.convolve(sig_i, h, mode='same')
    mf_q = np.convolve(sig_q, h, mode='same')

    # ---- symbol timing: decimate by SPS at the optimal phase --------------
    # noiseless / known-timing chain: pick the phase whose decimated symbols
    # have max average magnitude (the RRC center), matches gardner lock target.
    nsym = gt['n_symbols']
    best_ph = 0; best_pw = -1
    for ph in range(SPS):
        di = mf_i[ph::SPS]; dq = mf_q[ph::SPS]
        pw = np.mean(di*di + dq*dq)
        if pw > best_pw:
            best_pw = pw; best_ph = ph
    di = mf_i[best_ph::SPS]; dq = mf_q[best_ph::SPS]

    # ---- QPSK demap: each symbol -> 2 coded bits (bit0=I sign,bit1=Q sign)-
    # The generator used no carrier offset (timing-only chain), so the soft
    # symbol I/Q ARE the conv-coded soft pairs (sign decision). Build the soft
    # pair stream for Viterbi: (s0,s1) per symbol.
    soft_pairs = list(zip(di.tolist(), dq.tolist()))
    soft_pairs = soft_pairs[:nsym]

    # ---- Viterbi r=1/2 K=7 decode -> CADU bits + flush --------------------
    dec_bits = viterbi_decode2(soft_pairs)
    # the encoder consumed (cadu_bits + 6 flush); recovered bits length matches.
    # find ASM (32 bits) at the front, strip it.
    asm_bits = [(ASM >> (31-k)) & 1 for k in range(32)]
    # locate ASM (should be offset 0 in this noiseless chain; search a small window)
    off = None
    for cand in range(0, 8):
        if dec_bits[cand:cand+32] == asm_bits:
            off = cand; break
    if off is None:
        print("FRAMESYNC: ASM not found = FAIL"); allok=False; off = 0
    else:
        print(f"FRAMESYNC: ASM found at bit offset {off}")
    body_bits = dec_bits[off+32:]

    # ---- regroup body bits into the 1020 randomized RS bytes --------------
    nbytes = I_INTER * 255
    rand_block = []
    for i in range(nbytes):
        v = 0
        for k in range(8):
            v = (v<<1) | body_bits[i*8 + k]
        rand_block.append(v)

    # ---- derandomize (XOR PN) --------------------------------------------
    pn = ccsds_pn(nbytes)
    rs_block = [(rand_block[i] ^ pn[i]) & 0xFF for i in range(nbytes)]

    # ---- de-interleave into 4 codewords, RS-decode each ------------------
    cws = [[0]*255 for _ in range(I_INTER)]
    for i in range(255):
        for k in range(I_INTER):
            cws[k][i] = rs_block[i*I_INTER + k]
    frame_data = []
    rs_all_ok = True
    for k in range(I_INTER):
        data, ok = rs_decode(cws[k])
        if not ok: rs_all_ok = False
        frame_data += list(data)
    truth_frame = gt['frame_data']
    rs_match = (frame_data == truth_frame)
    print(f"RS decode: all_ok={rs_all_ok}  frame_data matches truth = {rs_match}")
    if not (rs_all_ok and rs_match): allok = False

    # ---- VCDU demux: header + M-PDU FHP + walk CCSDS packets -------------
    b = frame_data
    vw = be16(b, 0)
    scid = (vw >> 6) & 0xFF; vcid = vw & 0x3F
    vcnt = (b[2]<<16)|(b[3]<<8)|b[4]
    mh = be16(b, 6); fhp = mh & 0x7FF
    vt = gt['vcdu']
    vok = (scid==vt['scid'] and vcid==vt['vcid'] and vcnt==vt['vcnt'] and fhp==vt['fhp'])
    print(f"VCDU: scid={scid} vcid={vcid} vcnt=0x{vcnt:06X} fhp={fhp}  header matches = {vok}")
    if not vok: allok = False

    # walk packets, collect payloads
    p = 8 + fhp
    recovered_payloads = []
    while p + 6 <= len(b):
        w0 = be16(b, p); apid = w0 & 0x7FF
        w1 = be16(b, p+2); seq = w1 & 0x3FFF
        dl = be16(b, p+4); plen = dl + 1
        total = 6 + plen
        if p + total > len(b): break
        if apid != P['apid']:    # hit padding / non-image
            break
        recovered_payloads.append(b[p+6:p+6+plen])
        p += total
        if len(recovered_payloads) >= NB:
            break
    print(f"VCDU: recovered {len(recovered_payloads)} CCSDS packets (expected {NB})")
    if len(recovered_payloads) < NB: allok = False

    # ---- DCT decompress each block, compare to truth image ---------------
    # expected reconstruction = dequant+IDCT of the truth zig-zag coeffs (the
    # only loss vs the source image is DCT quantization, exactly as dct.rail).
    coeff_truth = gt['coeff_blocks_zz']
    maxerr_overall = 0
    blocks_ok = 0
    for k in range(min(NB, len(recovered_payloads))):
        pay = recovered_payloads[k]
        # parse int16 LE zig-zag coeffs
        coeffs_zz = []
        for c in range(64):
            lo = pay[c*2]; hi = pay[c*2+1]
            u = (hi<<8)|lo
            if u >= 32768: u -= 65536
            coeffs_zz.append(u)
        coeff_match = (coeffs_zz == coeff_truth[k])
        # inverse zig-zag + dequant
        D = np.zeros(64)
        for j in range(64):
            nat = ZZ[j]
            D[nat] = coeffs_zz[j] * float(QTBL.flatten()[nat])
        rec = idct2(D.reshape(8,8)) + 128.0
        rec = np.clip(np.round(rec), 0, 255).astype(int)
        # expected: same operation on the TRUTH coeffs (independent path)
        Dt = np.zeros(64)
        for j in range(64):
            nat = ZZ[j]
            Dt[nat] = coeff_truth[k][j] * float(QTBL.flatten()[nat])
        exp_rec = np.clip(np.round(idct2(Dt.reshape(8,8)) + 128.0), 0, 255).astype(int)
        err = int(np.max(np.abs(rec - exp_rec)))
        maxerr_overall = max(maxerr_overall, err)
        bok = coeff_match and err <= 1
        print(f"IMG BLK{k}: coeffs match={coeff_match} pixel max err {err} (tol 1) = {bok}")
        if bok: blocks_ok += 1
        else: allok = False

    print(f"overall image max pixel err {maxerr_overall}; blocks recovered {blocks_ok}/{NB}")
    print("PASS" if allok else "FAIL")
    sys.exit(0 if allok else 1)

if __name__ == "__main__":
    main()
