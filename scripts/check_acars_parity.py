#!/usr/bin/env python3
# Validate src/acars_parity.rail against /tmp/acars_parity_truth.json.
# The rung is correct iff:
#   CLEAN frame  -> PARITY_ERRORS 0, BCS_RX == BCS_CALC == truth BCS, BCS_OK 1
#   FLIP  frame  -> a single bit flip is DETECTED: PARITY_ERRORS >= 1 AND BCS_OK 0
# i.e. it actually flags corruption (not vacuously passing) and accepts a clean frame.
import sys, json
t = json.load(open('/tmp/acars_parity_truth.json'))

out = {}
keys = ('CHARS', 'PARITY_ERRORS', 'BCS_RX', 'BCS_CALC', 'BCS_OK')
for l in open(sys.argv[1]):
    l = l.rstrip('\n')
    for tag in ('CLEAN_', 'FLIP_'):
        if l.startswith(tag):
            rest = l[len(tag):]
            for key in keys:
                if rest.startswith(key + ' '):
                    out[tag + key] = rest[len(key) + 1:]

fails = []
def chk(name, got, want):
    if got != want:
        fails.append(f'{name}: got {got!r} want {want!r}')

# --- clean frame: everything must check out ---
chk('CLEAN chars',         out.get('CLEAN_CHARS'),         str(t['n_msg']))
chk('CLEAN parity_errors', out.get('CLEAN_PARITY_ERRORS'), '0')
chk('CLEAN bcs_rx',        out.get('CLEAN_BCS_RX'),        str(t['bcs']))
chk('CLEAN bcs_calc',      out.get('CLEAN_BCS_CALC'),      str(t['bcs']))
chk('CLEAN bcs_ok',        out.get('CLEAN_BCS_OK'),        '1')

# --- flipped frame: the single bit flip must be DETECTED by BOTH layers ---
fp = out.get('FLIP_PARITY_ERRORS')
try:
    fp_n = int(fp) if fp is not None else -1
except ValueError:
    fp_n = -1
if fp_n < 1:
    fails.append(f'FLIP parity not flagged: PARITY_ERRORS got {fp!r} want >=1')
chk('FLIP bcs_ok', out.get('FLIP_BCS_OK'), '0')   # BCS must mismatch on corruption

if fails:
    print('FAIL  ' + ' | '.join(fails))
    sys.exit(1)

print(f'PASS  acars_parity: clean frame OK (parity_errs=0, BCS=0x{t["bcs"]:04x} matched) | '
      f'single bit flip (char[{t["flip_idx"]}] bit{t["flip_bit"]}) DETECTED '
      f'(parity_errors={out["FLIP_PARITY_ERRORS"]}, BCS_OK=0)')
sys.exit(0)
