#!/usr/bin/env python3
"""Feature-design prototype + cross-check oracle for the Rail characterizer (docs/RFML.md).

Validates that SCALE-INVARIANT features separate the 5 modulation classes BEFORE we build the Rail
extractor — and that they survive the synthetic→real scale gap (rtl_fm AGC means absolute amplitude
isn't comparable, so every feature here is a ratio/rate). Nearest-centroid is the deterministic floor
the Rail classifier must match. Prints held-out accuracy + confusion matrix (Gate A).

Usage: modclass_proto.py            # synthetic train/test
       modclass_proto.py --real <s16>   # also classify windows of a real capture (Gate B)
"""
import numpy as np, argparse

def load(split):
    s = np.fromfile(f"data/modclass/{split}.s16", "<i2").astype(np.float64)
    y = np.array([int(l) for l in open(f"data/modclass/{split}.labels")])
    win = int(open("data/modclass/win.txt").read())
    n = len(y)
    return s[:n * win].reshape(n, win), y, win

def feats(d):
    """10 scale-invariant features from one FM-demod audio window (all ratios/rates, so they
    survive the synthetic->real AGC scale gap)."""
    d = d - d.mean()
    sd = d.std() + 1e-9
    dn = d / sd
    # 1. normalized roughness: mean |first difference| / std
    rough = np.abs(np.diff(d)).mean() / sd
    # 2. zero-crossing rate (scale-free) -- tracks symbol/transition rate
    zcr = np.mean(np.abs(np.diff(np.sign(dn)))) / 2.0
    # spectrum (power), drop DC
    P = np.abs(np.fft.rfft(dn * np.hanning(len(dn)))) ** 2
    P[0] = 0.0
    Ps = P.sum() + 1e-12
    nb = len(P)
    # 3. spectral flatness geomean/mean (noise ~1, tone ~0)
    Pn = P[P > 0]
    flat = np.exp(np.log(Pn + 1e-12).mean()) / (Pn.mean() + 1e-12) if len(Pn) else 0.0
    # 4. spectral peak concentration
    peak = P.max() / Ps
    # 5. spectral centroid (normalized to Nyquist)
    fbin = np.arange(nb)
    cent = (fbin * P).sum() / Ps / nb
    # 6. excess kurtosis of dn (2-level FSK/MSK platykurtic; noise ~0)
    kurt = (dn ** 4).mean() - 3.0
    # 7. bimodality coefficient (skew^2 + 1)/kurtosis_full  (high => two-level)
    sk = (dn ** 3).mean()
    ku = (dn ** 4).mean()
    bimod = (sk ** 2 + 1.0) / (ku + 1e-9)
    # 8. HF energy ratio (>Nyq/3): sharp FSK edges rich; Gaussian MSK smooth; tells fsk from msk
    hf = P[nb // 3:].sum() / Ps
    # 9. LF energy ratio (<~2 kHz, bin nb*2000/24000): MSK concentrated low, FSK spread
    lf = P[:max(1, int(nb * 2000 / 24000))].sum() / Ps
    # 10. dominant-bin frequency (normalized): AFSK tones land mid-band, others differ
    dom = float(np.argmax(P)) / nb
    return np.array([rough, zcr, flat, peak, cent, kurt, bimod, hf, lf, dom])

def featurize(X):
    return np.array([feats(x) for x in X])

def zfit(F):
    return F.mean(0), F.std(0) + 1e-9

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--real")
    a = ap.parse_args()
    classes = open("data/modclass/classes.txt").read().split()
    Xtr, ytr, win = load("train")
    Xte, yte, _ = load("test")
    Ftr, Fte = featurize(Xtr), featurize(Xte)
    mu, sg = zfit(Ftr)
    Ftr, Fte = (Ftr - mu) / sg, (Fte - mu) / sg
    # nearest-centroid (the deterministic floor)
    cent = np.array([Ftr[ytr == c].mean(0) for c in range(len(classes))])
    pred = np.array([np.argmin(((cent - f) ** 2).sum(1)) for f in Fte])
    acc = (pred == yte).mean()
    print(f"Gate A — nearest-centroid held-out accuracy: {acc*100:.1f}%  (chance {100/len(classes):.0f}%)")
    cm = np.zeros((len(classes), len(classes)), int)
    for t, p in zip(yte, pred):
        cm[t, p] += 1
    print("confusion (rows=true, cols=pred): " + " ".join(f"{c[:4]:>5}" for c in classes))
    for i, c in enumerate(classes):
        print(f"  {c:>7} " + " ".join(f"{cm[i,j]:5d}" for j in range(len(classes))))
    # feature means per class (for the Rail port to reproduce)
    print("\ncentroids (z-space) per class:")
    for i, c in enumerate(classes):
        print(f"  {c:>7} " + " ".join(f"{v:+.2f}" for v in cent[i]))
    if a.real:
        s = np.fromfile(a.real, "<i2").astype(np.float64)
        nwin = len(s) // win
        R = s[:nwin * win].reshape(nwin, win)
        FR = (featurize(R) - mu) / sg
        pr = np.array([np.argmin(((cent - f) ** 2).sum(1)) for f in FR])
        import collections
        cnt = collections.Counter(pr)
        print(f"\nGate B — real capture {a.real}: {nwin} windows")
        for c in range(len(classes)):
            if cnt[c]:
                print(f"  {classes[c]:>7}: {cnt[c]:4d}  ({cnt[c]/nwin*100:.0f}%)")

if __name__ == "__main__":
    main()
