#!/usr/bin/env python3
# ===========================================================================
# gen.py for the END-TO-END LRPT decoder rung (src/lrpt_decode.rail).
#
# It reuses the existing full-chain transmit generator (gen_lrpt_chain_gen.py)
# verbatim to emit /tmp/lrpt_chain.s8 (int8 baseband IQ) + the ground-truth
# JSON /tmp/lrpt_chain_truth.json, then derives a tiny SELF-CHECK reference
# that the pure-Rail decoder can read WITHOUT a JSON parser:
#
#   /tmp/lrpt_decode_expect.s8   — NB*64 bytes, the expected reconstructed
#                                  image pixels (dequant+IDCT+level-shift+clamp
#                                  of the ground-truth zig-zag coeffs, i.e. the
#                                  exact bytes dct.rail must reproduce; this is
#                                  the FINAL deliverable of the whole chain).
#   /tmp/lrpt_decode_meta.s8     — small header the Rail reads: [nb] (1 byte).
#
# The expected pixels are computed by an INDEPENDENT numpy idct2 reference (the
# same oracle as check_lrpt_chain_gen.py), NOT echoed from any rail output, so
# the Rail decoder's in-process SELFCHECK is a true cross-check.
#
# HONESTY: synthetic vector (numpy-generated), no real Meteor pass, no
# attestation. Conventional (Berlekamp) RS field, not the CCSDS dual-basis;
# DCT carries raw int16 coeffs (no entropy stage). All labeled in the rungs.
# ===========================================================================
import sys, json, subprocess, os
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))

def idct2(C):
    out = np.zeros((8, 8))
    for x in range(8):
        for y in range(8):
            s = 0.0
            for u in range(8):
                cu = np.sqrt(1/8) if u == 0 else np.sqrt(2/8)
                for v in range(8):
                    cv = np.sqrt(1/8) if v == 0 else np.sqrt(2/8)
                    s += cu*cv*C[u, v]*np.cos((2*x+1)*u*np.pi/16)*np.cos((2*y+1)*v*np.pi/16)
            out[x, y] = s
    return out

def main():
    # forward args (e.g. --nb N) straight through to the chain generator
    nb = 3
    if '--nb' in sys.argv:
        nb = int(sys.argv[sys.argv.index('--nb')+1])
    args = ['--nb', str(nb)]
    # run the existing full transmit-chain generator (writes IQ + truth JSON)
    r = subprocess.run(['/opt/homebrew/bin/python3.11',
                        os.path.join(HERE, 'gen_lrpt_chain_gen.py')] + args,
                       capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    if r.returncode != 0:
        sys.stderr.write(r.stderr)
        raise SystemExit("chain generator failed")

    gt = json.load(open('/tmp/lrpt_chain_truth.json'))
    ZZ = gt['zz']
    QTBL = np.array(gt['qtbl'], dtype=np.float64)
    NB = gt['params']['nb']

    # expected reconstructed pixels = dequant+IDCT of the TRUTH coeffs (this is
    # exactly what dct.rail produces at the end of the chain — the deliverable).
    expect = []
    for k in range(NB):
        cz = gt['coeff_blocks_zz'][k]
        D = np.zeros(64)
        for j in range(64):
            D[ZZ[j]] = cz[j] * QTBL[ZZ[j]]
        rec = np.clip(np.round(idct2(D.reshape(8, 8)) + 128.0), 0, 255).astype(int).flatten()
        expect.append(rec.tolist())

    flat = np.array([p for blk in expect for p in blk], dtype=np.uint8)
    flat.tofile('/tmp/lrpt_decode_expect.s8')
    np.array([NB], dtype=np.uint8).tofile('/tmp/lrpt_decode_meta.s8')

    print(f"LRPT decode self-check vector: NB={NB} expected-pixel bytes={flat.size} "
          f"-> /tmp/lrpt_decode_expect.s8  (meta NB -> /tmp/lrpt_decode_meta.s8)")

if __name__ == "__main__":
    main()
