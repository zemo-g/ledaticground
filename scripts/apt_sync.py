#!/usr/bin/env python3.11
# APT sync + render: takes Rail apt.rail full-line ROW output (2080 px/line),
# finds the line-start offset by correlating the sync region against a square
# wave, re-chunks, extracts channel A (px 86..994), renders PNG.
#   usage: apt_sync.py <rail_rows.out> <out.png> [synth]   ('synth' -> also corr vs truth)
import sys, numpy as np
from PIL import Image

rows = {}
for l in open(sys.argv[1]):
    if not l.startswith("ROW"):
        continue
    p = l.split()
    if len(p) < 3 or not p[1].lstrip("-").isdigit():
        continue                                   # skip partial/raced/concatenated lines
    rows[int(p[1])] = [int(x) for x in p[2:] if x.lstrip("-").isdigit()]
stream = np.array([v for i in sorted(rows) for v in rows[i]], float)
LINE = 2080
nfull = len(stream)//LINE - 1

# sync template: high-contrast square over first ~39 px (matches synthetic;
# for real NOAA-APT use the 1040 Hz / 2-px-period Sync-A pattern)
synth = "synth" in sys.argv
if synth:
    tmpl = np.array([1.0 if (x//3)%2==0 else -1.0 for x in range(39)])
else:
    tmpl = np.array([1.0 if (x//2)%2==0 else -1.0 for x in range(39)])  # ~1040 Hz @ 4160px/s

def score(o):
    s = 0.0
    for L in range(nfull):
        seg = stream[o+L*LINE : o+L*LINE+39]
        if len(seg)==39: s += abs(np.dot(tmpl, seg - seg.mean()))
    return s

best_o = max(range(LINE), key=score)
img = np.array([stream[best_o+L*LINE : best_o+L*LINE+LINE] for L in range(nfull)])
chA = img[:, 86:995]
chA = (chA - chA.min())/(np.ptp(chA)+1e-9)
print(f"sync offset = {best_o}  image = {chA.shape}")

Image.fromarray((chA*255).astype(np.uint8), 'L').save(sys.argv[2])
print("wrote", sys.argv[2])

if synth:
    truth = np.load("/tmp/apt_truth.npy"); truth=(truth-truth.min())/(np.ptp(truth)+1e-9)
    nl = min(chA.shape[0], truth.shape[0]); m=min(chA.shape[1],truth.shape[1])
    a=chA[:nl,:m]; b=truth[:nl,:m]
    best=max(np.corrcoef(np.roll(a,h,axis=1).ravel(), b.ravel())[0,1] for h in range(0,909,2))
    print(f"corr(synced, truth) = {best:.4f}")
