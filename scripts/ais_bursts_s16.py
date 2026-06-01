#!/usr/bin/env python3
"""ledaticground AIS host driver for the AUTONOMOUS path: burst detection on a
FM-demodulated s16 capture (what the roof Pi sends — small files that survive the weak
roof WiFi). The DECODE is pure Rail (src/ais_decode.rail -> /tmp/rail_out); this host
stage only finds tight burst windows.

Detector: an AIS burst is constant-envelope GMSK, so the FM discriminator output is
*smooth* (small structured ±deviation swings), whereas idle (no signal) is *rough*
(random ±pi phase jumps). Per-5ms lag-1 roughness cleanly separates them (~7x).

Usage: ais_bursts_s16.py <s16_file> [fs] [rail_out]
"""
import sys, subprocess, numpy as np

S16 = sys.argv[1]
FS = float(sys.argv[2]) if len(sys.argv) > 2 else 48000.0
RAIL = sys.argv[3] if len(sys.argv) > 3 else "/tmp/rail_out"
WIN = "/tmp/ais_win.s16"

s = np.fromfile(S16, dtype="<i2").astype(np.int64)
if len(s) < 1000:
    print("NO_DATA"); sys.exit(0)

# roughness = per-5ms mean |s[n]-s[n-1]|; burst = LOW roughness, idle = HIGH
B = int(0.005 * FS)
nb = len(s) // B
diff = np.abs(np.diff(s))
rough = np.array([diff[i * B:(i + 1) * B].mean() for i in range(nb)])
thr = np.median(rough) * 0.6

bursts = []
i = 0
while i < nb:
    if rough[i] < thr:
        j = i
        while j < nb and rough[j] < thr:
            j += 1
        bursts.append((i * B, j * B))
        i = j
    else:
        i += 1

results = []
product = []          # canonical decoded-observation lines (the attested product)
for (a, b) in bursts:
    # tight window with ~4ms margin so the slice mean = the burst's DC (carrier offset)
    lo = max(a - int(0.004 * FS), 0)
    hi = min(b + int(0.004 * FS), len(s))
    s[lo:hi].astype("<i2").tofile(WIN)
    out = subprocess.run([RAIL], capture_output=True, text=True, timeout=30).stdout
    if "DECODE_OK" in out:
        t0 = a / FS
        head = out.splitlines()[0]
        print(f"t={t0:5.1f}s  {head}")
        for ln in out.splitlines()[1:]:
            print(f"          {ln.strip()}")
        results.append(out)
        product.append(f"t={t0:.1f}s " + " ".join(x.strip() for x in out.splitlines()))

with open("/tmp/ais_decoded.txt", "w") as f:
    f.write("\n".join(product) + ("\n" if product else ""))

print(f"\n=== {len(results)}/{len(bursts)} bursts decoded by pure-Rail chain "
      f"({len(bursts)} detected in {len(s)/FS:.0f}s) ===")
print("wrote /tmp/ais_decoded.txt (attestation product)")
