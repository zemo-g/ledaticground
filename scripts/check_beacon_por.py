#!/usr/bin/env python3
# beacon_por checker: validate src/beacon_por.rail proof-of-reception receipt.
# PASS iff ALL of:
#   VERIFY == 1   (the node's own signature is accepted)
#   TAMPER == 0   (a modified message is rejected)
#   the Rail-computed PAYLOAD_SHA256 matches the independent Python sha256 (truth)
#   the receipt is explicitly labeled SYNTHETIC_TEST  (honesty guard -- a real-looking
#     receipt over a synthetic detection must be impossible to emit)
#   geo is a PENDING field  (no fabricated geolocation -- honest pending)
# Usage: check_beacon_por.py <rail-stdout-file>
import sys, hashlib

truth_sha = open('/tmp/beacon_por_truth.txt').read().strip()

verify = tamper = None
rail_sha = None
receipt = None
for l in open(sys.argv[1]):
    if l.startswith('VERIFY'):
        verify = int(l.split('=')[1].split()[0])
    elif l.startswith('TAMPER'):
        tamper = int(l.split('=')[1].split()[0])
    elif l.startswith('PAYLOAD_SHA256 '):
        rail_sha = l.strip().split()[1]
    elif l.startswith('RECEIPT'):
        receipt = l.strip()[len('RECEIPT'):].strip()

ok = True
msgs = []

if verify != 1:
    ok = False; msgs.append(f'VERIFY={verify} (want 1)')
else:
    msgs.append('VERIFY=1')

if tamper != 0:
    ok = False; msgs.append(f'TAMPER={tamper} (want 0)')
else:
    msgs.append('TAMPER=0')

if rail_sha is None:
    ok = False; msgs.append('no PAYLOAD_SHA256')
elif rail_sha != truth_sha:
    ok = False; msgs.append(f'sha mismatch rail={rail_sha[:12]} py={truth_sha[:12]}')
else:
    msgs.append('sha=match')

# honesty guard: the receipt MUST be labeled SYNTHETIC_TEST
if receipt is None or 'SYNTHETIC_TEST' not in receipt:
    ok = False; msgs.append('NOT labeled SYNTHETIC_TEST (honesty FAIL)')
else:
    msgs.append('labeled=SYNTHETIC_TEST')

# honesty guard: geolocation must be honestly PENDING, never fabricated
if receipt is None or 'geo=PENDING' not in receipt:
    ok = False; msgs.append('geo not PENDING (honesty FAIL)')
else:
    msgs.append('geo=PENDING')

print(f'beacon_por: {"PASS" if ok else "FAIL"}  ' + '  '.join(msgs))
sys.exit(0 if ok else 1)
