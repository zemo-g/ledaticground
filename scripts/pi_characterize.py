#!/usr/bin/env python3
"""Edge characterizer for the roof Pi — runs the RAIL-TRAINED audio model in pure Python.

Training happens in Rail on the Mini (src/modclass.rail, which exports models/audio_softmax.txt);
this lightweight inference runs on the Pi Zero, NO numpy (own radix-2 FFT), like pi_ais_decode.py.
Given an FM-demod-audio capture (rtl_fm -M fm -s 48000, s16), it characterizes each 4096-sample
window {noise,carrier,afsk,fsk,msk}, estimates per-window params, and emits compact JSON to ship
over the weak roof WiFi. Turns the node from "decode the AIS I hand-coded" into "characterize
whatever's on the air."  Usage: pi_characterize.py <capture.s16> [models/audio_softmax.txt]
"""
import sys, math, json, cmath
from array import array

WIN = 4096
FS = 48000.0
CLASSES = ["noise", "carrier", "afsk", "fsk", "msk"]

def load_model(path):
    mu = sg = b = None; W = [None] * 5
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tag, *vals = line.split()
        v = [float(x) for x in vals]
        if tag == "MU": mu = v
        elif tag == "SG": sg = v
        elif tag == "B": b = v
        elif tag.startswith("W"): W[int(tag[1:])] = v
    return mu, sg, W, b

def fft(x):
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
    n = len(d)
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
    return [rough, zcr, flat, peak, cent, kurt, bimod, hf / ps, lf / ps, dom], (mean, mx, Ps / cnt, sd)

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

def classify(F, mu, sg, W, b):
    z = [(F[i] - mu[i]) / sg[i] for i in range(len(F))]
    lg = [b[c] + sum(W[c][i] * z[i] for i in range(len(z))) for c in range(5)]
    return max(range(5), key=lambda c: lg[c]), z

def load_novelty(path):
    tau = None; C = [None] * 5; S = [None] * 5
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        t, *v = line.split()
        if t == "TAU": tau = float(v[0])
        elif t.startswith("C"): C[int(t[1:])] = [float(x) for x in v]
        elif t.startswith("S"): S[int(t[1:])] = [float(x) for x in v]
    return tau, C, S

def novelty(z, C, S):
    # min diagonal-Mahalanobis distance to any class centroid (standardized feature space)
    return min(math.sqrt(sum(((z[f] - C[c][f]) / S[c][f]) ** 2 for f in range(len(z))) / len(z)) for c in range(5))

def main():
    cap = sys.argv[1]
    mpath = sys.argv[2] if len(sys.argv) > 2 else "models/audio_softmax.txt"
    npath = sys.argv[3] if len(sys.argv) > 3 else "models/audio_novelty.txt"
    mu, sg, W, b = load_model(mpath)
    try:
        tau, C, S = load_novelty(npath)
    except OSError:
        tau, C, S = None, None, None       # novelty optional; classify-only if absent
    a = array("h"); a.frombytes(open(cap, "rb").read())
    s = [float(x) for x in a]
    nwin = len(s) // WIN
    tally = {c: 0 for c in CLASSES}; tally["unknown"] = 0
    sig = []   # (class, center, baud, snr) for non-idle, recognized windows
    for w in range(nwin):
        d = s[w * WIN:(w + 1) * WIN]
        F, spec = feats(d)
        ci, z = classify(F, mu, sg, W, b)
        if tau is not None and novelty(z, C, S) > tau:   # the node admits it doesn't recognize this
            tally["unknown"] += 1
            continue
        tally[CLASSES[ci]] += 1
        if CLASSES[ci] not in ("noise", "carrier"):
            c, bd, sn = params(d, spec)
            sig.append((CLASSES[ci], c, bd, sn))
    out = {"capture": cap, "windows": nwin, "classes": tally,
           "signal_windows": len(sig), "unknown_windows": tally["unknown"],
           "model": "rail-trained-audio-softmax+novelty"}
    if sig:
        # dominant non-idle class + its aggregate params
        from collections import Counter
        dom = Counter(x[0] for x in sig).most_common(1)[0][0]
        ds = [x for x in sig if x[0] == dom]
        out["dominant_signal"] = dom
        # baud per-window is noisy (autocorr first-zero); aggregate as median over plausible
        # values only, null if none — never report the no-symbol L=1 sentinel as a real rate.
        bauds = sorted(x[2] for x in ds if 100.0 < x[2] < 15000.0)
        out["params"] = {"center_hz": round(sum(x[1] for x in ds) / len(ds), 1),
                         "baud_hz": round(bauds[len(bauds) // 2], 1) if bauds else None,
                         "snr_db": round(sum(x[3] for x in ds) / len(ds), 1)}
    print(json.dumps(out))

if __name__ == "__main__":
    main()
