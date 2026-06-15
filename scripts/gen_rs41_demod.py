#!/usr/bin/env python3
# RS41-2 generator: a bits-only synthetic RS41 2-FSK vector at a known sps, for the
# demod checker. Re-uses gen_rs41_gen.py's modulation but with a SHORT random NRZ
# bit stream (no frame structure needed -- just proves the discriminator + integrate-
# and-dump recovers the planted bits). Writes /tmp/rs41_in.s8 + /tmp/rs41_bits.npy +
# /tmp/rs41_sps.txt. SYNTHETIC-ONLY.
import numpy as np, sys, math

def argf(n, d): return float(sys.argv[sys.argv.index(n)+1]) if n in sys.argv else d
def argi(n, d): return int(sys.argv[sys.argv.index(n)+1]) if n in sys.argv else d

fs   = argi('--fs', 48000)
baud = argi('--baud', 4800)
sps  = fs // baud
snr  = argf('--snr', 25.0)
dev  = argf('--dev', 2400.0)
BT   = argf('--bt', 0.5)
nbits = argi('--nbits', 800)

rng = np.random.default_rng(argi('--seed', 4806))
# a few leading 1s (preamble-ish) then random payload bits
preamble = [1, 0]*8
payload = rng.integers(0, 2, nbits).tolist()
channel = preamble + payload

nrz = np.repeat(np.array([1.0 if b == 1 else -1.0 for b in channel]), sps)
def gaussian_taps(bt, sps, span=3):
    # standard GFSK gaussian pulse: sigma (in samples) = sqrt(ln2)/(2*pi*BT) * sps
    n = np.arange(-span*sps, span*sps+1)
    sigma = math.sqrt(math.log(2))/(2*math.pi*bt)*sps
    h = np.exp(-(n**2)/(2*sigma**2))
    return h/np.sum(h)
shaped = np.convolve(nrz, gaussian_taps(BT, sps), mode='same')
ph = np.cumsum(shaped * 2*np.pi*dev/fs)
z = np.exp(1j*ph)
r2 = np.random.default_rng(99)
sigma = math.sqrt(1.0/(10**(snr/10))/2.0)
z = z + (r2.standard_normal(len(z)) + 1j*r2.standard_normal(len(z)))*sigma
A = 90.0
i8 = np.clip(np.round(z.real*A), -127, 127).astype(np.int8)
q8 = np.clip(np.round(z.imag*A), -127, 127).astype(np.int8)
iq = np.empty(2*len(z), np.int8); iq[0::2] = i8; iq[1::2] = q8
iq.tofile('/tmp/rs41_in.s8')
np.save('/tmp/rs41_bits.npy', np.array(channel, np.uint8))
open('/tmp/rs41_sps.txt', 'w').write(f'{sps}\n')
print(f'rs41 demod vector: {len(channel)} channel bits, baud={baud} sps={sps} '
      f'dev={dev} snr={snr} -> /tmp/rs41_in.s8 ({len(iq)} int8)')
