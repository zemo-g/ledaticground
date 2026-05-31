#!/usr/bin/env python3.11
# Generate a synthetic NOAA-APT signal (known image) and a reference decode,
# to validate the Rail APT decoder. Format matches rtl_fm output: s16 @ 11025 Hz,
# image AM-modulated on a 2400 Hz subcarrier, 2 lines/s, 2080 px/line.
import numpy as np, sys

FS = 11025; SUB = 2400.0; PXRATE = 4160.0; LINEPX = 2080; NLINES = 120
spp = FS / PXRATE                      # samples per pixel ~2.65
npix = LINEPX * NLINES
nsamp = int(npix * spp)

def brightness(line, x):
    # channel A image region (px 86..994): diagonal sine stripes + gradient
    if 86 <= x < 995:
        u = (x - 86) / 909.0
        return 0.5 + 0.45*np.sin(2*np.pi*(u*6 + line/15.0))
    # sync A (px 0..38): hard square wave (7 cyc) for the correlator
    if x < 39:
        return 1.0 if (x // 3) % 2 == 0 else 0.0
    # everything else mid-gray
    return 0.5

if "--gen" in sys.argv:
    t = np.arange(nsamp) / FS
    pix = np.floor(t * PXRATE).astype(int)
    line = (pix // LINEPX); xin = (pix % LINEPX)
    b = np.array([brightness(l, x) for l, x in zip(line, xin)])
    s = b * np.sin(2*np.pi*SUB*t)
    pcm = np.clip(s*30000, -32767, 32767).astype(np.int16)
    pcm.tofile("/tmp/apt_test.s16")
    # save the ground-truth channel-A image
    img = np.zeros((NLINES, 909))
    for l in range(NLINES):
        for x in range(909):
            img[l, x] = brightness(l, 86 + x)
    np.save("/tmp/apt_truth.npy", img)
    print(f"GEN nsamp={nsamp} lines={NLINES} -> /tmp/apt_test.s16")

if "--ref" in sys.argv:
    s = np.fromfile("/tmp/apt_test.s16", dtype=np.int16).astype(np.float64)
    t = np.arange(len(s)) / FS
    # quadrature AM envelope at 2400 Hz
    I = s*np.cos(2*np.pi*SUB*t); Q = s*np.sin(2*np.pi*SUB*t)
    k = int(round(FS/SUB))                 # ~5-sample moving average
    box = np.ones(k)/k
    env = np.sqrt(np.convolve(I,box,'same')**2 + np.convolve(Q,box,'same')**2)
    # resample env to pixels
    px_idx = (np.arange(npix) * spp).astype(int)
    px_idx = np.clip(px_idx, 0, len(env)-1)
    pixels = env[px_idx].reshape(NLINES, LINEPX)
    chA = pixels[:, 86:995]
    chA = (chA - chA.min())/(np.ptp(chA)+1e-9)
    truth = np.load("/tmp/apt_truth.npy"); truth=(truth-truth.min())/(np.ptp(truth)+1e-9)
    cc = np.corrcoef(chA.ravel(), truth.ravel())[0,1]
    print(f"REF decode corr(recovered, truth) = {cc:.4f}")
