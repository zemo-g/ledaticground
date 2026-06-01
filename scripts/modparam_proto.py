#!/usr/bin/env python3
"""Parameter-estimation prototype + oracle for the Rail parameter head (docs/RFML.md).

Beyond the class, estimate physically-grounded parameters per FM-demod-audio window, so the
characterizer ROUTES with parameters, not just a label:
  - center : mean instantaneous frequency (Hz)  [carrier/tuning offset; synthetic-calibrated]
  - baud   : symbol rate (Hz) via first zero-crossing of the autocorrelation  [scale-invariant]
  - snr    : spectral peak-above-noise-floor (dB)  [scale-invariant ratio]
Validates recovery on signals with KNOWN parameters before we port to Rail.
"""
import numpy as np, math
import scripts.gen_modclass as G

FS = G.FS

def make(cls, win, seed):
    rng = np.random.default_rng(seed)
    return G.make_window(cls, win, rng).astype(np.float64)

def est_center(d):
    # FM-demod sample = inst freq (rad/sample) scaled by 32767/pi in our corpus; invert to Hz
    return d.mean() * (math.pi / 32767.0) * FS / (2 * math.pi)

def est_snr(d):
    # spectral peak-to-average power (dB). No median/sort -> portable to Rail. Scale-free,
    # monotonic with concentration: pure tone high, noise low.
    dn = (d - d.mean())
    sd = dn.std() + 1e-9
    P = np.abs(np.fft.rfft(dn / sd * np.hanning(len(dn)))) ** 2
    P[0] = 0.0
    avg = P[1:].mean() + 1e-12
    return 10.0 * math.log10(P.max() / avg)

def est_baud(d):
    # autocorrelation of mean-removed signal; first zero crossing lag ~ symbol period
    x = d - d.mean()
    n = len(x)
    # normalized autocorr via FFT (fast); take positive lags
    f = np.fft.rfft(x, 2 * n)
    ac = np.fft.irfft(f * np.conj(f))[:n]
    ac = ac / (ac[0] + 1e-12)
    lag = 0
    for L in range(1, n):
        if ac[L] <= 0.0:
            lag = L
            break
    if lag == 0:
        return 0.0
    return FS / lag

def main():
    win = 4096
    print("param recovery on known-parameter synthetic windows (FS=48000):")
    print(f"{'class':>8} {'true_baud':>10} {'est_baud':>10} {'est_center':>11} {'est_snr_dB':>11}")
    truth = {"carrier": 0, "afsk": 520.833, "fsk": 1200.0, "msk": 9600.0, "noise": 0}
    for cls in ["noise", "carrier", "afsk", "fsk", "msk"]:
        bs, cs, ss = [], [], []
        for s in range(8):
            d = make(cls, win, 1000 + s)
            bs.append(est_baud(d)); cs.append(est_center(d)); ss.append(est_snr(d))
        print(f"{cls:>8} {truth[cls]:>10.1f} {np.median(bs):>10.1f} {np.median(cs):>11.1f} {np.median(ss):>11.1f}")

if __name__ == "__main__":
    main()
