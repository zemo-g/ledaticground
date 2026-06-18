#!/usr/bin/env python3
"""measure_doppler_real_iq.py -- REAL off-air Doppler measurement + SGP4 physics-binding check.

THE POINT. The physics-binding waves (B/C) were validated only on SYNTHETIC IQ
(gen_tle_doppler.py paints an SGP4-true curve, doppler_real.rail measures it, the binding
reproduces a ~few-Hz residual). data/doppler_residual.txt stood at PENDING_needs_IQ -- the
mechanism had never bound a REAL off-air emission. This tool closes that: it measures the
Doppler track from a real wideband cu8 capture and binds it to the satellite's SGP4 prediction
over the (approximate) station location.

THE DSP (why a naive estimator fails). METEOR LRPT is a wide ~120 kHz suppressed-carrier
(O)QPSK envelope; on a no-LNA rooftop the in-band SNR is ~5 dB. A raw spectral centroid wanders
in the noise and a 4th-power carrier-recovery finds no clean line. What works: a noise-floor-
GATED, power-weighted centroid over the in-band bins, restricted to the strong-signal windows
around TCA, lightly smoothed. That recovers a clean monotone Doppler S-curve.

THE BINDING. SGP4 (skyfield) predicts the satellite's topocentric range-rate -> Doppler over the
capture window; we fit the receiver LO offset (a constant; RTL-SDR ~20 ppm = ~2.7 kHz at 137 MHz)
and a small timing shift, then report the residual RMS over the high-SNR windows. A small residual
means the measured curve is consistent with THAT orbit passing over THIS location at THIS time --
a real off-air emission bound to a physically-possible world.

HONESTY (load-bearing -- this is provenance, never proof):
  * geo is APPROXIMATE (the node's GPS fix is PENDING_needs_GPS_PPS); the bind is "consistent with
    a pass over ~this region", and a ~10 km location error barely moves the Doppler shape.
  * precision is SNR-LIMITED (~200+ Hz residual vs the synthetic fixture's few Hz) -- an LNA would
    tighten it. We label the coarse residual honestly, never claim the synthetic precision.
  * a resourced over-the-air spoofer could transmit a physics-consistent fake Doppler; single-node
    sensor-binding raises the cost of an undetected lie, it does not prove truth.

Usage:
  measure_doppler_real_iq.py <capture.bin> --fs 250000 --fc 137.9e6 \
      --t0 2026-06-16T08:45:00Z --lat 42.0 --lon -83.1 --alt-m 190 \
      --tle-l1 "1 59051U ..." --tle-l2 "2 59051 ..." [--win-s 3.0] [--snr-db 5.5]
Emits a JSON summary to stdout and, with --out PATH, the measured track (t_rel_s,doppler_hz)
for downstream binding (the meas_sha256 the PHYSICS_BINDING_RECEIPT commits).
"""
import sys, json, argparse
from datetime import datetime, timezone
import numpy as np


def measure_track(path, fs, win_s, sigband_hz=70000.0, noiseband_hz=90000.0):
    """Noise-gated power-weighted in-band centroid per window -> [(t_rel_s, doppler_hz, snr_db, energy)]."""
    raw = np.memmap(path, dtype=np.uint8, mode="r")
    n = len(raw) // 2
    iq = raw[:2 * n].astype(np.float32).reshape(-1, 2)
    x = (iq[:, 0] - 127.5) + 1j * (iq[:, 1] - 127.5)
    W = int(win_s * fs)
    nb = 4096
    fr = np.fft.fftshift(np.fft.fftfreq(nb, 1 / fs))
    sb = np.abs(fr) < sigband_hz
    nbnd = np.abs(fr) > noiseband_hz
    track = []
    for k in range(0, n - W, W):
        seg = x[k:k + W]
        P = np.zeros(nb)
        for j in range(0, W - nb, nb):
            P += np.abs(np.fft.fftshift(np.fft.fft(seg[j:j + nb]))) ** 2
        nf = np.median(P[nbnd])
        g = np.where(P * sb > 3 * nf, P * sb, 0.0)
        snr = 10 * np.log10(P[sb].sum() / (P[nbnd].sum() * sb.sum() / nbnd.sum() + 1e-9))
        if g.sum() > 0:
            track.append((k / fs + win_s / 2.0, float((fr * g).sum() / g.sum()), float(snr), float(g.sum())))
    return np.array(track), n / fs


