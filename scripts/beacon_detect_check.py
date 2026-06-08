#!/usr/bin/env python3
# Rung 1 checker: validate src/beacon_detect.rail output vs ground truth.
# PASS if: DETECT==1, the reported absolute freq is within 1 FFT bin of truth,
# and the reported SNR is within 4 dB of the requested in-band SNR.
import sys, numpy as np

truth = np.load('/tmp/beacon_detect_truth.npy')
foff, f_abs, det_snr, fc, fs, nfft = truth[:6]
inband_snr = truth[6] if len(truth) > 6 else det_snr
bin_hz = fs / nfft

det = None; abs_mhz = None; off_hz = None; snr_db = None
for l in open(sys.argv[1]):
    if l.startswith('DETECT '):  det = int(l.split()[1])
    if l.startswith('CARRIER'):
        for tok in l.split():
            if tok.startswith('offsetHz='): off_hz = float(tok.split('=')[1])
            if tok.startswith('absMHz='):   abs_mhz = float(tok.split('=')[1])
    if l.startswith('SNR '):     snr_db = float(l.split()[1])

ok = True; msgs = []
if det != 1:
    ok = False; msgs.append(f'DETECT={det} (want 1)')
if abs_mhz is None:
    ok = False; msgs.append('no CARRIER absMHz')
else:
    df_hz = abs(abs_mhz*1e6 - f_abs*1e6)
    msgs.append(f'freq err={df_hz:.1f}Hz ({df_hz/bin_hz:.2f} bins)')
    if df_hz > 1.5*bin_hz: ok = False
if snr_db is None:
    ok = False; msgs.append('no SNR')
else:
    de = abs(snr_db - det_snr)
    msgs.append(f'SNR rep={snr_db:.1f} expect={det_snr:.1f} err={de:.1f}dB')
    if de > 3.0: ok = False

print(f'beacon_detect: {"PASS" if ok else "FAIL"}  ' + '  '.join(msgs))
print(f'  truth: {f_abs:.6f}MHz off={foff:.0f}Hz in-band(per-bin)={inband_snr:.0f}dB '
      f'expected-detector-SNR={det_snr:.1f}dB bin={bin_hz:.1f}Hz')
sys.exit(0 if ok else 1)
