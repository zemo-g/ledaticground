#!/usr/bin/env python3
# ledaticground Orbcomm front-end — cu8 -> 38.4k int8-IQ for src/orbcomm_char.rail (IS-4).
#
# THE HONEST DECODE PATH. src/orbcomm_char.rail is the proof-of-RECEPTION rung: it
# MEASURES physical observables from off-air IQ (carrier_present via in-burst SNR > 3 dB,
# carrier_offset_hz via the squared-symbol differential-product angle/4pi, sym_rate via
# transition-energy autocorrelation -> ~4800 sym/s confirm, burst timing via a
# 6x-noise-floor windowed power threshold, SNR) and emits an explicit
# `PAYLOAD=NONE semantics PROPRIETARY` line. It fabricates NOTHING above the link layer
# (Orbcomm user data is commercial/partly-encrypted, no public dictionary). It expects
# FS=38400 (src/orbcomm_char.rail line 204) with the burst oversampled (~8 samples/symbol
# at 4800 sym/s), and a leading inter-burst gap (it takes the noise floor from the first
# 200 samples — src/orbcomm_char.rail line 176).
#
# THE CONTRAST (do NOT confuse): src/orbcomm_decode.rail is the full
# SD-PSK -> UW -> descramble -> CRC chain, but its UW/scrambler taps are ILLUSTRATIVE
# synthetic stand-ins (the module says so) and it is gen-driven 1-sample/symbol synthetic
# ONLY — it CANNOT decode real Orbcomm payload (proprietary). So on REAL RF we run
# orbcomm_char (CHARACTERIZE), NOT orbcomm_decode (which would imply a content decode we
# cannot honestly make). carrier_present=0 is an HONEST outcome (the node admits it heard
# nothing), not a failure.
#
# THE FRONT-END: take a roof rtl_sdr cu8 capture centred on the Orbcomm channel (250k
# samp, the program-wide raw-IQ rate), digitally tune to the exact per-sat downlink,
# low-pass to the ~25 kHz channel, decimate to ~38.4k complex, quantize to int8
# interleaved -> /tmp/orbcomm_char_in.s8. Then `bash scripts/railrun.sh src/orbcomm_char.rail`
# measures the observables. This script does NOT touch the radio and does NOT ssh — it is
# the OFF-RADIO conversion stage and a DEPLOY ARTIFACT (importing/running it on a fixture
# must NOT start any capture).
#
#   iq_to_orbcomm_char.py <raw.cu8> [--center-hz H] [--chan-hz H] [--fs H]
#                         [--bw H] [--out PATH]
#
# --center-hz : the LO/tune centre the cu8 was captured at (default = --chan-hz).
# --chan-hz   : the Orbcomm per-sat downlink to extract (default 137662500, a
#               representative in-band channel; the exact per-sat value must be confirmed
#               before live capture — open_question in inband-signals.json).
# --fs        : cu8 capture sample rate (default 250000).
# --bw        : channel low-pass bandwidth (default 12500 -> half of one ~25 kHz channel).
#
# NOW vs LNA: a strong overhead Orbcomm pass may clear the 3 dB detection floor on the
# bare halo, but the ~8 dB no-LNA deficit + cross-pol means many passes read
# carrier_present=0 (the HONEST outcome). The LNA (IS-5) raises the hit rate; the
# characterization code + this front-end are complete and synthetic-validated now.
import sys, numpy as np
from scipy import signal

OUT_DEFAULT = "/tmp/orbcomm_char_in.s8"
FS_OUT = 38400          # src/orbcomm_char.rail FS (line 204) -> ~8 samples/symbol at 4800 sym/s
A = 80.0                # int8 quantization scale (matches gen_orbcomm_char.py amp)


def argf(flag, d):
    return float(sys.argv[sys.argv.index(flag) + 1]) if flag in sys.argv else d


def argi(flag, d):
    return int(sys.argv[sys.argv.index(flag) + 1]) if flag in sys.argv else d


def args(flag, d):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else d


