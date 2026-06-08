#!/usr/bin/env python3
# Validate src/acars_deframe.rail against /tmp/acars_block_truth.json. Checks each parsed
# field, zero parity errors, and the block-check (BCS) match.
import sys, json
t = json.load(open('/tmp/acars_block_truth.json'))
out = {}
for l in open(sys.argv[1]):
    l = l.rstrip('\n')
    for key in ('MODE','REG','ACK','LABEL','BLKID','TEXT','PARITY_ERRORS','BCS_RX','BCS_CALC','BCS_OK'):
        if l.startswith(key+' '):
            out[key] = l[len(key)+1:]

fails = []
def chk(name, got, want):
    if got != want: fails.append(f'{name}: got {got!r} want {want!r}')

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
print(f'PASS  mode={out["MODE"]} reg={out["REG"]} label={out["LABEL"]} blkid={out["BLKID"]} '
      f'parity_errs=0 BCS=0x{t["bcs"]:04x} OK  text="{out["TEXT"]}"')
sys.exit(0)
