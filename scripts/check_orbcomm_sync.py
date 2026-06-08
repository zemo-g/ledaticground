#!/usr/bin/env python3
# Validates src/orbcomm_sync.rail — Orbcomm carrier/timing recovery + frame sync.
#
# The headline test (per the rung spec): the SYNC WORD is LOCATED IN THE STREAM AT AN
# OFFSET. We placed the packet at an unknown sample offset (PKT_OFF symbols of leading
# idle) in an OVERSAMPLED IQ stream with a residual carrier offset. The Rail rung must
# recover symbol timing, differential-demod (carrier-phase-invariant), and report the
# unique word at the expected DECODED offset with a high correlation score.
#
# PASS (all of):
#   1. UW found at the expected decoded offset (uw_decoded_start) with score >= 15/16,
#   2. timing phase in [0, sps),
#   3. data-region BER < 5% (timing + demod actually recovered the bits, not just luck),
#   4. carrier-offset estimate within +/- 0.02 cyc/sym of truth (carrier recovery).
import sys, numpy as np

uw          = np.load('/tmp/orbcomm_sync_uw.npy')
data        = np.load('/tmp/orbcomm_sync_data.npy')
uw_start    = int(np.load('/tmp/orbcomm_sync_uwstart.npy')[0])
meta        = np.load('/tmp/orbcomm_sync_meta.npy')   # [sps,pktoff,npre,nuw,ndata,total,expphase]
foff_truth  = float(np.load('/tmp/orbcomm_sync_foff.npy')[0])
SPS = int(meta[0])
exp_phase = int(meta[6]) if len(meta) > 6 else None

rec=None; uw_at=None; uw_score=None; phase=None; carrier=None
for l in open(sys.argv[1]):
    if l.startswith('SYM '):
        rec = np.array([int(c) for c in l.strip()[4:]], dtype=np.uint8)
    elif l.startswith('UW_AT '):
        p=l.split(); uw_at=int(p[1]); uw_score=int(p[3])
    elif l.startswith('TIMING '):
        for tok in l.split():
            if tok.startswith('phase='): phase=int(tok.split('=')[1])
    elif l.startswith('CARRIER '):
        carrier=float(l.split()[1])

if rec is None:    print('FAIL: no SYM line');    sys.exit(1)
if uw_at is None:  print('FAIL: no UW_AT line');  sys.exit(1)
if phase is None:  print('FAIL: no TIMING line'); sys.exit(1)

# 1. frame sync: UW at the expected decoded offset, high score
sync_ok = (uw_at == uw_start) and (uw_score >= 15)

# 2. timing phase: in range, and (if an intra-symbol shift was injected) within +/-1 sample
# of the expected best phase, modulo sps (an off-by-one timing phase is harmless since
# integrate-and-dump over sps samples is broad; the BER check below catches real failures).
def phase_close(a, b, sps):
    d = (a - b) % sps
    return d <= 1 or d >= sps - 1
phase_in_range = (phase is not None) and (0 <= phase < SPS)
phase_ok = phase_in_range and (exp_phase is None or phase_close(phase, exp_phase, SPS))

# 3. data BER: data bits start right after the UW in the decoded stream
data_start = uw_at + len(uw)
rec_data = rec[data_start:data_start+len(data)]
dnb = min(len(rec_data), len(data))
data_ber = float(np.mean(rec_data[:dnb] != data[:dnb])) if dnb else 1.0

# 4. carrier offset within tolerance (cyc/sym)
carrier_ok = (carrier is not None) and (abs(carrier - foff_truth) < 0.02)

print(f'UW@{uw_at} (expect {uw_start}) score {uw_score}/16  phase={phase}/{SPS} '
      f'(expect {exp_phase})  data_BER {data_ber:.4f}  carrier {carrier} (true {foff_truth:.4f})')
print(f'  sync_ok={sync_ok} phase_ok={phase_ok} ber_ok={data_ber<0.05} carrier_ok={carrier_ok}')

ok = sync_ok and phase_ok and (data_ber < 0.05) and carrier_ok
print('PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
