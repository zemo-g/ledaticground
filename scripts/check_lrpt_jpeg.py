#!/usr/bin/env python3
# Validate src/lrpt_jpeg.rail: the FULL Meteor JPEG-like decompress path. The Rail module
# entropy-decodes the bitstream (DC/AC Huffman + run-length), dequantizes, runs the 2D IDCT
# and level-shifts to grayscale pixels. We compare its reconstructed 8x8 pixel blocks against
# numpy's independent dequant+IDCT ground truth (computed in gen_lrpt_jpeg.py, never echoed
# from the Rail output). A correct entropy decode is a PRECONDITION for matching pixels:
# any Huffman/run-length error corrupts the coefficients and the pixels diverge wildly, so
# a pixel match end-to-end proves the entropy stage decoded correctly too.
import sys, json, numpy as np
gt = json.load(open('/tmp/lrpt_jpeg_truth.json'))
exp = gt['expected']
blocks = {}
for l in open(sys.argv[1]):
    if l.startswith('BLK'):
        k = int(l[3:l.index(' ')])
        pix = [int(x) for x in l[l.index('PIX ')+4:].split()]
        blocks[k] = pix
allok = True
maxerr_overall = 0
for k in range(gt['nb']):
    if k not in blocks:
        print(f"BLK{k} missing = FAIL"); allok=False; continue
    if len(blocks[k]) != 64:
        print(f"BLK{k} has {len(blocks[k])} pixels (expected 64) = FAIL"); allok=False; continue
    got = np.array(blocks[k]).reshape(8,8)
    e = np.array(exp[k])
    maxerr = int(np.max(np.abs(got - e)))
    maxerr_overall = max(maxerr_overall, maxerr)
    ok = maxerr <= 1   # float IDCT rounding can differ by 1 LSB on a few pixels
    print(f"BLK{k} max pixel abs err {maxerr} (tol 1) = {ok}")
    if not ok: allok=False
print(f"overall max pixel err {maxerr_overall} across {gt['nb']} entropy-decoded blocks")
print("PASS" if allok else "FAIL")
sys.exit(0 if allok else 1)
