#!/usr/bin/env python3
# RS41-5 generator: the top-level integration vector for the FULL RS41 decoder chain.
# Builds the canonical RS41-SG frame (via gen_rs41_gen.py) with real-ish GPS coords for a
# balloon over Michigan + planted serial/frame#/PTU, writes /tmp/rs41_in.s8 + truth/meta +
# /tmp/rs41_frame_len.txt. SYNTHETIC-ONLY (no live 400 MHz reception). The --snr flag tunes
# the channel so the RS correction stage is exercised at low SNR.
import sys, subprocess, os

HERE = os.path.dirname(os.path.abspath(__file__))
args = ['--snr', sys.argv[sys.argv.index('--snr')+1] if '--snr' in sys.argv else '25']
for f in ('--lat', '--lon', '--alt', '--serial', '--frame', '--tempcount'):
    if f in sys.argv:
        args += [f, sys.argv[sys.argv.index(f)+1]]
subprocess.run(['/opt/homebrew/bin/python3.11', os.path.join(HERE, 'gen_rs41_gen.py')] + args, check=True)

meta = {}
for l in open('/tmp/rs41_meta.txt'):
    if '=' in l:
        k, v = l.strip().split('=', 1); meta[k] = v
open('/tmp/rs41_frame_len.txt', 'w').write(f"{meta['frame_len']}\n")
print(f"rs41 decode integration vector staged: frame_len={meta['frame_len']} "
      f"serial={meta['serial']} -> /tmp/rs41_in.s8 + /tmp/rs41_frame_len.txt")
