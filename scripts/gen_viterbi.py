#!/usr/bin/env python3
# Reference CCSDS r=1/2 K=7 convolutional encoder (G1=0o171, G2=0o133) + BPSK soft
# channel, to validate src/viterbi.rail. Encodes known bits (terminated to state 0),
# maps 0->+1 / 1->-1 at amplitude 100, adds Gaussian noise at a chosen Eb/N0, writes
# int8 soft symbols to /tmp/vit_soft.s8 and the truth bits to /tmp/vit_truth.npy.
import numpy as np, sys
G1=0o171; G2=0o133
rng=np.random.default_rng(3)
N = int(sys.argv[sys.argv.index('--n')+1]) if '--n' in sys.argv else 2000
snr = float(sys.argv[sys.argv.index('--snr')+1]) if '--snr' in sys.argv else 4.0
bits = list(rng.integers(0,2,N)) + [0]*6           # 6 zero flush -> terminate to state 0
s=0; sym=[]
for b in bits:
    reg=((s<<1)|b)&127
    sym.append(bin(reg&G1).count('1')&1)
    sym.append(bin(reg&G2).count('1')&1)
    s=reg&63
sym=np.array(sym)
soft = np.where(sym==0, 100.0, -100.0)
sigma = 100.0/(10**(snr/20))
soft = soft + rng.standard_normal(len(soft))*sigma
np.clip(np.round(soft),-127,127).astype(np.int8).tofile('/tmp/vit_soft.s8')
np.save('/tmp/vit_truth.npy', np.array(bits,dtype=np.uint8))
print(f'encoded {len(bits)} bits -> {len(sym)} soft symbols, snr={snr}dB sigma={sigma:.1f}')
