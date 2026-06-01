#!/usr/bin/env python3
"""IQ-domain feature design + oracle for the Rail IQ characterizer (docs/RFML.md).

The IQ sibling of modclass_proto.py. Scale-invariant complex-baseband features that separate
{noise,cw,bpsk,qpsk,fsk}; nearest-centroid floor + the logreg ceiling; Gate B on real roof IQ.
Input cu8 (uint8 ~127.5-centered), 240 kHz.  Usage: modclass_iq_proto.py [--real <cu8>]
"""
import numpy as np, math, argparse, collections

def load(split):
    b = np.fromfile(f"data/modclass_iq/{split}.cu8", np.uint8).astype(np.float64) - 127.5
    y = np.array([int(l) for l in open(f"data/modclass_iq/{split}.labels")])
    win = int(open("data/modclass_iq/win.txt").read())
    z = (b[0::2] + 1j * b[1::2])
    n = len(y)
    return z[:n * win].reshape(n, win), y, win

def feats(z):
    """8 scale-invariant complex-baseband features from one IQ window."""
    a = np.abs(z) + 1e-9
    # 1. normalized amplitude variance: constant-envelope (cw/psk/fsk) low, noise/AM high
    gamma = a.std() / a.mean()
    # instantaneous frequency
    df = np.angle(z[1:] * np.conj(z[:-1]))
    # 2. inst-freq std: FSK sustained 2-level high, CW ~0, PSK impulsive
    ifstd = df.std()
    # 3. inst-freq kurtosis: PSK phase-jumps impulsive (high), FSK bimodal (low)
    dfn = (df - df.mean()) / (df.std() + 1e-9)
    ifkurt = (dfn ** 4).mean() - 3.0
    # spectrum of complex z (full, shifted)
    P = np.abs(np.fft.fftshift(np.fft.fft(z * np.hanning(len(z))))) ** 2
    Ps = P.sum() + 1e-12
    nb = len(P)
    # 4. peak concentration (CW huge)
    peak = P.max() / Ps
    # 5. spectral flatness (noise ~1, tone ~0)
    Pn = P[P > 0]
    flat = math.exp(np.log(Pn + 1e-12).mean()) / (Pn.mean() + 1e-12)
    # 6. occupied bandwidth: spectral spread about centroid, normalized
    f = np.arange(nb)
    c = (f * P).sum() / Ps
    bw = math.sqrt(((f - c) ** 2 * P).sum() / Ps) / nb
    # 7. 2nd-moment magnitude |E[z^2]|/E[|z|^2]: BPSK aligns (high), QPSK cancels (low)
    m2 = abs((z * z).mean()) / ((a ** 2).mean() + 1e-12)
    # 8. zero-crossing rate of inst-freq (FSK/noise high, CW low)
    zcr = np.mean(np.abs(np.diff(np.sign(dfn)))) / 2.0
    return np.array([gamma, ifstd, ifkurt, peak, flat, bw, m2, zcr])

def featurize(Z):
    return np.array([feats(z) for z in Z])

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--real"); a = ap.parse_args()
    cls = open("data/modclass_iq/classes.txt").read().split()
    Ztr, ytr, win = load("train"); Zte, yte, _ = load("test")
    Ftr, Fte = featurize(Ztr), featurize(Zte)
    mu, sg = Ftr.mean(0), Ftr.std(0) + 1e-9
    Ftr, Fte = (Ftr - mu) / sg, (Fte - mu) / sg
    cent = np.array([Ftr[ytr == c].mean(0) for c in range(len(cls))])
    pred = np.array([np.argmin(((cent - f) ** 2).sum(1)) for f in Fte])
    print(f"nearest-centroid floor: {(pred==yte).mean()*100:.1f}%  (chance {100/len(cls):.0f}%)")
    try:
        from sklearn.linear_model import LogisticRegression
        from sklearn.neural_network import MLPClassifier
        lr = LogisticRegression(max_iter=3000, C=5).fit(Ftr, ytr)
        ml = MLPClassifier((32, 16), max_iter=4000, random_state=0).fit(Ftr, ytr)
        print(f"Gate A logreg: {(lr.predict(Fte)==yte).mean()*100:.1f}%   mlp: {(ml.predict(Fte)==yte).mean()*100:.1f}%")
        p = lr.predict(Fte); cm = np.zeros((len(cls), len(cls)), int)
        for t, q in zip(yte, p): cm[t, q] += 1
        print("logreg confusion (rows=true): " + " ".join(f"{c[:4]:>5}" for c in cls))
        for i, c in enumerate(cls): print(f"  {c:>6} " + " ".join(f"{cm[i,j]:5d}" for j in range(len(cls))))
        clf = lr
    except ImportError:
        clf = None
    if a.real and clf is not None:
        b = np.fromfile(a.real, np.uint8).astype(np.float64) - 127.5
        z = b[0::2] + 1j * b[1::2]
        nw = len(z) // win
        Z = z[:nw * win].reshape(nw, win)
        FR = (featurize(Z) - mu) / sg
        pr = clf.predict(FR); cnt = collections.Counter(pr)
        print(f"\nGate B — real IQ {a.real}: {nw} windows (AIS GMSK should read as fsk)")
        for c in range(len(cls)):
            if cnt[c]: print(f"  {cls[c]:>6}: {cnt[c]:5d}  ({cnt[c]/nw*100:4.0f}%)")

if __name__ == "__main__":
    main()
