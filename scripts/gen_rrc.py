#!/usr/bin/env python3
# Reference vector for src/rrc.rail — the CONFIGURABLE-sps root-raised-cosine matched
# filter at the FRONT of the Meteor-M2 LRPT QPSK chain (feeds Gardner timing -> qpsk Costas).
#
# Meteor-M2 LRPT uses RRC pulse shaping (roll-off beta ~= 0.6). The receiver applies a
# matched RRC so combined Tx-RRC * Rx-RRC = raised-cosine (zero ISI at symbol centers).
#
# Two things this generator emits so the checker can verify the rung's spec exactly:
#   1) An int8 IQ test block (impulses + a few unit symbols) -> /tmp/rrc_in.s8. Passing
#      it through the matched RRC reproduces the (scaled) RRC taps; numpy 'same' convolution
#      is the exact ground truth for YI/YQ.
#   2) A separate IDEAL raised-cosine response (Tx-RRC * Rx-RRC, full convolution) so the
#      checker can confirm the matched filter gives MINIMAL ISI: the combined response is
#      ~1 at the symbol center and ~0 at all other integer-symbol offsets.
# Also writes the cfg file /tmp/rrc_cfg.txt = "sps beta span" that the rail rung reads.
import numpy as np, sys, json

SPS  = int(sys.argv[sys.argv.index('--sps')+1])  if '--sps'  in sys.argv else 4
BETA = float(sys.argv[sys.argv.index('--beta')+1]) if '--beta' in sys.argv else 0.6
SPAN = int(sys.argv[sys.argv.index('--span')+1]) if '--span' in sys.argv else 4   # symbols each side
NTAPS = 2*SPS*SPAN + 1

def rrc_taps(beta, sps, span):
    N = 2*sps*span + 1
    t = (np.arange(N) - (N-1)/2) / sps   # symbol units
    h = np.zeros(N)
    for i, ti in enumerate(t):
        if abs(ti) < 1e-8:
            h[i] = 1.0 - beta + 4*beta/np.pi
        elif beta > 0 and abs(abs(4*beta*ti) - 1.0) < 1e-8:
            h[i] = (beta/np.sqrt(2)) * (
                (1+2/np.pi)*np.sin(np.pi/(4*beta)) +
                (1-2/np.pi)*np.cos(np.pi/(4*beta)))
        else:
            num = (np.sin(np.pi*ti*(1-beta)) +
                   4*beta*ti*np.cos(np.pi*ti*(1+beta)))
            den = np.pi*ti*(1-(4*beta*ti)**2)
            h[i] = num/den
    return h

h = rrc_taps(BETA, SPS, SPAN)

# --- write cfg for the rail rung (configurable sps/beta/span). beta is carried as an
# INTEGER in hundredths (e.g. 0.6 -> 60); the rung parses three plain ints and /100.0 the
# beta (a decimal-point float token miscompiled on file-read bytes in Rail). ---
open('/tmp/rrc_cfg.txt','w').write(f"{SPS} {round(BETA*100)} {SPAN}\n")

# --- input: impulses + a couple of unit symbols, amplitude 100 ---
NSYM_IN = 6
A = 100.0
nin = SPS * (NSYM_IN + 2*SPAN)
xi = np.zeros(nin); xq = np.zeros(nin)
xi[SPS*1] = A     # I-channel impulse
xq[SPS*3] = A     # Q-channel impulse one+ symbol later
xi[SPS*4] = -A    # unit symbols
xq[SPS*4] = A

i8 = np.clip(np.round(xi), -127, 127).astype(np.int8)
q8 = np.clip(np.round(xq), -127, 127).astype(np.int8)
iq = np.empty(2*nin, np.int8); iq[0::2]=i8; iq[1::2]=q8
iq.tofile('/tmp/rrc_in.s8')

# --- exact ground truth: 'same'-mode convolution of input with the RRC taps ---
yi = np.convolve(xi, h, mode='same')
yq = np.convolve(xq, h, mode='same')

# --- minimal-ISI ground truth: matched RRC*RRC = raised cosine ---
# combined response sampled at symbol centers should be a Kronecker delta (zero ISI).
rc = np.convolve(h, h, mode='full')                  # length 2*NTAPS-1
rc = rc / rc.max()                                   # normalize peak to 1
peak = int(np.argmax(rc))
# sample at integer-symbol offsets around the peak
offsets = range(-SPAN, SPAN+1)
isi_samples = {}
for k in offsets:
    idx = peak + k*SPS
    if 0 <= idx < len(rc):
        isi_samples[k] = float(rc[idx])
# max |response| at any NON-zero symbol offset = the residual ISI
residual_isi = max(abs(v) for k,v in isi_samples.items() if k != 0)

json.dump({"sps":SPS,"beta":BETA,"span":SPAN,"ntaps":NTAPS,"nin":nin,
           "taps":[float(x) for x in h],
           "yi":[float(x) for x in yi],"yq":[float(x) for x in yq],
           "rc_center":isi_samples.get(0,0.0),
           "residual_isi":residual_isi},
          open('/tmp/rrc_truth.json','w'))
print(f"rrc sps={SPS} beta={BETA} span={SPAN} ntaps={NTAPS}, {nin} input samples, "
      f"center tap h[0]={h[(NTAPS-1)//2]:.4f}, matched RRC*RRC residual ISI={residual_isi:.4f}")
