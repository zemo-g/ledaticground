#!/usr/bin/env python3
# Validates src/orbcomm_sdpsk.rail SD-PSK differential demod + frame sync against ground truth.
# Differential decode has NO phase ambiguity for the *transitions* (the bit IS the transition),
# so the decoded stream should match decoded_truth exactly except for noise-induced bit errors.
# PASS = symbol rate reported 4800 AND unique word found at the expected decoded index with a
# high score AND post-UW data BER < 5%.
import sys, numpy as np

truth = np.load('/tmp/orbcomm_sdpsk_truth.npy')          # decoded bits (tx_bits[1:])
uw    = np.load('/tmp/orbcomm_sdpsk_uw.npy')
data  = np.load('/tmp/orbcomm_sdpsk_databits.npy')
uw_start_exp = int(np.load('/tmp/orbcomm_sdpsk_uw_start.npy')[0])

rec = None; uw_at = None; uw_score = None; symrate = None
for l in open(sys.argv[1]):
    if l.startswith('BITS '):
        rec = np.array([int(c) for c in l.strip()[5:]], dtype=np.uint8)
    elif l.startswith('UW_AT '):
        p = l.split()
        uw_at = int(p[1]); uw_score = int(p[3])
    elif l.startswith('SYMRATE '):
        symrate = int(l.split()[1])
if rec is None:
    print('FAIL: no BITS line'); sys.exit(1)
if uw_at is None:
    print('FAIL: no UW_AT line'); sys.exit(1)
if symrate is None:
    print('FAIL: no SYMRATE line'); sys.exit(1)

nb = min(len(truth), len(rec))
ber = float(np.mean(rec[:nb] != truth[:nb]))

# frame-sync correctness: UW found at the expected decoded offset (differential decode is
# deterministic, so allow exact), with score >= 15/16 at moderate SNR.
sync_ok = (uw_at == uw_start_exp) and (uw_score >= 15)

# data-region BER: data bits start right after the UW in the decoded stream.
data_start = uw_start_exp + len(uw)
rec_data = rec[data_start:data_start+len(data)]
data_nb = min(len(rec_data), len(data))
data_ber = float(np.mean(rec_data[:data_nb] != data[:data_nb])) if data_nb else 1.0

rate_ok = (symrate == 4800)

print(f'decoded {nb} bits  overall_BER {ber:.4f}  data_BER {data_ber:.4f}  '
      f'UW@{uw_at} (expect {uw_start_exp}) score {uw_score}/16  sym_rate={symrate}  '
      f'sync_ok={sync_ok} rate_ok={rate_ok}')
ok = sync_ok and rate_ok and (data_ber < 0.05)
print('PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
