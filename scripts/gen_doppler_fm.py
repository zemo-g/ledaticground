#!/usr/bin/env python3
# Realistic synthetic for validating doppler_real.rail BEFORE the live NOAA-19 pass.
# Emits 105 narrowband-FM snapshots (APT-like: 17 kHz deviation, 2400 Hz subcarrier
# + video tones) whose carrier CENTER follows a known Doppler S-curve plus a constant
# offset (simulated SDR ppm / carrier error) plus a DC spike (rtl_sdr artifact) and
# noise. Mirrors the real noaa19_doppler.sh capture format (uint8 IQ, 60 kHz, 16384
# samples/snapshot) so the exact same pipeline runs on synthetic and on real data.
import numpy as np, os
fs = 60000; N = 16384; nsnap = 105; dt_snap = 7.3
D = '/tmp/dopcap_synth'; os.makedirs(D, exist_ok=True)
t_snap = np.arange(nsnap) * dt_snap
T = t_snap[-1]
tc = 0.52 * T                                   # zero-crossing near closest approach
dop_truth = -2300 * np.tanh((t_snap - tc) / (0.16 * T)) + 700   # ~ +3000 .. -1600 Hz
const_off = 1850.0                              # nuisance constant (ppm + carrier err)
kf = 17000.0                                    # FM deviation (Hz) — APT-like
rng = np.random.default_rng(7)
n = np.arange(N) / fs
t0 = 1780280000
with open(f'{D}/times.txt', 'w') as tf:
    for i in range(nsnap):
        fc = dop_truth[i] + const_off
        m = 0.6*np.sin(2*np.pi*2400*n) + 0.25*np.sin(2*np.pi*900*n) + 0.15*np.sin(2*np.pi*4160*n)
        phi = 2*np.pi*fc*n + 2*np.pi*kf*np.cumsum(m)/fs
        z = np.exp(1j*phi)
        z += (rng.standard_normal(N) + 1j*rng.standard_normal(N)) * 0.15   # noise floor
        z += 0.45                                                          # DC spike
        I = np.clip(127.5 + 90*z.real, 0, 255).astype(np.uint8)
        Q = np.clip(127.5 + 90*z.imag, 0, 255).astype(np.uint8)
        iq = np.empty(2*N, np.uint8); iq[0::2] = I; iq[1::2] = Q
        iq.tofile(f'{D}/snap_{i:03d}.iq')
        tf.write(f'{i} {t0 + int(round(t_snap[i]))}\n')
np.save(f'{D}/dop_truth.npy', dop_truth)
np.save(f'{D}/t_snap.npy', t_snap)
with open('/tmp/dop_real.iq', 'wb') as out:                # concat in time order
    for i in range(nsnap):
        with open(f'{D}/snap_{i:03d}.iq', 'rb') as s:
            out.write(s.read())
print(f'wrote {nsnap} snaps to {D}; truth Doppler {dop_truth[0]:.0f}..{dop_truth[-1]:.0f} Hz, '
      f'const_off {const_off:.0f} Hz; concat -> /tmp/dop_real.iq')
