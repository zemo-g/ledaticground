#!/usr/bin/env python3
"""Open-set novelty prototype + oracle (docs/RFML.md) — "the node admits it hears something new."

The softmax always force-fits a window into one of the 5 known classes. The novelty head adds an
out-of-distribution score = distance to the nearest class centroid in standardized feature space;
above a threshold (high percentile of in-distribution training distances) the verdict is UNKNOWN.
Validated falsifiably: NOVEL modulations the model never trained on (chirp, multitone) must flag
UNKNOWN (high OOD recall) while known test windows mostly do not (low false-unknown rate).
"""
import numpy as np, math
import scripts.modclass_proto as M
import scripts.gen_modclass as G

FS = G.FS

def fm_audio(m, snr_db, n, rng):
    """RF->FM-demod-audio chain (same as gen_modclass) for an arbitrary inst-freq message m (Hz)."""
    phase = np.cumsum(2 * math.pi * m / FS)
    z = np.exp(1j * phase)
    nstd = 10 ** (-snr_db / 20) / math.sqrt(2)
    z = z + (rng.standard_normal(len(z)) + 1j * rng.standard_normal(len(z))) * nstd
    d = G.fm_demod(z)
    return np.clip(np.round(d * (32767.0 / math.pi)), -32767, 32767).astype(np.float64)

def novel_window(kind, win, rng):
    n = win + 1
    if kind == "chirp":                       # linear frequency sweep — not any trained class
        m = np.linspace(-3500, 3500, n) * rng.choice([1, -1])
    elif kind == "multitone":                 # several SIMULTANEOUS tones (afsk is sequential)
        t = np.arange(n) / FS
        m = 1500 * (np.sin(2 * math.pi * 900 * t) + np.sin(2 * math.pi * 1700 * t) + np.sin(2 * math.pi * 2600 * t))
    else:
        raise ValueError(kind)
    return fm_audio(m, rng.uniform(6, 18), n, rng)

def main():
    cls = open("data/modclass/classes.txt").read().split()
    Xtr, ytr, win = M.load("train"); Xte, yte, _ = M.load("test")
    Ftr = M.featurize(Xtr); mu, sg = M.zfit(Ftr); Ftr = (Ftr - mu) / sg
    Fte = (M.featurize(Xte) - mu) / sg
    cent = np.array([Ftr[ytr == c].mean(0) for c in range(len(cls))])
    cstd = np.array([Ftr[ytr == c].std(0) + 1e-3 for c in range(len(cls))])  # per-class per-feature spread
    def novelty(F):  # min diagonal-Mahalanobis distance to any class (per-feature normalized)
        out = []
        for f in F:
            out.append(min(math.sqrt((((cent[c] - f) / cstd[c]) ** 2).mean()) for c in range(len(cls))))
        return np.array(out)
    tr_nov = novelty(Ftr)
    nov_te = novelty(Fte)
    rng = np.random.default_rng(909)
    novs = {}
    for kind in ["chirp", "multitone"]:
        Z = np.array([novel_window(kind, win, rng) for _ in range(60)])
        novs[kind] = novelty((M.featurize(Z) - mu) / sg)
    print(f"{'pctile':>7} {'tau':>6} {'false-unknown':>14} {'chirp-recall':>13} {'multitone':>10}")
    for pct in [90, 95, 97, 99]:
        tau = float(np.percentile(tr_nov, pct))
        print(f"{pct:>7} {tau:>6.2f} {(nov_te>tau).mean()*100:>13.1f}% "
              f"{(novs['chirp']>tau).mean()*100:>12.0f}% {(novs['multitone']>tau).mean()*100:>9.0f}%")
    print("multitone ~= afsk in this feature space (force-fits to afsk) -> documented open-set blind")
    print("spot; a spectral-peak-count feature would separate it. chirp is out-of-envelope -> caught.")

if __name__ == "__main__":
    main()
