#!/usr/bin/env python3
# Validator for the ARINC 618 field-parse rung (src/acars_parse.rail) against
# /tmp/acars_parse_truth.json.
#
# Usage: check_acars_parse.py <rail_stdout_file>
#
# Asserts the parser pulls EVERY ARINC 618 field out of the deframed character block:
#   mode, aircraft registration, ack, label, block-id, MSN, flight-id, free text.
# A known message must parse to the right fields (the rung-spec test).
import sys, json

t = json.load(open('/tmp/acars_parse_truth.json'))

out = {}
info = {}
for line in open(sys.argv[1]):
    line = line.rstrip('\n')
    if line.startswith('INFO '):
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
    for key in ('MODE', 'REG', 'ACK', 'LABEL', 'BLKID', 'MSN', 'FLTID', 'TEXT'):
        if line.startswith(key + ' '):
            out[key] = line[len(key) + 1:]

fails = []
def chk(name, got, want):
    if got != want:
        fails.append(f'{name}: got {got!r} want {want!r}')

chk('chars',   info.get('chars'), t['n_chars'])
chk('MODE',  out.get('MODE'),  t['mode'])
chk('REG',   out.get('REG'),   t['reg'])
chk('ACK',   out.get('ACK'),   str(t['ack']))
chk('LABEL', out.get('LABEL'), t['label'])
chk('BLKID', out.get('BLKID'), t['blkid'])
chk('MSN',   out.get('MSN'),   t['msn'])
chk('FLTID', out.get('FLTID'), t['fltid'])
chk('TEXT',  out.get('TEXT'),  t['text'])

if fails:
    print('FAIL  ' + ' | '.join(fails))
    sys.exit(1)

print(f'PASS  field parse ok: mode={out["MODE"]} reg={out["REG"]} ack={out["ACK"]} '
      f'label={out["LABEL"]} blkid={out["BLKID"]} msn={out["MSN"]} fltid={out["FLTID"]} '
      f'text="{out["TEXT"]}"')
sys.exit(0)
