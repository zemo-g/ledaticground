#!/usr/bin/env python3
# Validate src/acars_amdemod.rail. The module prints DC-removed envelope audio as "A <x>"
# lines. We confirm:
#   (1) the recovered audio cross-correlates with the clean MSK truth (zero-mean, the demod
#       is unsigned so there is no polarity ambiguity) -> corr > 0.85, AND
#   (2) the 1200/2400 Hz MSK tones are INTACT: per-bit Goertzel tone-energy on the recovered
#       audio (slice E(1200) > E(2400) -> bit 1) recovers the truth bits at low BER.
# The tone check is what proves "the MSK audio with the tones intact" demanded by the spec.
import sys, numpy as np

truth = np.load('/tmp/acars_amdemod_truth.npy').astype(np.float64)
bits  = np.load('/tmp/acars_amdemod_bits.npy').astype(np.int64)
meta  = np.load('/tmp/acars_amdemod_meta.npy')
fs, baud, sps, prekey = (int(x) for x in meta)

rec = []
for l in open(sys.argv[1]):
    if l.startswith('A '):
        rec.append(float(l.split()[1]))
rec = np.array(rec, np.float64)
if rec.size == 0:
    print('FAIL no audio lines'); sys.exit(1)

# drop the pre-key settling region; align to the modulated portion
aud = rec[prekey:prekey+len(truth)]
n = min(len(aud), len(truth))
aud = aud[:n]; t = truth[:n]
if n < sps*8:
    print(f'FAIL too short n={n}'); sys.exit(1)

# (1) zero-mean cross-correlation recovered-audio vs clean MSK
a = aud - aud.mean(); b = t - t.mean()
denom = (np.linalg.norm(a)*np.linalg.norm(b)) or 1.0
corr = float(np.dot(a, b)/denom)
corr_ok = corr > 0.85

# (2) tone-intact check: per-bit Goertzel energy at 1200 Hz and 2400 Hz on the RECOVERED audio
nbits = n // sps
c1 = np.cos(2*np.pi*1200.0*np.arange(sps)/fs); s1 = np.sin(2*np.pi*1200.0*np.arange(sps)/fs)
c0 = np.cos(2*np.pi*2400.0*np.arange(sps)/fs); s0 = np.sin(2*np.pi*2400.0*np.arange(sps)/fs)
rec_bits = np.zeros(nbits, np.int64)
for k in range(nbits):
    seg = aud[k*sps:(k+1)*sps]
    seg = seg - seg.mean()
    e1 = (seg@c1)**2 + (seg@s1)**2
    e0 = (seg@c0)**2 + (seg@s0)**2
    rec_bits[k] = 1 if e1 > e0 else 0
tb = bits[:nbits]
ber = float(np.mean(rec_bits != tb)) if nbits else 1.0
tone_ok = ber < 0.05

ok = corr_ok and tone_ok
status = 'PASS' if ok else 'FAIL'
print(f'samples {n}  corr(recovered_env, clean_MSK)={corr:.4f}  '
      f'tone_BER={ber:.4f} over {nbits} bits  {status}'
      + ('' if ok else f' (need corr>0.85 [{corr_ok}] and tone_BER<0.05 [{tone_ok}])'))
sys.exit(0 if ok else 1)