def cu8_to_complex(path):
    """rtl_sdr cu8 (uint8 interleaved I/Q, bias 127.5) -> complex64 baseband."""
    raw = np.fromfile(path, dtype=np.uint8).astype(np.float32) - 127.5
    return (raw[0::2] + 1j * raw[1::2]).astype(np.complex64)


def channelize(iq, fs, foff, bw, fs_out):
    """Digitally tune by -foff Hz to bring the Orbcomm channel to DC, low-pass to the
    channel half-bandwidth, and resample to fs_out complex. foff = chan_hz - center_hz.
    NOTE: the carrier offset orbcomm_char measures is the RESIDUAL after this tune (LO
    error + Doppler), so we tune to the NOMINAL channel and let the rung measure the
    leftover offset — we deliberately do NOT zero it here."""
    n = np.arange(len(iq), dtype=np.float64)
    iq = iq * np.exp(-2j * np.pi * (foff / fs) * n).astype(np.complex64)
    nyq = fs / 2.0
    cutoff = min(bw, 0.95 * nyq)
    sos = signal.butter(6, cutoff / nyq, btype="low", output="sos")
    iq = signal.sosfiltfilt(sos, iq)
    n_out = int(round(len(iq) * fs_out / fs))
    if n_out < 1:
        return np.zeros(0, np.complex64)
    return signal.resample(iq, n_out).astype(np.complex64)


def quantize_s8(baseband):
    """complex baseband -> int8 interleaved IQ (the /tmp/orbcomm_char_in.s8 contract).
    NORMALIZE first: the cu8 input level depends on the dongle gain + the channelization
    gain, so we scale by a per-capture factor that maps the 99.5th-percentile sample
    magnitude to ~A counts (headroom against clipping). The decoder measures only
    RELATIVE power (envelope / SNR / autocorr), so a per-capture normalization is correct
    and makes the .s8 level gain-independent — the same burst decodes whether the capture
    was strong or weak."""
    if len(baseband) == 0:
        return np.empty(0, np.int8)
    mag = np.abs(baseband)
    p995 = np.percentile(mag, 99.5)
    scale = (A / p995) if p995 > 1e-9 else 0.0
    i8 = np.clip(np.round(baseband.real * scale), -127, 127).astype(np.int8)
    q8 = np.clip(np.round(baseband.imag * scale), -127, 127).astype(np.int8)
    out = np.empty(2 * len(baseband), np.int8)
    out[0::2] = i8
    out[1::2] = q8
    return out


def main():
    if len(sys.argv) < 2:
        sys.stderr.write(
            "usage: iq_to_orbcomm_char.py <raw.cu8> "
            "[--center-hz H] [--chan-hz H] [--fs H] [--bw H] [--out PATH]\n")
        sys.exit(2)
    inp = sys.argv[1]
    fs = argi("--fs", 250000)
    chan_hz = argi("--chan-hz", 137662500)
    center_hz = argi("--center-hz", chan_hz)
    bw = argf("--bw", 12500.0)
    out = args("--out", OUT_DEFAULT)

    iq = cu8_to_complex(inp)
    if len(iq) < fs // 100:                      # < ~10 ms: nothing usable
        sys.stderr.write(f"iq_to_orbcomm_char: capture too short ({len(iq)} samples)\n")
        quantize_s8(np.zeros(0, np.complex64)).tofile(out)
        print(f"ORBCOMM_FRONTEND in={inp} samples={len(iq)} -> {out} EMPTY (too short)")
        sys.exit(0)

    foff = chan_hz - center_hz
    bb = channelize(iq, fs, foff, bw, FS_OUT)
    s8 = quantize_s8(bb)
    s8.tofile(out)
    dur = len(iq) / fs
    print(f"ORBCOMM_FRONTEND in={inp} chan={chan_hz}Hz center={center_hz}Hz "
          f"foff={foff:+d}Hz fs={fs}->{FS_OUT} bw={bw:.0f}Hz "
          f"dur={dur:.1f}s out_samples={len(bb)} -> {out}")


if __name__ == "__main__":
    main()
