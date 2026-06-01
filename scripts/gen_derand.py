#!/usr/bin/env python3
# Reference for src/derand.rail. Generates the canonical CCSDS pseudo-randomizer byte
# sequence (h(x)=x^8+x^7+x^5+x^3+1, continuous LFSR, init 0xFF -> FF 48 0E C0 9A ...),
# writes a random test payload, and the expected derandomized output (payload XOR PN).
import numpy as np, sys
def ccsds_pn(n):
    state=0xFF; seq=[]
    for _ in range(n):
        byte=0
        for _ in range(8):
            out=(state>>7)&1
            byte=(byte<<1)|out
            fb=((state&1)^((state>>2)&1)^((state>>4)&1)^((state>>7)&1))&1
            state=((state<<1)|fb)&0xFF
        seq.append(byte)
    return seq
pn=ccsds_pn(255)
rng=np.random.default_rng(9)
N=64
payload=rng.integers(0,256,N).astype(np.uint8)
payload.tofile('/tmp/derand_in.s8')
expected=np.array([payload[i]^pn[i%255] for i in range(N)],np.uint8)
np.save('/tmp/derand_expected.npy', expected)
np.save('/tmp/derand_pn.npy', np.array(pn,np.uint8))
print('PN first8:', ' '.join(f'{x:02x}' for x in pn[:8]))
