#!/usr/bin/env python3
"""Self-labeling coverage-gap experiment (docs/RFML.md) — does the learned detector reach BELOW the
deterministic decode floor, on REAL signal?

The flywheel: the deterministic AIS decoder SELF-LABELS real bursts (a CRC-valid frame can't pass by
chance -> ground-truth 'a real AIS burst is here'). We take those real bursts and add progressively
more noise, then at each level measure decode-recall (decoder still gets CRC?) vs detect-recall
(characterizer still says 'msk'?) vs idle false-positive rate (detector must NOT cry msk on no-burst
windows, or the gain is meaningless).

DOMAIN NOTE (earned the hard way 2026-06-01): the audio characterizer was trained on rtl_fm wideband
FM-demod audio. Running it on a *channelized* IQ demod (narrow LPF -> smoother idle noise) gives 100%
false-positive — it calls band-limited idle 'msk'. So we run the sweep in the detector's NATIVE
domain: rtl_fm FM-demod audio (data/ais_clean_a.s16), where idle->noise correctly (FPR ~0). Caveat:
noise is added in the AUDIO domain (a model of weaker post-demod SNR, not strictly RF noise), so the
dB axis is relative; the RELATIVE gap (detect floor below decode floor) is the finding. And detection
!= decode: a detected-but-undecodable burst says 'a vessel is here', not its MMSI.

Usage: coverage_gap.py [data/ais_clean_a.s16]
"""
import sys, math, numpy as np
from array import array
import scripts.pi_ais_decode as D
import scripts.pi_characterize as C

CAP = sys.argv[1] if len(sys.argv) > 1 else "data/ais_clean_a.s16"
WIN = 4096
SNRS = [40, 25, 20, 17, 15, 13, 11, 9, 7, 5, 3, 1]   # added-audio-noise SNR (dB), high->low

mu, sg, W, b_ = C.load_model("models/audio_softmax.txt")
def decodes(win):
    si = [int(round(x)) for x in win]
    return len(D.frames_in_window(si, 0, len(si))) > 0
def detects(win):
    F, _ = C.feats([float(x) for x in win])
    ci, _ = C.classify(F, mu, sg, W, b_)
    return C.CLASSES[ci] == "msk"

def main():
    a = array("h"); a.frombytes(open(CAP, "rb").read())
    s = np.array(a, dtype=np.float64)
    nwin = len(s) // WIN
    wins = [s[w * WIN:(w + 1) * WIN] for w in range(nwin)]
    # self-label with the deterministic decoder (CRC-valid = real burst) + an idle control set
    bursts = [w for w in wins if decodes(w)]
    idle = [w for w in wins if not decodes(w) and w.std() < np.median([x.std() for x in wins])]
    if not bursts:
        print("no decoder-confirmed bursts in this capture"); return
    print(f"self-labeled {len(bursts)} decoder-confirmed real bursts + {len(idle)} idle windows from {CAP}")
    print(f"{'addSNR_dB':>9} {'decode-recall':>14} {'detect-recall':>14} {'idle-FPR':>10}")
    rng = np.random.default_rng(2026)
    sig_rms = float(np.median([np.sqrt(((w - w.mean()) ** 2).mean()) for w in bursts]))
    dec_floor = det_usable = None
    for snr in SNRS:
        nstd = sig_rms / (10 ** (snr / 20))
        def addn(w):
            return w + rng.standard_normal(len(w)) * nstd
        dec = sum(decodes(addn(w)) for w in bursts) / len(bursts)
        det = sum(detects(addn(w)) for w in bursts) / len(bursts)
        fpr = (sum(detects(addn(w)) for w in idle) / len(idle)) if idle else float("nan")
        print(f"{snr:>9} {dec*100:>13.0f}% {det*100:>13.0f}% {fpr*100:>9.0f}%")
        if dec_floor is None and dec < 0.5: dec_floor = snr
        # detector is only USEFUL where recall stays high AND it isn't crying msk on idle (FPR low)
        if det >= 0.5 and fpr <= 0.10: det_usable = snr
    print(f"\n[audio-noise model, {len(bursts)} bursts -> directional, not RF-calibrated dB]")
    if dec_floor is not None and det_usable is not None:
        print(f"decode 50%-floor ~{dec_floor} dB ; detector USABLE floor (recall>=50% AND idle-FPR<=10%) ~{det_usable} dB")
        print(f"=> the learned detector usefully detects bursts ~{dec_floor - det_usable} dB BELOW the "
              f"decode floor (presence below the MMSI-decode floor) before noise defeats it too.")

if __name__ == "__main__":
    main()
