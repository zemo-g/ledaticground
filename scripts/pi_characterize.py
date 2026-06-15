#!/usr/bin/env python3
"""Edge characterizer for the roof Pi — runs the RAIL-TRAINED audio model in pure Python.

Training happens in Rail on the Mini (src/modclass.rail, which exports models/audio_softmax.txt);
this lightweight inference runs on the Pi Zero, NO numpy (own radix-2 FFT), like pi_ais_decode.py.
Given an FM-demod-audio capture (rtl_fm -M fm -s 48000, s16), it characterizes each 4096-sample
window {noise,carrier,afsk,fsk,msk}, estimates per-window params, and emits compact JSON to ship
over the weak roof WiFi. Turns the node from "decode the AIS I hand-coded" into "characterize
whatever's on the air."  Usage: pi_characterize.py <capture.s16> [models/audio_softmax.txt] [models/audio_novelty.txt]

FEATURE COORDINATE SYSTEM (RFML cross-cluster contract): feats() below is the EXACT
featlib_v3.feats() = 18 features, copied VERBATIM so the Pi computes the same coordinate
system the model was trained in (zero train/serve skew). Order:
  [rough zcr flat peak cent kurt bimod hf lf dom peak2 npeaks bw p2p1 peak3 sq_sharp occ_norm baud_norm]
The first 15 are byte-identical to featlib.feats(); the last 3 are gain-invariant
cyclostationary / mod-structure discriminators (sq_sharp, occ_norm, baud_norm).

COLS + FAIL-LOUD GUARD: model files carry a `# COLS i j k...` header naming which feature
indices the softmax/novelty use. load_model/load_novelty PARSE it; classify/novelty index
F[cols[j]] (NOT positional). A FAIL-LOUD dimension guard at load time crashes with an explicit
error JSON if the model references a feature index the serving extractor does not produce — it
NEVER silently degrades to all-unknown again. This is the regression that produced the
155/155-unknown V3 path: the old serving feats() returned only 10 features but the deployed v3
model declares COLS 0..15, so F[10..15] were missing/garbage and every window tripped novelty.
"""
import sys, math, json, cmath
from array import array

WIN = 4096
FS = 48000.0
CLASSES = ["noise", "carrier", "afsk", "fsk", "msk"]

# ---- canonical feature names (RFML feature-coordinate-system contract, featlib_v3) ----
_BASE15_NAMES = ["rough", "zcr", "flat", "peak", "cent", "kurt", "bimod",
                 "hf", "lf", "dom", "peak2", "npeaks", "bw", "p2p1", "peak3"]
_V3_NAMES = ["sq_sharp", "occ_norm", "baud_norm"]
FEATURE_NAMES = _BASE15_NAMES + _V3_NAMES   # 18

# squared-signal symbol-clock search band, in Hz (excludes DC and the 2*carrier line)
_SYM_LO_HZ = 200.0
_SYM_HI_HZ = 12000.0


def _err_exit(kind_dict):
    """Fail-loud: emit an explicit error JSON and exit nonzero. NEVER fall through."""
    print(json.dumps(kind_dict))
    sys.exit(2)


def load_model(path):
    mu = sg = b = None; W = [None] * 5; cols = None
    for line in open(path):
        st = line.strip()
        if st.startswith("# COLS"):
            cols = [int(x) for x in st.split()[2:]]; continue
        if not st or st.startswith("#"):
            continue
        tag, *vals = st.split()
        v = [float(x) for x in vals]
        if tag == "MU": mu = v
        elif tag == "SG": sg = v
        elif tag == "B": b = v
        elif tag.startswith("W"): W[int(tag[1:])] = v
    # positional fallback ONLY when the model omits a COLS header (legacy 10-feat models)
    if cols is None:
        cols = list(range(len(mu) if mu is not None else 0))
    return mu, sg, W, b, cols


def load_novelty(path):
    tau = None; C = [None] * 5; S = [None] * 5; cols = None
    for line in open(path):
        st = line.strip()
        if st.startswith("# COLS"):
            cols = [int(x) for x in st.split()[2:]]; continue
        if not st or st.startswith("#"):
            continue
        t, *v = st.split()
        if t == "TAU": tau = float(v[0])
        elif t.startswith("C"): C[int(t[1:])] = [float(x) for x in v]
        elif t.startswith("S"): S[int(t[1:])] = [float(x) for x in v]
    if cols is None:
        cols = list(range(len(C[0]) if C[0] is not None else 0))
    return tau, C, S, cols


