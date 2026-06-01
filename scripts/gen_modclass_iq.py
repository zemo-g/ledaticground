#!/usr/bin/env python3
"""Synthetic IQ-domain modulation corpus for the IQ characterizer (docs/RFML.md).

Coherent modulations (PSK) live in complex baseband, NOT FM-demod audio — so this is the IQ sibling
of gen_modclass.py. Matches the real roof format: cu8 (uint8, ~127.5-centered) at 240 kHz, exactly
what `rtl_sdr -s 240000` writes (data/*.iq). Classes (chance 20%):
  0 noise  - complex-Gaussian
  1 cw     - unmodulated tone (constant envelope)
  2 bpsk   - phase {0,pi}
  3 qpsk   - phase {pi/4,3pi/4,5pi/4,7pi/4}  (LRPT/METEOR is QPSK)
  4 fsk    - 2-FSK, constant envelope  (AIS GMSK reads here -> the real-transfer target)
Writes data/modclass_iq/{train,test}.cu8 (interleaved uint8 IQ, WIN complex/window) + .labels.
"""
import numpy as np, os, math, argparse

FS = 240000.0
CLASSES = ["noise", "cw", "bpsk", "qpsk", "fsk"]
SCALE = 40.0          # cu8 amplitude around the 127.5 center (real roof spans ~+-94)

def iq_signal(cls, n, rng):
    if cls == "noise":
        return (rng.standard_normal(n) + 1j * rng.standard_normal(n)) / math.sqrt(2), 0.0
    if cls == "cw":
        f0 = rng.uniform(15000, 40000) * rng.choice([-1, 1])
        return np.exp(1j * 2 * math.pi * f0 * np.arange(n) / FS), 1.0
    if cls == "bpsk":
        baud = rng.choice([12000.0, 24000.0]); sps = int(FS / baud)
        sym = (2 * rng.integers(0, 2, n // sps + 2) - 1.0)
        z = np.repeat(sym, sps)[:n].astype(complex)
        return z, 1.0
    if cls == "qpsk":
        baud = rng.choice([12000.0, 24000.0]); sps = int(FS / baud)
        bits = rng.integers(0, 4, n // sps + 2)
        cst = np.exp(1j * (math.pi / 4 + bits * math.pi / 2))
        z = np.repeat(cst, sps)[:n]
        return z, 1.0
    if cls == "fsk":
        baud = rng.choice([9600.0, 19200.0]); sps = int(FS / baud); shift = rng.uniform(8000, 24000)
        sym = (2 * rng.integers(0, 2, n // sps + 2) - 1.0)
        finst = np.repeat(sym, sps)[:n] * shift
        ph = np.cumsum(2 * math.pi * finst / FS)
        return np.exp(1j * ph), 1.0
    raise ValueError(cls)

def make_window(cls, win, rng):
    z, amp = iq_signal(cls, win, rng)
    if amp != 0.0:
        snr = rng.uniform(2.0, 20.0)
        nstd = 10 ** (-snr / 20) / math.sqrt(2)
        z = z + (rng.standard_normal(win) + 1j * rng.standard_normal(win)) * nstd
    I = np.clip(np.round(z.real * SCALE + 127.5), 0, 255).astype(np.uint8)
    Q = np.clip(np.round(z.imag * SCALE + 127.5), 0, 255).astype(np.uint8)
    out = np.empty(2 * win, np.uint8); out[0::2] = I; out[1::2] = Q
    return out

def build(split, per, win, seed):
    rng = np.random.default_rng(seed)
    order = [(ci, c) for ci, c in enumerate(CLASSES) for _ in range(per)]
    rng.shuffle(order)
    wins, labels = [], []
    for ci, cls in order:
        wins.append(make_window(cls, win, rng)); labels.append(ci)
    os.makedirs("data/modclass_iq", exist_ok=True)
    np.concatenate(wins).tofile(f"data/modclass_iq/{split}.cu8")
    open(f"data/modclass_iq/{split}.labels", "w").write("\n".join(map(str, labels)) + "\n")
    return len(labels)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--win", type=int, default=2048)
    ap.add_argument("--per", type=int, default=200)
    ap.add_argument("--testper", type=int, default=60)
    a = ap.parse_args()
    ntr = build("train", a.per, a.win, 303)
    nte = build("test", a.testper, a.win, 404)
    open("data/modclass_iq/classes.txt", "w").write("\n".join(CLASSES) + "\n")
    open("data/modclass_iq/win.txt", "w").write(f"{a.win}\n")
    print(f"win={a.win} (complex)  train={ntr}  test={nte}  classes={CLASSES}  fs={FS:.0f} fmt=cu8")

if __name__ == "__main__":
    main()
