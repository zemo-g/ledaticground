#!/usr/bin/env python3
# Validate src/framesync.rail output against /tmp/framesync_truth.json.
# Checks:
#   1. ASM found at the correct bit OFFSET,
#   2. correct QPSK DE-ROTATION recovered,
#   3. the emitted DE-ROTATED SOFT values match the ground-truth de-rotated soft exactly
#      (the decoder just selects + sign-transforms the int8 soft samples, so this is exact),
#   4. the implied hard bits of those soft values match the clean payload hard bits
#      (the actual recovery objective: the soft sign carries the right bit despite noise).
import sys, json
gt = json.load(open('/tmp/framesync_truth.json'))
off = rot = None
soft = None
for l in open(sys.argv[1]):
    if l.startswith('SYNC offset='):
        toks = l.split()
        off = int(toks[1].split('=')[1])
        rot = int(toks[2].split('=')[1])
    if l.startswith('SOFT '):
        soft = [int(x) for x in l.split()[1:]]

ok_off = (off == gt['offset'])
ok_rot = (rot == gt['rotation'])

exp_soft = gt['expected_soft']
ok_soft = (soft is not None and soft == exp_soft)

# implied hard bits of the emitted soft, vs the CLEAN payload hard bits. With the chosen
# AMP/NOISE the noise should not flip any bit, so this should be exact too.
n = gt['data_bits']
clean_hard = gt['payload_hard_clean']
got_hard = [0 if v >= 0 else 1 for v in (soft or [])]
nflip = sum(1 for a, b in zip(got_hard[:n], clean_hard[:n]) if a != b) if soft else n
ber = nflip / n if n else 1.0
ok_ber = (ber <= 0.02)   # de-rotated soft sign recovers the bits at < 2% BER

print(f"ASM offset {off} (want {gt['offset']}) = {ok_off}")
print(f"QPSK de-rotation {rot} (want {gt['rotation']}) = {ok_rot}")
print(f"emitted de-rotated soft exact ({len(soft) if soft else 0} vals) = {ok_soft}")
print(f"de-rotated hard bits vs clean payload: {nflip}/{n} flips, BER {ber:.4f} = {ok_ber}")
allok = ok_off and ok_rot and ok_soft and ok_ber
print("PASS" if allok else "FAIL")
sys.exit(0 if allok else 1)
