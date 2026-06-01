#!/usr/bin/env python3
"""Synthetic SAME (NOAA Weather Radio alert) burst generator — the falsify-each-rung test
fixture for same_decode.py. Builds the AFSK header (520.83 baud, mark 2083.3 / space 1562.5 Hz,
NRZ, LSB-first bytes), 16-byte 0xAB preamble + ZCZC header, sent 3x like real SAME.
Usage: gen_same.py [--snr 30] [--corrupt] [--out /tmp/same_test.s16]
"""
import numpy as np, argparse, random

FS = 48000.0; BAUD = 520.833; SPB = FS / BAUD
MARK = 4 * BAUD          # 2083.33 Hz
SPACE = 3 * BAUD         # 1562.5 Hz
MSG = "ZCZC-WXR-RWT-026163-026099+0100-1532100-KDTX/NWS-"   # NWS Detroit Required Weekly Test, Wayne+Macomb MI

def header_bits():
    bits = []
    for byte in [0xAB] * 16 + [ord(c) for c in MSG]:
        for i in range(8):
            bits.append((byte >> i) & 1)        # LSB-first
    return bits

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snr", type=float, default=30)
    ap.add_argument("--corrupt", action="store_true", help="flip bits in each repeat (distinct positions) to test 2-of-3 voting")
    ap.add_argument("--out", default="/tmp/same_test.s16")
    a = ap.parse_args()
    random.seed(42)
    reps = [header_bits() for _ in range(3)]
    if a.corrupt:
        L = len(reps[0]); pre = 16 * 8
        for r, rep in enumerate(reps):                 # 6 errors/rep, offset per rep so no byte fails 2-of-3
            for k in range(6):
                p = pre + (r * 9 + k * 37) % (L - pre)
                rep[p] ^= 1
    allbits = [b for rep in reps for b in rep]
    n = int(len(allbits) * SPB) + 16
    ph = 0.0; sig = np.zeros(n)
    for s in range(n):
        bi = int(s / SPB)
        if bi >= len(allbits):
            break
        ph += 2 * np.pi * (MARK if allbits[bi] else SPACE) / FS
        sig[s] = np.sin(ph)
    amp = 8000.0
    noise = np.random.RandomState(7).normal(0, amp / (10 ** (a.snr / 20)), n)
    np.clip(sig * amp + noise, -32767, 32767).astype("<i2").tofile(a.out)
    print(f"wrote {a.out}: {n} samples, 3 reps @ {BAUD:.2f} baud, msg='{MSG}'"
          f"{' (corrupted — voting test)' if a.corrupt else ''}")

main()
