#!/usr/bin/env python3
# ledaticground ACARS front-end — Pi-side cu8 -> 48k int8-IQ channelizer.
#
# THE FRONT-END BRIDGE (IS-1). The committed src/acars_decode.rail is the full,
# synthetic-validated ACARS receive chain (AM envelope -> MSK 1200/2400 Hz ->
# deframe SYN SYN/SOH/STX/ETX -> odd-parity -> CRC-CCITT BCS poly 0x8408 ->
# fixed-width fields). It reads int8 INTERLEAVED IQ from /tmp/acars_in.s8 and does
# its OWN envelope detect (a[n]=sqrt(I^2+Q^2)-mean) at SPS=20, FS=48000
# (src/acars_decode.rail lines 90-91). It currently only ever sees synthetic
# gen_acars_gen.py output; this script is the path that lets it see REAL RF.
#
# THE GAP IT CLOSES: the live AIS loop (scripts/ais_monitor.sh) uses `rtl_fm -M fm`,
# which is WRONG for ACARS — ACARS is AM, not FM. The clean real-RF path is RAW IQ:
#   rtl_sdr -f <ch> -s <fs> -g <g> -> uint8 interleaved (cu8) -> THIS SCRIPT ->
#   digital tune to the exact ACARS channel, low-pass the ~12.5 kHz channel,
#   decimate to 48 kHz complex, quantize to int8 interleaved -> /tmp/acars_in.s8 ->
#   run src/acars_decode.rail UNMODIFIED.
# Only the small .s8 (or the decode JSON) crosses WiFi (decode-on-Pi pattern, same
# as pi_ais_decode.py). This script does NOT touch the radio and does NOT ssh.
#
# ACARS channels (the 4 most-active North-American VHF channels, all in-band on the
# 136-138 MHz halo passband — see docs/CHANNEL_INTELLIGENCE.md):
#   PRIMARY 136.975 (the band's busiest), plus 136.700, 136.800, 136.850.
#
# HONEST SCOPE NOW vs LNA: ACARS is terrestrial VERTICALLY polarized; the horizontal
# halo costs 15-20 dB cross-pol loss + the ~8 dB no-LNA deficit, so live copy of
# distant aircraft will be rare/marginal NOW. A single NEARBY aircraft burst that
# clears the CRC is still a real attested FACT. Reliable continuous copy needs the
# LNA (IS-5 dependency) — that is a sensitivity gate on the antenna, not on this code.
#
#   pi_acars_capture.py <raw.cu8> [--center-hz <Hz>] [--chan-hz <ACARS Hz>]
#                       [--fs <capture Hz>] [--bw <channel Hz>] [--out PATH]
#
# --center-hz : the LO/tune centre the cu8 was captured at (default = --chan-hz,
#               i.e. the dongle was tuned directly on the ACARS channel -> 0 offset).
# --chan-hz   : the ACARS channel to extract (default 136975000).
# --fs        : cu8 capture sample rate (default 250000 — the program-wide raw-IQ rate).
# --bw        : channel low-pass bandwidth (default 12500 — one ACARS 25 kHz channel,
#               half-bandwidth lowpass; ACARS occupies ~ +/-5 kHz around the carrier).
#
# Pure-numpy/scipy (Pi has scipy in the autocap venv); NO rtl_sdr call here — this is
# the OFF-RADIO conversion stage. It is a DEPLOY ARTIFACT: importing or running it on
# a fixture must NOT start any capture.
import sys, numpy as np
from scipy import signal

OUT_DEFAULT = "/tmp/acars_in.s8"
FS_OUT = 48000          # src/acars_decode.rail FS (line 91)
SPS = 20                # src/acars_decode.rail SPS (line 90); FS_OUT/SPS = 2400 baud
PREKEY = 2 * SPS        # the decoder drops 2*SPS unmodulated lead samples (line 257)
A = 70.0                # int8 quantization scale (matches gen_acars_gen.py amp)

# canonical in-band ACARS VHF channels (Hz) — primary first
ACARS_CHANNELS = [136975000, 136700000, 136800000, 136850000]


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
    """Digitally tune by -foff Hz to bring the channel to DC, low-pass to bw, and
    resample to fs_out complex. foff = chan_hz - center_hz (the channel's offset
    from the capture centre). Returns a complex baseband stream at fs_out."""
    n = np.arange(len(iq), dtype=np.float64)
    # complex mix down: shift the channel at +foff to 0 Hz
    iq = iq * np.exp(-2j * np.pi * (foff / fs) * n).astype(np.complex64)
    # low-pass to the channel half-bandwidth (anti-alias before resample)
    nyq = fs / 2.0
    cutoff = min(bw, 0.95 * nyq)
    sos = signal.butter(6, cutoff / nyq, btype="low", output="sos")
    iq = signal.sosfiltfilt(sos, iq)
    # resample complex to fs_out (resample I and Q together via the complex array)
    n_out = int(round(len(iq) * fs_out / fs))
    if n_out < 1:
        return np.zeros(0, np.complex64)
    return signal.resample(iq, n_out).astype(np.complex64)


def quantize_s8(baseband):
    """complex baseband -> int8 interleaved IQ (the /tmp/acars_in.s8 contract).
    NORMALIZE first: the cu8 input level depends on the dongle gain + the channelization
    gain. The decoder envelope-detects sqrt(I^2+Q^2) and removes the DC mean, so only the
    RELATIVE modulation matters; we scale by a per-capture factor mapping the
    99.5th-percentile sample magnitude to ~A counts (headroom against clipping) so the .s8
    level is gain-independent — the same burst decodes whether the capture was strong or
    weak. (Clipping/saturation would destroy the AM envelope, so headroom matters.)"""
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
            "usage: pi_acars_capture.py <raw.cu8> "
            "[--center-hz H] [--chan-hz H] [--fs H] [--bw H] [--out PATH]\n")
        sys.exit(2)
    inp = sys.argv[1]
    fs = argi("--fs", 250000)
    chan_hz = argi("--chan-hz", ACARS_CHANNELS[0])
    center_hz = argi("--center-hz", chan_hz)   # default: tuned on the channel -> 0 offset
    bw = argf("--bw", 12500.0)
    out = args("--out", OUT_DEFAULT)

    iq = cu8_to_complex(inp)
    if len(iq) < fs // 100:                     # < ~10 ms: nothing usable
        sys.stderr.write(f"pi_acars_capture: capture too short ({len(iq)} samples)\n")
        # still write an (empty) output so the decoder honestly reports "no input"
        quantize_s8(np.zeros(0, np.complex64)).tofile(out)
        print(f"ACARS_FRONTEND in={inp} samples={len(iq)} -> {out} EMPTY (too short)")
        sys.exit(0)

    foff = chan_hz - center_hz                  # channel offset from capture centre
    bb = channelize(iq, fs, foff, bw, FS_OUT)
    s8 = quantize_s8(bb)
    s8.tofile(out)
    dur = len(iq) / fs
    print(f"ACARS_FRONTEND in={inp} chan={chan_hz}Hz center={center_hz}Hz "
          f"foff={foff:+d}Hz fs={fs}->{FS_OUT} bw={bw:.0f}Hz "
          f"dur={dur:.1f}s out_samples={len(bb)} -> {out}")


if __name__ == "__main__":
    main()
