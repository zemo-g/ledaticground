#!/usr/bin/env python3.11
# Attested-spectrum summariser (extract-more #2). Reads a cu8 WIDEBAND IQ capture (raw_iq/*.bin --
# the real 137-band RF-environment snapshots, NOT the demodulated AIS audio) and computes a
# DETERMINISTIC, self-calibrating spectral summary + a fixed-grid binned spectrum.
#
# The SPECTRUM_RECEIPT commits to: product_sha256 (this summary), spectrum_sha256 (the binned
# spectrum -- the "meet in the middle" full-fidelity commitment), input_sha256 (the raw .bin).
#
# Self-calibrating: cu8 has NO absolute calibration, so every metric is relative to the capture's
# OWN noise floor -- SDR gain + band-edge rolloff cancel out. NOTHING fabricated; honest measured
# values only (this is a physical observation of the RF environment, attested as a FACT).
# Mirrors the proven math in ~/.ledatic/roofv2/wb_proto.py (NFFT=4096, hanning, fftshift, floor=median).
#
# Usage: gen_spectrum_summary.py <capture.bin> [fs_hz]    (fs default 250000; pass the satdump rate)
import sys, json
import numpy as np

NFFT = 4096
NBINS_OUT = 256          # fixed grid for the binned-spectrum commitment (spectrum_sha256)
OCC_THRESH_DB = 6.0      # a bin is "occupied" if its time-avg power is >6 dB over the floor

def main():
    if len(sys.argv) < 2:
        sys.exit("usage: gen_spectrum_summary.py <capture.bin> [fs_hz]")
    inp = sys.argv[1]
    fs = int(sys.argv[2]) if len(sys.argv) > 2 else 250000

    raw = np.fromfile(inp, dtype=np.uint8).astype(np.float32) - 127.5
    iq = raw[0::2] + 1j * raw[1::2]
    if len(iq) < NFFT:
        sys.exit("gen_spectrum_summary: capture too short (%d < %d samples)" % (len(iq), NFFT))

    ncols = min(1400, max(50, len(iq) // NFFT))
    hop = max(NFFT, (len(iq) - NFFT) // ncols)
    win = np.hanning(NFFT)
    acc = np.zeros(NFFT)
    nc = 0
    for s in range(0, len(iq) - NFFT, hop):
        seg = iq[s:s + NFFT] * win
        acc += np.abs(np.fft.fftshift(np.fft.fft(seg))) ** 2
        nc += 1
    psd = acc / nc                                   # time-averaged power spectrum (fftshifted)
    f = np.fft.fftshift(np.fft.fftfreq(NFFT, 1.0 / fs))

    dc = np.abs(f) < 3000                            # null the rtl DC spike before measuring
    floor = float(np.median(psd[~dc]))
    psd_db = 10.0 * np.log10(psd / (floor + 1e-12) + 1e-12)   # dB over the capture's OWN floor

    nd = psd_db[~dc]
    occ_pct = round(100.0 * float((nd > OCC_THRESH_DB).sum()) / nd.size, 1)
    peak_excess_db = round(float(nd.max()), 1)
    dyn_range_db = round(float(nd.max() - np.percentile(nd, 5)), 1)
    pk = int(np.argmax(np.where(dc, -1e9, psd_db)))
    peak_off_hz = int(round(float(f[pk])))

    edges = np.linspace(0, NFFT, NBINS_OUT + 1).astype(int)   # fixed-grid binned spectrum (dB/floor)
    binned = [round(float(psd_db[edges[i]:edges[i + 1]].mean()), 2) for i in range(NBINS_OUT)]

    summary = {
        "kind": "spectrum_summary", "src": inp.split("/")[-1],
        "fs_hz": fs, "nfft": NFFT, "n_cols": nc, "nbins": NBINS_OUT,
        "occ_thresh_db": OCC_THRESH_DB, "occ_pct": occ_pct,
        "peak_excess_db": peak_excess_db, "dyn_range_db": dyn_range_db, "peak_off_hz": peak_off_hz,
    }
    summ_path = "/tmp/spectrum_summary.json"
    bins_path = "/tmp/spectrum_binned.txt"
    # DETERMINISTIC writes (sorted keys / fixed format) so product_sha256 + spectrum_sha256 reproduce.
    open(summ_path, "w").write(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    open(bins_path, "w").write("\n".join("%.2f" % v for v in binned) + "\n")

    # headline scalars for the rollup to stage into the receipt (integers on the wire).
    print("OCC_PCT=%s" % occ_pct)
    print("PEAK_EXCESS_DB=%d" % round(peak_excess_db))
    print("DYN_RANGE_DB=%d" % round(dyn_range_db))
    print("N_COLS=%d" % nc)
    print("FS_HZ=%d" % fs)
    print("NFFT=%d" % NFFT)
    print("PEAK_OFF_HZ=%d" % peak_off_hz)
    print("SUMMARY_PATH=%s" % summ_path)
    print("BINS_PATH=%s" % bins_path)

if __name__ == "__main__":
    main()
