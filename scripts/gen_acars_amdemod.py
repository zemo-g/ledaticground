#!/usr/bin/env python3
# Synthetic ACARS RF for src/acars_amdemod.rail (AM envelope demod rung).
# ACARS (ARINC 618) is AM: a VHF carrier amplitude-modulated by a 2400-baud MSK subcarrier.
# MSK tones: bit 1 -> 1200 Hz, bit 0 -> 2400 Hz (continuous-phase, h=0.5). The capture is
# downconverted to baseband (0-Hz-offset carrier) and quantized to int8 IQ -> /tmp/acars_in.s8.
#
# The envelope demod recovers |z| = (1 + m*msk)*carrier; after DC removal that is m*msk
# (the MSK audio with both tones intact). We store the clean MSK audio + the data bits as
# ground truth so the checker can confirm: (a) the audio correlates with the clean MSK, and
# (b) the 1200/2400 Hz tones survive (per-bit tone energy).
#
# Cross-pol caveat: ACARS is terrestrial VERTICALLY polarized; a horizontal antenna costs
# 15-20 dB. Default operating point is a comfortable SNR (envelope demod is low-SNR tolerant
# but noise-rectifies as SNR drops). Pass --snr 6 to exercise the degraded regime.
import numpy as np, sys, math

fs   = 48000
baud = 2400
sps  = fs // baud           # 20 samples / bit

def argf(flag, d):
    return float(sys.argv[sys.argv.index(flag)+1]) if flag in sys.argv else d
def argi(flag, d):
    return int(sys.argv[sys.argv.index(flag)+1]) if flag in sys.argv else d

N    = argi('--n', 200)     # data bits
snr  = argf('--snr', 16.0)  # dB operating point
m    = argf('--m', 0.8)     # AM modulation depth
rng  = np.random.default_rng(42)

bits = rng.integers(0, 2, N).astype(np.uint8)

# MSK audio: continuous-phase FSK, bit 1 -> 1200 Hz, bit 0 -> 2400 Hz.
f1, f0 = 1200.0, 2400.0
inst = np.where(np.repeat(bits, sps) == 1, f1, f0).astype(np.float64)
ph   = 2*np.pi*np.cumsum(inst)/fs
msk  = np.cos(ph)                          # clean MSK audio, range [-1, 1]

# AM modulate onto a 0-Hz baseband carrier: z = (1 + m*msk) * exp(j*0) (purely real envelope)
env  = 1.0 + m*msk
z    = env.astype(np.complex128)

# constant carrier-only pre-key so the demod has a settling region (no modulation)
pre  = np.ones(2*sps, np.complex128)
z    = np.concatenate([pre, z])

# AWGN at requested SNR (signal power ~ mean|z|^2)
sigpow = np.mean(np.abs(z)**2)
npow   = sigpow / (10**(snr/10))
noise  = (rng.standard_normal(len(z)) + 1j*rng.standard_normal(len(z))) * math.sqrt(npow/2)
z      = z + noise

A   = 70.0
i8  = np.clip(np.round(z.real*A), -127, 127).astype(np.int8)
q8  = np.clip(np.round(z.imag*A), -127, 127).astype(np.int8)
iq  = np.empty(2*len(z), np.int8); iq[0::2]=i8; iq[1::2]=q8
iq.tofile('/tmp/acars_in.s8')

# ground truth: clean MSK audio (post-prekey), data bits, and meta (fs/baud/sps/prekey-len).
np.save('/tmp/acars_amdemod_truth.npy', msk.astype(np.float32))
np.save('/tmp/acars_amdemod_bits.npy', bits)
np.save('/tmp/acars_amdemod_meta.npy', np.array([fs, baud, sps, len(pre)]))
print(f'{N} bits, fs={fs}, sps={sps}, baud={baud}, m={m}, snr={snr}dB, '
      f'prekey={2*sps} samp -> /tmp/acars_in.s8 ({len(z)} samples)')