def fft(x):
    """Recursive radix-2 FFT — verbatim from featlib_v3.py / featlib.py."""
    n = len(x)
    if n == 1:
        return [x[0]]
    ev = fft(x[0::2]); od = fft(x[1::2])
    out = [0j] * n
    for k in range(n // 2):
        t = cmath.exp(-2j * math.pi * k / n) * od[k]
        out[k] = ev[k] + t; out[k + n // 2] = ev[k] - t
    return out


def feats(d):
    """18-feature vector for one window `d` (list[float]) — VERBATIM featlib_v3.feats().

    Returns FEATURE_NAMES-aligned floats. First 15 are byte-identical to featlib.feats();
    last 3 are the gain-invariant cyclostationary features (sq_sharp, occ_norm, baud_norm).
    """
    n = len(d)
    # ============== BEGIN verbatim from featlib_v3.feats() (first 15) ==============
    mean = sum(d) / n
    var = sum((x - mean) ** 2 for x in d) / n
    sd = math.sqrt(var) + 1e-9
    rough = (sum(abs(d[i + 1] - d[i]) for i in range(n - 1)) / (n - 1)) / sd
    def sgn(x): return 1.0 if x > 0 else (-1.0 if x < 0 else 0.0)
    zcr = (sum(abs(sgn(d[i + 1] - mean) - sgn(d[i] - mean)) for i in range(n - 1)) / (n - 1)) / 2.0
    m3 = sum(((x - mean) / sd) ** 3 for x in d) / n
    m4 = sum(((x - mean) / sd) ** 4 for x in d) / n
    kurt = m4 - 3.0
    bimod = (m3 * m3 + 1.0) / (m4 + 1e-9)
    # spectrum: windowed dn, fft, power, bins 1..nb-1 (DC dropped)
    win = [((d[i] - mean) / sd) * (0.5 - 0.5 * math.cos(2 * math.pi * i / (n - 1))) for i in range(n)]
    X = fft(win)
    nb = n // 2 + 1
    Ps = 0.0; mx = 0.0; argmx = 0; sumlog = 0.0; cnt = 0; hf = 0.0; lf = 0.0
    hcut = nb // 3
    for k in range(1, nb):
        p = X[k].real * X[k].real + X[k].imag * X[k].imag
        Ps += p
        if p > mx: mx = p; argmx = k
        sumlog += math.log(p + 1e-12); cnt += 1
        if k >= hcut: hf += p
        if k < 170: lf += p
        # centroid accumulated below
    sumkp = 0.0
    for k in range(1, nb):
        p = X[k].real * X[k].real + X[k].imag * X[k].imag
        sumkp += k * p
    ps = Ps + 1e-12
    peak = mx / ps
    cent = (sumkp / ps) / nb
    flat = math.exp(sumlog / cnt) / ((Ps / cnt) + 1e-12)
    dom = argmx / nb
    F10 = [rough, zcr, flat, peak, cent, kurt, bimod, hf / ps, lf / ps, dom]

    # ---------- existing-5 gain-invariant features (verbatim from featlib_v3.feats) ----------
    P = [0.0] * nb            # P[0] (DC) stays 0; bins 1..nb-1 carry power
    for k in range(1, nb):
        P[k] = X[k].real * X[k].real + X[k].imag * X[k].imag
    mu_bin = (sumkp / ps)
    spread = 0.0
    for k in range(1, nb):
        spread += P[k] * (k - mu_bin) * (k - mu_bin)
    bw = math.sqrt(spread / ps) / nb
    Sm = [0.0] * nb
    for k in range(1, nb):
        lo = P[k - 1] if k - 1 >= 1 else 0.0
        hi = P[k + 1] if k + 1 <= nb - 1 else 0.0
        Sm[k] = (lo + P[k] + hi) / 3.0
    smx = max(Sm[1:]) if nb > 1 else 0.0
    thr = 0.25 * smx
    npk = 0
    for k in range(1, nb):
        if Sm[k] >= thr and smx > 0.0:
            npk += 1
    npeaks = npk / float(nb)
    locmax = []
    for k in range(1, nb):
        lo = Sm[k - 1] if k - 1 >= 1 else 0.0
        hi = Sm[k + 1] if k + 1 <= nb - 1 else 0.0
        if Sm[k] > lo and Sm[k] >= hi:
            locmax.append(P[k])
    locmax.sort(reverse=True)
    p1 = locmax[0] if len(locmax) >= 1 else 0.0
    p2 = locmax[1] if len(locmax) >= 2 else 0.0
    p3 = locmax[2] if len(locmax) >= 3 else 0.0
    peak2 = p2 / ps
    peak3 = p3 / ps
    p2p1 = (p2 / (p1 + 1e-12))
    F15 = F10 + [peak2, npeaks, bw, p2p1, peak3]
    # ============== END verbatim (first 15 are byte-identical to featlib) ==============

    # ---------- NEW v3 gain-invariant cyclostationary / mod-structure features ----------
    # occ_norm: main-spectrum -6 dB (0.25*max power) occupied bandwidth / Nyquist.
    occ_lo = nb; occ_hi = 0
    pthr = 0.25 * mx
    for k in range(1, nb):
        if P[k] >= pthr:
            if k < occ_lo: occ_lo = k
            if k > occ_hi: occ_hi = k
    occ_bins = (occ_hi - occ_lo) if occ_hi >= occ_lo else 0
    occ_norm = (occ_bins * (FS / n)) / (FS / 2.0)     # (Hz width)/Nyquist -> [0,1]

    # Squared-signal spectrum: y = (d-mean)^2, DC-removed, Hann, FFT. Squaring exposes the
    # symbol clock as a spectral LINE; its location == symbol rate, concentration == cyclostat.
    y = [(d[i] - mean) * (d[i] - mean) for i in range(n)]
    ym = sum(y) / n
    yc = [y[i] - ym for i in range(n)]
    yvar = 0.0
    for v in yc:
        yvar += v * v
    ysd = math.sqrt(yvar / n) + 1e-9
    ywin = [(yc[i] / ysd) * (0.5 - 0.5 * math.cos(2 * math.pi * i / (n - 1))) for i in range(n)]
    Y = fft(ywin)
    P2tot = 0.0
    P2 = [0.0] * nb
    for k in range(1, nb):
        pk = Y[k].real * Y[k].real + Y[k].imag * Y[k].imag
        P2[k] = pk
        P2tot += pk
    p2tot = P2tot + 1e-12

    # symbol-clock band [200,12000] Hz: dominant line / total -> sq_sharp; arg -> baud
    klo = int(_SYM_LO_HZ * n / FS)
    if klo < 1: klo = 1
    khi = int(_SYM_HI_HZ * n / FS)
    if khi > nb: khi = nb
    mxb = 0.0; argb = 0
    for k in range(klo, khi):
        if P2[k] > mxb:
            mxb = P2[k]; argb = k
    sq_sharp = mxb / p2tot
    baud_hz = (argb * (FS / n)) if argb else 0.0
    baud_norm = baud_hz / (FS / 2.0)

    return F15 + [sq_sharp, occ_norm, baud_norm]


def feats_spec(d):
    """feats() plus the (mean, mx, Ps/cnt, sd) spec tuple used by params()."""
    F = feats(d)
    n = len(d)
    mean = sum(d) / n
    var = sum((x - mean) ** 2 for x in d) / n
    sd = math.sqrt(var) + 1e-9
    win = [((d[i] - mean) / sd) * (0.5 - 0.5 * math.cos(2 * math.pi * i / (n - 1))) for i in range(n)]
    X = fft(win)
    nb = n // 2 + 1
    Ps = 0.0; mx = 0.0; cnt = 0
    for k in range(1, nb):
        p = X[k].real * X[k].real + X[k].imag * X[k].imag
        Ps += p
        if p > mx: mx = p
        cnt += 1
    return F, (mean, mx, Ps / cnt, sd)


def params(d, spec):
    mean, mx, avg, sd = spec
    center = mean * (FS / (2 * 32767.0))
    snr = 10.0 * math.log10(mx / (avg + 1e-12) + 1e-12)
    # baud via autocorr first zero-crossing
    x = [v - mean for v in d]; n = len(x)
    ac0 = sum(v * v for v in x) + 1e-9
    baud = 0.0
    for L in range(1, 256):
        ac = sum(x[i] * x[i + L] for i in range(n - L))
        if ac / ac0 <= 0.0:
            baud = FS / L; break
    return center, baud, snr


def classify(F, mu, sg, W, b, cols):
    # index F BY COLS (the model's feature-index map), never positionally
    z = [(F[cols[j]] - mu[j]) / sg[j] for j in range(len(cols))]
    lg = [b[c] + sum(W[c][j] * z[j] for j in range(len(z))) for c in range(5)]
    return max(range(5), key=lambda c: lg[c]), z


def novelty(z, C, S):
    # min diagonal-Mahalanobis distance to any class centroid (standardized feature space)
    return min(math.sqrt(sum(((z[f] - C[c][f]) / S[c][f]) ** 2 for f in range(len(z))) / len(z)) for c in range(5))


def _guard_dims(mcols, ncols, serving_nfeat, cap):
    """FAIL-LOUD dimension guard. Crash (nonzero) with explicit error JSON if any model
    feature index exceeds what the serving extractor produces, or if model/novelty cols
    disagree. NEVER silently degrade to all-unknown (compound-verification-gap rule)."""
    model_max_col = max(mcols) if mcols else -1
    if model_max_col >= serving_nfeat:
        _err_exit({"kind": "rfml", "error": "feat_dim_mismatch",
                   "model_max_col": model_max_col, "serving_feats": serving_nfeat,
                   "detail": "model references a feature index the serving extractor does not produce",
                   "capture": cap})
    if ncols is not None and ncols != mcols:
        _err_exit({"kind": "rfml", "error": "cols_disagree",
                   "softmax_cols": mcols, "novelty_cols": ncols,
                   "detail": "softmax and novelty COLS headers differ", "capture": cap})


def main():
    cap = sys.argv[1]
    mpath = sys.argv[2] if len(sys.argv) > 2 else "models/audio_softmax.txt"
    npath = sys.argv[3] if len(sys.argv) > 3 else "models/audio_novelty.txt"
    mu, sg, W, b, cols = load_model(mpath)
    try:
        tau, C, S, ncols = load_novelty(npath)
    except OSError:
        tau, C, S, ncols = None, None, None, None   # novelty optional; classify-only if absent

    # FAIL-LOUD dimension guard at load time, before classifying anything.
    serving_nfeat = len(FEATURE_NAMES)              # == 18, == len(feats(window))
    nov_cols = ncols if (tau is not None) else None
    _guard_dims(cols, nov_cols, serving_nfeat, cap)

    a = array("h"); a.frombytes(open(cap, "rb").read())
    s = [float(x) for x in a]
    nwin = len(s) // WIN
    tally = {c: 0 for c in CLASSES}; tally["unknown"] = 0
    sig = []   # (class, center, baud, snr) for non-idle, recognized windows
    for w in range(nwin):
        d = s[w * WIN:(w + 1) * WIN]
        F, spec = feats_spec(d)
        ci, z = classify(F, mu, sg, W, b, cols)
        if tau is not None and novelty(z, C, S) > tau:   # the node admits it doesn't recognize this
            tally["unknown"] += 1
            continue
        tally[CLASSES[ci]] += 1
        if CLASSES[ci] not in ("noise", "carrier"):
            c, bd, sn = params(d, spec)
            sig.append((CLASSES[ci], c, bd, sn))
    out = {"capture": cap, "windows": nwin, "classes": tally,
           "signal_windows": len(sig), "unknown_windows": tally["unknown"],
           "model": "rail-trained-audio-softmax-v3-18f+novelty"}
    if sig:
        from collections import Counter
        dom = Counter(x[0] for x in sig).most_common(1)[0][0]
        ds = [x for x in sig if x[0] == dom]
        out["dominant_signal"] = dom
        bauds = sorted(x[2] for x in ds if 100.0 < x[2] < 15000.0)
        out["params"] = {"center_hz": round(sum(x[1] for x in ds) / len(ds), 1),
                         "baud_hz": round(bauds[len(bauds) // 2], 1) if bauds else None,
                         "snr_db": round(sum(x[3] for x in ds) / len(ds), 1)}
    print(json.dumps(out))


if __name__ == "__main__":
    main()
