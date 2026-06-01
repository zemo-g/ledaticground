#!/usr/bin/env python3
"""ledaticground AIS host driver: burst detection + tight windowing.
The DECODE is pure Rail (src/ais_decode.rail -> /tmp/rail_out). This host stage only
finds bursts and hands each a tight FM-demod s16 window — the same division of labor as
the APT sync stage (Rail does the signal/protocol math; host orchestrates which window).

Input: raw RTL-SDR IQ (uint8) centered at 162.000 MHz. Channelizes AIS-A (161.975, -25kHz)
and AIS-B (162.025, +25kHz), envelope-detects bursts, FM-demods each to s16, runs the
Rail decoder per burst. Usage: ais_bursts.py <iq_file> [rail_out_path]
"""
import sys, subprocess, numpy as np

IQ = sys.argv[1]
RAIL = sys.argv[2] if len(sys.argv) > 2 else "/tmp/rail_out"
FS = 240000.0
WIN = "/tmp/ais_win.s16"

b = np.fromfile(IQ, np.uint8).astype(np.float32) - 127.5
z = b[0::2] + 1j * b[1::2]
t = np.arange(len(z)) / FS
ntap = 101
h = np.sinc((12000 / (FS / 2)) * (np.arange(ntap) - ntap // 2)) * np.hamming(ntap)
h /= h.sum()

def channelize(shift_hz):
    za = z * np.exp(2j * np.pi * shift_hz * t)
    za = np.convolve(za, h, "same")[::5]
    return za, FS / 5

def find_bursts(za, fsd):
    mag = np.abs(za); floor = np.median(mag); thr = floor * 3
    hot = (mag > thr).astype(int); edg = np.diff(hot)
    starts = np.where(edg == 1)[0]; ends = np.where(edg == -1)[0]
    if len(ends) and len(starts) and ends[0] < starts[0]: ends = ends[1:]
    n = min(len(starts), len(ends))
    return [(s, e) for s, e in zip(starts[:n], ends[:n]) if (e - s) / fsd > 0.008]

def decode_burst(za, s, e):
    # tight window with small margin so the mean = the burst's DC (carrier offset)
    a = max(s - 30, 0); b2 = min(e + 30, len(za))
    disc = np.concatenate([[0.0], np.angle(za[a + 1:b2] * np.conj(za[a:b2 - 1]))])
    s16 = np.clip(disc * 8000, -32767, 32767).astype("<i2")
    s16.tofile(WIN)
    out = subprocess.run([RAIL], capture_output=True, text=True, timeout=30).stdout
    return out.strip()

results = []
for name, shift in [("AIS-A 161.975", 25000.0), ("AIS-B 162.025", -25000.0)]:
    za, fsd = channelize(shift)
    bursts = find_bursts(za, fsd)
    for (s, e) in bursts:
        out = decode_burst(za, s, e)
        if "DECODE_OK" in out:
            print(f"[{name}] t={s/fsd:6.3f}s  {out.splitlines()[0]}")
            for ln in out.splitlines()[1:]:
                print(f"             {ln.strip()}")
            results.append(out)
        # silent on NO_DECODE (noise burst / collision)
print(f"\n=== {len(results)} CRC-valid AIS messages decoded by pure-Rail chain ===")
