#!/usr/bin/env python3
# Build a complete synthetic AIS HDLC frame to validate src/ais_deframe.rail end-to-end:
#   168-bit payload -> +CRC-16/X-25 FCS -> bit-stuff -> [0x7E flag]..[0x7E flag] -> NRZI.
# Writes the NRZI bitstream (1 byte/bit) to /tmp/ais_frame.bits + the payload truth.
import numpy as np
B=[int(x) for x in np.fromfile("/tmp/ais_msg.bits",np.uint8)]   # 168-bit payload
# CRC-16/X-25 over the payload bits (MSB-first bytes); AIS computes FCS over the bits
def crc_x25(bits):
    crc=0xFFFF
    for b in bits:
        crc^= (b&1)
        crc = (crc>>1)^0x8408 if (crc&1) else (crc>>1)
    return crc ^ 0xFFFF
fcs=crc_x25(B)
fbits=[(fcs>>i)&1 for i in range(16)]          # FCS LSB-first (HDLC)
payload_fcs=B+fbits
# bit-stuff: after 5 consecutive 1s insert a 0
stuffed=[]; run=0
for b in payload_fcs:
    stuffed.append(b)
    run = run+1 if b==1 else 0
    if run==5: stuffed.append(0); run=0
flag=[0,1,1,1,1,1,1,0]
framed=flag+stuffed+flag
# NRZI encode: 0 -> transition, 1 -> no transition; start level 0
nrzi=[]; lvl=0
for b in framed:
    if b==0: lvl^=1
    nrzi.append(lvl)
# pad with some idle (1s = no transition) on each side
pre=[0]*8; post=[0]*8
np.array(pre+nrzi+post,np.uint8).tofile('/tmp/ais_frame.bits')
np.save('/tmp/ais_payload_truth.npy', np.array(B,np.uint8))
print(f'frame: payload 168b + FCS 0x{fcs:04x} -> stuffed {len(stuffed)}b -> framed {len(framed)}b -> NRZI {len(nrzi)}b (+pad)')
