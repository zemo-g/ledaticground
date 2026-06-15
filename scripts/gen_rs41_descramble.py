#!/usr/bin/env python3
# RS41-3 generator: stage the inputs the Rail descrambler reads -- a 'BITS <...>' line
# (the demodulated channel bits, preamble + scrambled frame, LSB-first) and the frame
# length. Re-uses gen_rs41_gen.py to build the canonical frame, then emits the wire bits
# directly (so the descramble checker doesn't depend on the demod stage passing -- it
# isolates the descramble + sync-search + RS41 mask). SYNTHETIC-ONLY.
import numpy as np, sys, subprocess, os

HERE = os.path.dirname(os.path.abspath(__file__))
# (re)build the canonical frame + truth via the main generator
snr = sys.argv[sys.argv.index('--snr')+1] if '--snr' in sys.argv else '25'
subprocess.run(['/opt/homebrew/bin/python3.11', os.path.join(HERE, 'gen_rs41_gen.py'),
                '--snr', snr], check=True, stdout=subprocess.DEVNULL)

scrambled = np.load('/tmp/rs41_scrambled.npy').astype(int).tolist()
meta = {}
for l in open('/tmp/rs41_meta.txt'):
    if '=' in l:
        k, v = l.strip().split('=', 1); meta[k] = v
flen = int(meta['frame_len'])
pre = int(meta['preamble_bytes'])

# wire bytes = preamble (0x55) + scrambled frame
wire = [0x55]*pre + scrambled
bits = []
for b in wire:
    for i in range(8):
        bits.append((b >> i) & 1)        # LSB-first
open('/tmp/rs41_bits_in.txt', 'w').write('BITS ' + ''.join(str(x) for x in bits) + '\n')
open('/tmp/rs41_frame_len.txt', 'w').write(f'{flen}\n')
print(f'rs41 descramble vector: preamble={pre}B scrambled={len(scrambled)}B '
      f'frame_len={flen} bits={len(bits)} -> /tmp/rs41_bits_in.txt')