def predict_doppler(tle_l1, tle_l2, lat, lon, alt_m, fc, t0, t_rel):
    from skyfield.api import load, wgs84, EarthSatellite
    ts = load.timescale()
    sat = EarthSatellite(tle_l1, tle_l2, "sat", ts)
    obs = wgs84.latlon(lat, lon, elevation_m=alt_m)
    c = 299792458.0
    out = []
    for t in t_rel:
        tt = ts.utc(t0.year, t0.month, t0.day, t0.hour, t0.minute, t0.second + float(t))
        g = (sat - obs).at(tt)
        r = g.position.km
        v = g.velocity.km_per_s
        rr = float(np.dot(r, v) / np.linalg.norm(r))   # km/s, + = receding
        out.append(-fc * (rr * 1000.0) / c)             # Hz
    return np.array(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("capture")
    ap.add_argument("--fs", type=float, default=250000.0)
    ap.add_argument("--fc", type=float, default=137.9e6)
    ap.add_argument("--t0", default=None, help="capture start, ISO-8601 Z (required unless --track-only)")
    ap.add_argument("--lat", type=float, default=None)
    ap.add_argument("--lon", type=float, default=None)
    ap.add_argument("--alt-m", type=float, default=190.0)
    ap.add_argument("--tle-l1", default=None)
    ap.add_argument("--tle-l2", default=None)
    ap.add_argument("--win-s", type=float, default=3.0)
    ap.add_argument("--snr-db", type=float, default=5.5)
    ap.add_argument("--out", default=None, help="write the measured track (t_rel_s doppler_hz) here")
    ap.add_argument("--track-only", action="store_true",
                    help="emit ONLY the measured Doppler track (no SGP4/skyfield bind) -- the input the "
                         "verifiable doppler_range.rail receipt path consumes; needs no TLE/geo/t0/skyfield")
    a = ap.parse_args()

    track, dur = measure_track(a.capture, a.fs, a.win_s)
    if len(track) == 0:
        print(json.dumps({"ok": False, "reason": "no in-band signal windows"})); return 1
    hi = track[track[:, 2] > a.snr_db]
    if len(hi) < 5:
        print(json.dumps({"ok": False, "reason": "too few high-SNR windows", "hi": int(len(hi))})); return 1

    # --track-only: just the measured DSP product (t_rel doppler), no orbit prediction. This is what
    # the attested binding pipeline (doppler_range.rail + binding_attest.rail, verify.rail-reproducible)
    # consumes -- it does its OWN Keplerian prediction so the receipt is cold-verifiable. The full
    # mode below (skyfield SGP4) is the accurate standalone PROOF that the curve binds.
    if a.track_only or not (a.t0 and a.lat is not None and a.lon is not None and a.tle_l1 and a.tle_l2):
        if a.out:
            with open(a.out, "w") as f:
                for t, d, s, _e in hi:
                    f.write("%.3f %.1f\n" % (t, d))
        print(json.dumps({"ok": True, "track_only": True, "capture": a.capture,
                          "duration_s": round(dur, 1), "windows": int(len(track)),
                          "high_snr_windows": int(len(hi)),
                          "measured_span_hz": round(float(hi[:, 1].max() - hi[:, 1].min()), 0)}, indent=2))
        return 0

    t0 = datetime.fromisoformat(a.t0.replace("Z", "+00:00")).astimezone(timezone.utc)
    # fit a small timing shift + constant LO offset, minimise residual RMS over high-SNR windows
    best = None
    for tsh in np.arange(-90, 91, 2.0):
        pr = predict_doppler(a.tle_l1, a.tle_l2, a.lat, a.lon, a.alt_m, a.fc, t0, hi[:, 0] + tsh)
        off = float(np.mean(hi[:, 1] - pr))
        rms = float(np.sqrt(np.mean((hi[:, 1] - pr - off) ** 2)))
        if best is None or rms < best["residual_rms_hz"]:
            best = {"residual_rms_hz": round(rms, 1), "t_shift_s": float(tsh),
                    "lo_offset_hz": round(off, 1), "pred": pr}
    assert best is not None   # the t_shift grid is non-empty, so a best fit always exists

    if a.out:
        with open(a.out, "w") as f:
            for t, d, s, _e in hi:
                f.write("%.3f %.1f\n" % (t, d))

    print(json.dumps({
        "ok": True,
        "capture": a.capture,
        "duration_s": round(dur, 1),
        "windows": int(len(track)),
        "high_snr_windows": int(len(hi)),
        "snr_db_range": [round(float(track[:, 2].min()), 1), round(float(track[:, 2].max()), 1)],
        "measured_span_hz": round(float(hi[:, 1].max() - hi[:, 1].min()), 0),
        "predicted_span_hz": round(float(best["pred"].max() - best["pred"].min()), 0),
        "residual_rms_hz": best["residual_rms_hz"],
        "t_shift_s": best["t_shift_s"],
        "lo_offset_hz": best["lo_offset_hz"],
        "geo_note": "APPROXIMATE -- station geo is PENDING_needs_GPS_PPS",
        "claim": "measured off-air Doppler is consistent with this orbit over ~this location at this time (provenance, SNR-limited; NOT proof, NOT precise location)",
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
