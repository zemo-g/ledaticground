#!/usr/bin/env python3
# Synthetic ARINC 618 ACARS character block for src/acars_parse.rail (the FIELD-PARSE rung).
#
# This rung's job (distinct from acars_deframe, which does framing/parity/BCS): given a
# DEFRAMED ARINC 618 character block, parse the structured fields the ARINC 618 spec lays
# out. For a DOWNLINK message the text region (between STX and ETX) is itself structured:
#
#   pre-key(0x00) ... SYN SYN (0x16) ... SOH (0x01)
#   <mode 1>  <aircraft registration 7>  <ack 1>  <label 2>  <block-id 1>
#   STX (0x02)
#       <MSN 4>          message sequence number (e.g. M01A)
#       <flight-id 6>    e.g. "UA0123"
#       <free text ...>
#   ETX (0x03)  BCS_hi BCS_lo  DEL (0x7F)
#
# We emit ONE BYTE PER CHARACTER (the deframed stream, 7 data bits, NO parity here — the
# parser only needs the 7-bit data; it masks &127 anyway). This is the natural input for a
# field parser that runs AFTER deframe. Ground truth JSON lets the checker confirm every
# parsed field matches.
#
# Outputs:
#   /tmp/acars_parse.bytes        one byte per char (7-bit data), the parser input
#   /tmp/acars_parse_truth.json   ground-truth fields for check_acars_parse.py
import json

SOH, STX, ETX, SYN, DEL = 0x01, 0x02, 0x03, 0x16, 0x7F

# ---- ARINC 618 downlink header fields (7-bit ASCII) ----
mode   = '2'              # mode char (1)
reg    = '.N827NN'        # aircraft registration / address (7)
ack    = 0x15            # ACK char (1) -- NAK here (no tech ack); raw value
label  = 'H1'            # message label (2)
blkid  = '3'             # block id (1)

# ---- ARINC 618 downlink TEXT structure (between STX and ETX) ----
msn    = 'M01A'          # message sequence number (4)
fltid  = 'UA0123'        # flight id (6)
freetext = 'OPS NORMAL FL350 ETA 1423Z'

# assemble the 7-bit character stream
header = [ord(mode)] + [ord(c) for c in reg] + [ack] + [ord(c) for c in label] + [ord(blkid)]
text   = [ord(c) for c in msn] + [ord(c) for c in fltid] + [ord(c) for c in freetext]

prekey = [0x00] * 4
sync   = [SYN, SYN, SOH]
chars  = prekey + sync + header + [STX] + text + [ETX, 0x00, 0x00, DEL]

# write 1 byte per char (7-bit data, no parity — this is the post-deframe character stream)
with open('/tmp/acars_parse.bytes', 'wb') as f:
    f.write(bytes(c & 0x7F for c in chars))

truth = dict(
    mode=mode, reg=reg, ack=ack, label=label, blkid=blkid,
    msn=msn, fltid=fltid, text=freetext,
    n_chars=len(chars), prekey=len(prekey),
)
json.dump(truth, open('/tmp/acars_parse_truth.json', 'w'))
print(f'block: {len(chars)} chars  mode={mode} reg={reg} ack=0x{ack:02x} label={label} '
      f'blkid={blkid} msn={msn} fltid={fltid} text="{freetext}" -> /tmp/acars_parse.bytes')
