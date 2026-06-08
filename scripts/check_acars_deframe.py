#!/usr/bin/env python3
# Validator for the acars_deframe rung (src/acars_deframe.rail) against
# /tmp/acars_deframe_truth.json.
#
# Usage: check_acars_deframe.py <rail_stdout_file>
#
# Clean vector  : asserts the deframe STRUCTURE (syn/soh/stx/etx char offsets) and that the
#                 frame deframes to the expected CHAR BLOCK (mode/reg/ack/label/blkid/text),
#                 with PARITY_ERRORS=0 and BCS_OK=1.
# --corrupt set : asserts the deframer still locates the structure but FLAGS the damage
#                 (PARITY_ERRORS>=1 AND BCS_OK=0) — proves the rung is not vacuously passing.
import sys, json

t = json.load(open('/tmp/acars_deframe_truth.json'))
corrupt = bool(t.get('corrupt'))

out = {}
info = {}
for line in open(sys.argv[1]):
    line = line.rstrip('\n')
    if line.startswith('INFO '):
        # INFO chars=53 syn@7 soh@9 stx@22 etx@49
        for tok in line[5:].split():
            if '@' in tok:
                name, val = tok.split('@', 1)
                try:
                    info[name] = int(val)
                except ValueError:
                    pass
            elif '=' in tok:
                name, val = tok.split('=', 1)
                try:
                    info[name] = int(val)
                except ValueError:
                    info[name] = val
        continue
    for key in ('MODE', 'REG', 'ACK', 'LABEL', 'BLKID', 'TEXT',
                'PARITY_ERRORS', 'BCS_RX', 'BCS_CALC', 'BCS_OK'):
        if line.startswith(key + ' '):
            out[key] = line[len(key) + 1:]

fails = []
def chk(name, got, want):
    if got != want:
        fails.append(f'{name}: got {got!r} want {want!r}')

# --- deframe STRUCTURE: the rung must locate pre-key/sync/framing correctly ---
chk('syn@', info.get('syn'), t['syn_off'])
chk('soh@', info.get('soh'), t['soh_off'])
chk('stx@', info.get('stx'), t['stx_off'])
chk('etx@', info.get('etx'), t['etx_off'])
chk('chars', info.get('chars'), t['n_chars'])

if not corrupt:
    # --- clean: frame deframes to the exact char block ---
    chk('MODE',  out.get('MODE'),  t['mode'])
    chk('REG',   out.get('REG'),   t['reg'])
    chk('ACK',   out.get('ACK'),   str(t['ack']))
    chk('LABEL', out.get('LABEL'), t['label'])
    chk('BLKID', out.get('BLKID'), t['blkid'])
    chk('TEXT',  out.get('TEXT'),  t['text'])
    chk('PARITY_ERRORS', out.get('PARITY_ERRORS'), '0')
    chk('BCS_RX',   out.get('BCS_RX'),   str(t['bcs']))
    chk('BCS_CALC', out.get('BCS_CALC'), str(t['bcs']))
    chk('BCS_OK',   out.get('BCS_OK'),   '1')
    if fails:
        print('FAIL  ' + ' | '.join(fails)); sys.exit(1)
    print(f'PASS  deframe ok: syn@{info["syn"]} soh@{info["soh"]} stx@{info["stx"]} '
          f'etx@{info["etx"]}; mode={out["MODE"]} reg={out["REG"]} label={out["LABEL"]} '
          f'blkid={out["BLKID"]} parity_errs=0 BCS=0x{t["bcs"]:04x} OK text="{out["TEXT"]}"')
    sys.exit(0)
else:
    # --- corrupt: structure still found, but parity + BCS must catch the flipped bit ---
    try:
        perr = int(out.get('PARITY_ERRORS', '0'))
    except ValueError:
        perr = 0
    if perr < 1:
        fails.append(f'PARITY_ERRORS: got {out.get("PARITY_ERRORS")!r} want >=1 (corruption undetected)')
    chk('BCS_OK', out.get('BCS_OK'), '0')
    if fails:
        print('FAIL  ' + ' | '.join(fails)); sys.exit(1)
    print(f'PASS  negative test: corruption detected — PARITY_ERRORS={out.get("PARITY_ERRORS")} '
          f'BCS_OK=0 (structure still located: syn@{info["syn"]} soh@{info["soh"]} '
          f'stx@{info["stx"]} etx@{info["etx"]})')
    sys.exit(0)
