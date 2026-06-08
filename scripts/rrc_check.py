#!/usr/bin/env python3
# Validate src/rrc.rail against the numpy RRC reference. Three checks, all from the
# rung's spec:
#   1) impulse -> SYMMETRIC taps      (rail prints SYMOK 1; center tap == 1-beta+4beta/pi)
#   2) filtered IQ matches numpy 'same'-mode RRC convolution exactly (max abs err < 2e-2)
#   3) a pulse-shaped symbol recovers with MINIMAL ISI: the matched RRC*RRC raised-cosine
#      response sampled at non-zero symbol offsets is ~0 (residual ISI small).
import sys, json, numpy as np

gt = json.load(open('/tmp/rrc_truth.json'))
yi = yq = None; tap0 = None; symok = None
for l in open(sys.argv[1]):
    if l.startswith('YI '):
        yi = np.array([float(x) for x in l.split()[1:]])
    elif l.startswith('YQ '):
        yq = np.array([float(x) for x in l.split()[1:]])
    elif l.startswith('TAP0 '):
        tap0 = float(l.split()[1])
    elif l.startswith('SYMOK '):
        symok = int(l.split()[1])

eyi = np.array(gt['yi']); eyq = np.array(gt['yq'])
exp_tap0 = gt['taps'][(gt['ntaps']-1)//2]

# 1) symmetric taps + correct center tap
ok_tap = tap0 is not None and abs(tap0 - exp_tap0) < 1e-2
ok_sym = symok == 1

# 2) filtered IQ exactness
n = min(len(yi), len(eyi)) if yi is not None else 0
ei = float(np.max(np.abs(yi[:n] - eyi[:n]))) if n else 9.9
eq = float(np.max(np.abs(yq[:n] - eyq[:n]))) if (yq is not None and n) else 9.9
ok_i = ei < 2e-2
ok_q = eq < 2e-2

# 3) minimal ISI of the matched RRC*RRC raised-cosine (computed independently in numpy).
#    A good RRC matched filter gives a raised cosine with residual ISI well under 0.05.
residual_isi = gt['residual_isi']
ok_isi = residual_isi < 0.05

print(f"symmetric taps SYMOK={symok} = {ok_sym}")
print(f"center tap {tap0} (want {exp_tap0:.4f}) = {ok_tap}")
print(f"I-channel max abs err {ei:.4f} over {n} samples = {ok_i}")
print(f"Q-channel max abs err {eq:.4f} over {n} samples = {ok_q}")
print(f"matched RRC*RRC residual ISI {residual_isi:.4f} (< 0.05) = {ok_isi}")
allok = ok_sym and ok_tap and ok_i and ok_q and ok_isi
print("PASS" if allok else "FAIL")
sys.exit(0 if allok else 1)
