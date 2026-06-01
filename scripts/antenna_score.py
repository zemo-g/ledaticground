#!/usr/bin/env python3
"""ledaticground antenna figure-of-merit — the software multiplier for any antenna.
Turns "is this antenna better?" into a number, per band, so dipole tuning and the
new-antenna A/B are measured, not guessed. Appends every score to
data/antenna_scores.jsonl so the current-antenna baseline and the new antenna are
directly comparable.

  162 mode:  antenna_score.py 162 <iq_file> [label]
     <iq_file> = raw RTL-SDR IQ (uint8) centered at 162.000 MHz. Reports per-channel
     band SNR (dB), clipping %, AIS bursts detected/decoded, unique MMSIs. AIS is always
     on air, so 162 is measurable any time — the immediate tuning/benchmark signal.

  137 mode:  antenna_score.py 137 <apt_s16> [label]
     <apt_s16> = FM-demod APT audio (s16 @ 11025 Hz) from a NOAA pass. Reports the
     2400 Hz subcarrier / noise-floor ratio (>5 = real image, ~1 = noise). 137 antenna
     quality can only be measured while a satellite is overhead, so this is logged per pass.
"""
import sys, json, os, subprocess, numpy as np

MODE = sys.argv[1]
F = sys.argv[2]
LABEL = sys.argv[3] if len(sys.argv) > 3 else "unlabeled"
LOG = os.path.join(os.path.dirname(__file__), "..", "data", "antenna_scores.jsonl")
DECODER = "/tmp/ais_decode_bin"


def band_snr_db(psd, freqs, center_khz, half_khz=3.0):
    m = (freqs > center_khz - half_khz) & (freqs < center_khz + half_khz)
    floor = np.median(psd)
    return 10 * np.log10(psd[m].max() / floor)


def score_162(iq):
    b = np.fromfile(iq, np.uint8)
    clip = float(np.mean((b == 0) | (b == 255))) * 100.0
    z = b.astype(np.float32) - 127.5
    z = z[0::2] + 1j * z[1::2]
    fs = 240000.0
    # averaged periodogram for band SNR
    N = 131072
    w = np.hanning(N)
    psd = np.zeros(N); k = 0
    for s in range(0, len(z) - N, N):
        psd += np.abs(np.fft.fftshift(np.fft.fft(z[s:s + N] * w))) ** 2; k += 1
    psd /= max(k, 1)
    fr = np.fft.fftshift(np.fft.fftfreq(N, 1 / fs)) / 1e3
    snr_a = band_snr_db(psd, fr, -25.0)   # AIS-A 161.975
    snr_b = band_snr_db(psd, fr, +25.0)   # AIS-B 162.025
    # burst detect + Rail decode, both channels
    t = np.arange(len(z)) / fs
    ntap = 101
    h = np.sinc((12000 / (fs / 2)) * (np.arange(ntap) - ntap // 2)) * np.hamming(ntap); h /= h.sum()
    detected = decoded = 0; mmsis = set()
    for shift in (25000.0, -25000.0):
        za = np.convolve(z * np.exp(2j * np.pi * shift * t), h, "same")[::5]; fsd = fs / 5
        mag = np.abs(za); thr = np.median(mag) * 3
        hot = (mag > thr).astype(int); edg = np.diff(hot)
        st = np.where(edg == 1)[0]; en = np.where(edg == -1)[0]
        if len(en) and len(st) and en[0] < st[0]: en = en[1:]
        for a, e in zip(st, en):
            if (e - a) / fsd <= 0.008: continue
            detected += 1
            lo = max(a - 30, 0); hi = min(e + 30, len(za))
            disc = np.concatenate([[0.0], np.angle(za[lo + 1:hi] * np.conj(za[lo:hi - 1]))])
            np.clip(disc * 8000, -32767, 32767).astype("<i2").tofile("/tmp/ais_win.s16")
            out = subprocess.run([DECODER], capture_output=True, text=True, timeout=30).stdout
            if "DECODE_OK" in out:
                decoded += 1
                for ln in out.splitlines():
                    if "mmsi=" in ln: mmsis.add(ln.split("mmsi=")[1].split()[0])
    rate = decoded / detected if detected else 0.0
    rec = {"band": "162", "label": LABEL, "capture_mtime": os.path.getmtime(iq),
           "snr_a_db": round(float(snr_a), 1), "snr_b_db": round(float(snr_b), 1),
           "clip_pct": round(clip, 2), "bursts_detected": detected,
           "bursts_decoded": decoded, "decode_rate": round(rate, 3),
           "unique_mmsi": len(mmsis), "secs": round(len(z) / fs, 1)}
    return rec


def score_137(apt):
    s = np.fromfile(apt, dtype="<i2").astype(float)
    fs = 11025.0
    S = np.abs(np.fft.rfft(s * np.hanning(len(s))))
    fr = np.fft.rfftfreq(len(s), 1 / fs)
    m = (fr > 2300) & (fr < 2500)
    floor = np.median(S)
    ratio = S[m].max() / floor
    rec = {"band": "137", "label": LABEL, "capture_mtime": os.path.getmtime(apt),
           "subcarrier_2400_ratio": round(float(ratio), 2),
           "verdict": "signal" if ratio > 5 else ("weak" if ratio > 2 else "noise"),
           "secs": round(len(s) / fs, 1)}
    return rec


rec = score_162(F) if MODE == "162" else score_137(F)
print(json.dumps(rec, indent=2))
with open(LOG, "a") as f:
    f.write(json.dumps(rec) + "\n")
print(f"\nlogged to {os.path.relpath(LOG)}")
