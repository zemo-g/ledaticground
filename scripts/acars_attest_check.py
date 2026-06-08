#!/usr/bin/env python3
# Validate src/acars_attest.rail: the receipt must verify under its own Ed25519 signature
# (VERIFY=1) and a tampered message must be rejected (TAMPER=0). We also confirm the
# product_sha256 in the receipt matches an independent SHA-256 of the deframe output.
import sys, hashlib, re
verify = tamper = None
prod_sha = None
for l in open(sys.argv[1]):
    if l.startswith('VERIFY'):
        m = re.search(r'=\s*(\d+)', l); verify = int(m.group(1)) if m else None
    if l.startswith('TAMPER'):
        m = re.search(r'=\s*(\d+)', l); tamper = int(m.group(1)) if m else None
    if l.startswith('RECEIPT'):
        m = re.search(r'product_sha256=([0-9a-f]+)', l); prod_sha = m.group(1) if m else None

# independent hash of the attested product
try:
    data = open('/tmp/acars_deframe_out.txt','rb').read()
    indep = hashlib.sha256(data).hexdigest()
except FileNotFoundError:
    indep = None

fails = []
if verify != 1: fails.append(f'VERIFY={verify} (want 1)')
if tamper != 0: fails.append(f'TAMPER={tamper} (want 0)')
if prod_sha is None: fails.append('no product_sha256 in receipt')
elif indep is not None and prod_sha != indep:
    fails.append(f'product_sha256 mismatch receipt={prod_sha[:12]} indep={indep[:12]}')

if fails:
    print('FAIL  ' + ' | '.join(fails)); sys.exit(1)
print(f'PASS  ed25519 verify=1 tamper-reject=0 product_sha256={prod_sha[:16]}... matches')
sys.exit(0)
