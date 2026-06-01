#!/usr/bin/env python3
"""Synthetic multi-class modulation corpus for the RFML characterizer (docs/RFML.md).

The node's real domain is FM-demod audio @ 48 kHz, s16 (rtl_fm -M fm -s 48000) — which is what
data/ais_clean_a.s16 IS. So every synthetic class goes through the SAME chain: build a complex
baseband RF signal, add complex noise at a target SNR, then discriminator-demodulate to audio. That
keeps the idle-noise statistics honest (FM-demodulated thermal noise is rough ±π — a real feature),
which is the whole point: we want synthetic→real transfer to be a fair test.

Classes (physically coherent in the audio domain), chance = 20%:
  0 noise   - complex-Gaussian RF only           (idle squelch)
  1 carrier - unmodulated tone                    (NWR carrier)
  2 afsk    - audio FSK tones 1562/2083 Hz        (SAME-like)
  3 fsk     - direct 2-FSK, ±2400 Hz, 1200 baud   (generic data carrier)
  4 msk     - Gaussian MSK 2400 Hz dev 9600 baud  (AIS -- the real-transfer target)

Writes data/modclass/{train,test}.s16 (concatenated WIN-sample windows) + .labels (one class id per
line, window order) + classes.txt. Usage: gen_modclass.py [--win 4096] [--per 200] [--testper 60]
"""
import numpy as np, os, math, argparse

FS = 48000.0
CLASSES = ["noise", "carrier", "afsk", "fsk", "msk"]

def fm_demod(z):
    # discriminator: instantaneous frequency = angle(z[n] * conj(z[n-1]))
    d = np.angle(z[1:] * np.conj(z[:-1]))
    return d  # radians/sample, in (-pi, pi]

def gaussian_pulse(sps, BT=0.4, span=4):
    t = np.arange(-span * sps, span * sps + 1) / sps
    sigma = math.sqrt(math.log(2)) / (2 * math.pi * BT)
    g = np.exp(-t ** 2 / (2 * sigma ** 2))
    return g / g.sum()

def mod_signal(cls, n, rng):
    """Instantaneous-frequency message m(t) (Hz) for n complex baseband samples, + amplitude flag."""
    if cls == "noise":
        return np.zeros(n), 0.0                       # no signal, noise only
    if cls == "carrier":
        f0 = rng.uniform(500, 3000) * rng.choice([-1, 1])
        return np.full(n, f0), 1.0
    if cls == "afsk":                                  # audio tones FM-carried; demod recovers tones
        baud = 520.833; sps = FS / baud; dev = 4000.0
        nsym = int(n / sps) + 2
        toned = rng.choice([1562.5, 2083.3], nsym)
        m = np.zeros(n)
        for k in range(n):
            sym = int(k / sps)
            m[k] = math.sin(2 * math.pi * toned[sym] * k / FS)
        return m * dev, 1.0
    if cls == "fsk":                                   # direct 2-FSK, square frequency
        baud = 1200.0; sps = int(FS / baud); dev = 2400.0
        nsym = int(n / sps) + 2
        syms = rng.choice([-1.0, 1.0], nsym)
        m = np.repeat(syms, sps)[:n]
        return m * dev, 1.0
    if cls == "msk":                                   # Gaussian MSK = AIS (gen_ais chain)
        baud = 9600.0; sps = int(FS / baud); dev = 2400.0
        nsym = int(n / sps) + 8
        nrz = (2 * rng.integers(0, 2, nsym) - 1.0)
        up = np.repeat(nrz, sps)
        g = gaussian_pulse(sps)
        shaped = np.convolve(up, g, "same")[:n]
        return shaped * dev, 1.0
    raise ValueError(cls)

def make_window(cls, win, rng):
    n = win + 1
    m, amp = mod_signal(cls, n, rng)
    if amp == 0.0:                                     # pure-noise class
        z = (rng.standard_normal(n) + 1j * rng.standard_normal(n)) / math.sqrt(2)
    else:
        phase = np.cumsum(2 * math.pi * m / FS)
        sig = np.exp(1j * phase)
        snr = rng.uniform(3.0, 20.0)                   # dB, signal power = 1
        nstd = 10 ** (-snr / 20) / math.sqrt(2)
        z = sig + (rng.standard_normal(n) + 1j * rng.standard_normal(n)) * nstd
    d = fm_demod(z)                                    # win samples
    s = np.clip(np.round(d * (32767.0 / math.pi)), -32767, 32767).astype("<i2")
    return s

def build(split, per, win, seed):
    rng = np.random.default_rng(seed)
    wins, labels = [], []
    order = []
    for ci, cls in enumerate(CLASSES):
        for _ in range(per):
            order.append((ci, cls))
    rng.shuffle(order)                                 # interleave classes
    for ci, cls in order:
        wins.append(make_window(cls, win, rng))
        labels.append(ci)
    sig = np.concatenate(wins)
    os.makedirs("data/modclass", exist_ok=True)
    sig.tofile(f"data/modclass/{split}.s16")
    with open(f"data/modclass/{split}.labels", "w") as f:
        f.write("\n".join(str(l) for l in labels) + "\n")
    return len(labels)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--win", type=int, default=4096)
    ap.add_argument("--per", type=int, default=200)
    ap.add_argument("--testper", type=int, default=60)
    a = ap.parse_args()
    ntr = build("train", a.per, a.win, seed=101)
    nte = build("test", a.testper, a.win, seed=202)
    with open("data/modclass/classes.txt", "w") as f:
        f.write("\n".join(CLASSES) + "\n")
    with open("data/modclass/win.txt", "w") as f:
        f.write(f"{a.win}\n")
    print(f"win={a.win}  train={ntr} windows  test={nte} windows  classes={CLASSES}")
    print(f"  train.s16 = {ntr * a.win * 2} bytes   test.s16 = {nte * a.win * 2} bytes")

if __name__ == "__main__":
    main()
