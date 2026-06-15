#!/usr/bin/env python3
# RS41-6 checker: assert the two-receipt attestation invariants from the Rail output:
#   - VERIFY_A == 1, TAMPER_A == 0  (Receipt A self-verifies, tamper rejected)
#   - VERIFY_B == 1, TAMPER_B == 0  (Receipt B self-verifies, tamper rejected)
#   - v=2 type=FACT in Receipt A, v=2 type=INFERENCE in Receipt B (contract-conformant types)
#   - Receipt B.derived_from == Receipt A.chain_hash AND Receipt B.prev == A.chain_hash
#     (the inference is cryptographically bound to the fact it was derived from)
#   - BINDING == 1, ATTEST_STATUS PASS
#   - geo honestly PENDING_needs_GPS_PPS (no fabricated receiver coords)
import sys, re

vals = {}
recA = recB = None
for l in open(sys.argv[1]):
    l = l.rstrip('\n')
    if l.startswith('RECEIPT_A '): recA = l[10:]
    elif l.startswith('RECEIPT_B '): recB = l[10:]
    elif l.startswith('CHAIN_A '): vals['chainA'] = l.split()[1]
    elif l.startswith('CHAIN_B '): vals['chainB'] = l.split()[1]
    elif l.startswith('VERIFY_A '): vals['vA'] = int(re.search(r'accepted = (\d)', l).group(1))
    elif l.startswith('TAMPER_A '): vals['tA'] = int(re.search(r'accepted = (\d)', l).group(1))
    elif l.startswith('VERIFY_B '): vals['vB'] = int(re.search(r'accepted = (\d)', l).group(1))
    elif l.startswith('TAMPER_B '): vals['tB'] = int(re.search(r'accepted = (\d)', l).group(1))
    elif l.startswith('BINDING '): vals['bound'] = int(re.search(r'= (\d) \(want', l).group(1))
    elif l.startswith('ATTEST_STATUS '): vals['status'] = l[14:].split()[0]

ok = True; msgs = []
def chk(cond, label):
    global ok
    if cond: msgs.append(label)
    else: ok = False; msgs.append('!' + label)

chk(vals.get('vA') == 1, 'verifyA=1')
chk(vals.get('tA') == 0, 'tamperA=0')
chk(vals.get('vB') == 1, 'verifyB=1')
chk(vals.get('tB') == 0, 'tamperB=0')
chk(recA is not None and 'RS41_DECODE_RECEIPT' in recA and 'v=2' in recA and 'type=FACT' in recA and 'rs_ok=' in recA and 'product_sha256=' in recA, 'A=v2-FACT')
chk(recB is not None and 'RS41_INFERENCE_RECEIPT' in recB and 'v=2' in recB and 'type=INFERENCE' in recB and 'derived_from=' in recB, 'B=v2-INFERENCE')

# binding: B's derived_from= must equal A's chain_hash (the cross-ledger FACT binding).
# B's prev= threads the INFERENCE ledger's own chain (GENESIS on line 1), NOT chainA.
chainA = vals.get('chainA')
if recB and chainA:
    df = re.search(r'derived_from=([0-9a-f]+)', recB)
    pv = re.search(r'prev=(GENESIS|[0-9a-f]+)', recB)
    chk(df is not None and df.group(1) == chainA, 'derived_from==chainA')
    chk(pv is not None, 'prev_present')
else:
    ok = False; msgs.append('!binding_parse')

chk(vals.get('bound') == 1, 'BINDING=1')
chk(vals.get('status') == 'PASS', f"status={vals.get('status')}")
# honest geo (no fabricated receiver coords)
chk(recA is not None and 'geo=PENDING_needs_GPS_PPS' in recA, 'geo_PENDING')

print(f'rs41_attest: {"PASS" if ok else "FAIL"}  ' + '  '.join(msgs))
sys.exit(0 if ok else 1)
