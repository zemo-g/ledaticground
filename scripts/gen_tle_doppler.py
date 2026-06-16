#!/usr/bin/env python3
# gen_tle_doppler.py — TLE-TRUE synthetic IQ generator for the physics-binding waves.
#
# THE POINT (vs gen_doppler_fm.py / gen_orbcomm_doppler.py): the existing generators paint
# a HAND-AUTHORED tanh Doppler curve. That can never bind to an SGP4 prediction — the curve
# is not the orbit's curve. This generator's Doppler curve IS src/doppler_range.rail's SGP4
# output over a REAL bundled NOAA-19 element set + a CLAIMED observer geo, sampled at the
# snapshot times. So predict-vs-measured can actually ALIGN: doppler_real.rail measures the
# centroid track of this IQ, doppler_range.rail predicts the same orbit+geo, and Wave B binds
# them with a small residual. This is the fixture Wave B's positive (physics_ok=1) test runs.
#
# It NEVER fetches live elements (bundled TLE below) and NEVER touches the live decode path
# or the live AIS ledger. It only writes synthetic IQ + a /tmp truth file.
#
# Honesty: the IQ is SYNTHETIC and the geo is CLAIMED/SYNTHETIC — the truth file records both
# explicitly (suffix _SYNTH). This is an offline mechanism fixture, not a real off-air capture.
#
# Format (mirrors the real noaa19_doppler.sh capture + gen_doppler_fm.py so the same
# doppler_real.rail pipeline runs unchanged): uint8 cu8 IQ, FS=60 kHz, N=16384 samples per
# snapshot, snapshots concatenated in time order into the output IQ file.
#
#   gen_tle_doppler.py [--out /tmp/dop_real.iq] [--lat L] [--lon L] [--alt-km A]
#                      [--fc-hz F] [--now-unix U] [--nsnap K] [--dt-snap S]
#                      [--const-off HZ] [--kf HZ] [--noise STD] [--seed S]
#                      [--tle-l1 "..."] [--tle-l2 "..."] [--rail-bin PATH]
#
# Output IQ        -> --out                       (default /tmp/dop_real.iq, what doppler_real reads)
# Truth file       -> <out>.truth.txt AND /tmp/gen_tle_doppler_truth.txt (named TLE+geo+fc + per-snap Doppler)
# Per-snap dirs    -> /tmp/dopcap_tletrue/         (snap_NNN.iq + dop_truth.npy + t_snap.npy for doppler_fit --synth)

import os, sys, subprocess, argparse
import numpy as np

REPO = "/Users/ledaticempire/projects/ledaticground"
RAIL_BIN_DEFAULT = "/Users/ledaticempire/projects/rail/rail_native"
DOPPLER_RANGE = os.path.join(REPO, "src", "doppler_range.rail")

# --- BUNDLED REAL NOAA-19 ELEMENT SET (do NOT fetch live) -------------------------------
# Verbatim from data/tle_weather.txt (NORAD 33591, epoch 26166.49283008). The COLUMNS are
# load-bearing — doppler_range.rail's set_elements_from_tle does str_sub by fixed offset, so
# preserve the exact spacing (these are standard 69-col TLE lines). Bundled, not fetched.
BUNDLED_TLE_L1 = "1 33591U 09005A   26166.49283008  .00000032  00000+0  40805-4 0  9995"
BUNDLED_TLE_L2 = "2 33591  98.9521 237.3664 0014363  39.0504 321.1702 14.13474065894244"
BUNDLED_SAT = "NOAA-19"
BUNDLED_NORAD_EPOCH = "33591@26166.49283008"

FS = 60000.0
N = 16384


def stage(path, val):
    with open(path, "w") as f:
        f.write(str(val) + "\n")


def run_doppler_range(rail_bin, query_unix_list, lat, lon, alt_km, fc_hz, now_unix, l1, l2):
    """Stage /tmp/dr_* files, run doppler_range.rail in QUERY mode, parse DOPQ lines.
    Returns dict unix -> (el_deg, range_km, dop_hz). The Doppler curve IS this SGP4 path."""
    stage("/tmp/dr_geo_lat.txt", repr(float(lat)))
    stage("/tmp/dr_geo_lon.txt", repr(float(lon)))
    stage("/tmp/dr_geo_alt.txt", repr(float(alt_km)))
    stage("/tmp/dr_fc.txt", repr(float(fc_hz)))
    stage("/tmp/dr_now_unix.txt", repr(float(now_unix)))
    with open("/tmp/dr_tle_l1.txt", "w") as f:
        f.write(l1 + "\n")
    with open("/tmp/dr_tle_l2.txt", "w") as f:
        f.write(l2 + "\n")
    with open("/tmp/dr_query_unix.txt", "w") as f:
        for u in query_unix_list:
            f.write(f"{int(round(u))}\n")
    # run from the ledaticground dir so the bare `data/` self-test path never engages (we
    # supply a non-empty query file, so main dispatches to query mode regardless of cwd).
    proc = subprocess.run(
        [rail_bin, "run", DOPPLER_RANGE],
        cwd=REPO, capture_output=True, text=True, timeout=180,
    )
    out = {}
    for line in proc.stdout.splitlines():
        if line.startswith("DOPQ "):
            p = line.split()
            # DOPQ <unix> <el_deg> <range_km> <dop_hz>
            out[int(p[1])] = (float(p[2]), float(p[3]), float(p[4]))
    if not out:
        sys.stderr.write("=== doppler_range.rail stdout ===\n" + proc.stdout + "\n")
        sys.stderr.write("=== stderr ===\n" + proc.stderr + "\n")
        raise SystemExit("gen_tle_doppler: doppler_range.rail emitted no DOPQ lines (compile/run failed?)")
    return out


def find_pass(rail_bin, lat, lon, alt_km, fc_hz, now_unix, l1, l2,
              horizon_s=43200, step_s=30, min_peak_el=10.0):
    """Coarse-scan [now, now+horizon] for the FIRST above-horizon window whose PEAK
    elevation >= min_peak_el. Returns (aos_unix, tca_unix, los_unix, peak_el). Uses the
    SAME doppler_range.rail SGP4 (its el column), so the fixture is self-consistent."""
    grid = [now_unix + k * step_s for k in range(horizon_s // step_s + 1)]
    res = run_doppler_range(rail_bin, grid, lat, lon, alt_km, fc_hz, now_unix, l1, l2)
    # contiguous above-horizon runs
    best = None  # (peak_el, aos, tca, los)
    run_pts = []  # (unix, el)
    def close_run(run_pts):
        nonlocal best
        if not run_pts:
            return
        els = [e for (_, e) in run_pts]
        peak = max(els)
        if peak >= min_peak_el:
            aos = run_pts[0][0]
            los = run_pts[-1][0]
            tca = run_pts[els.index(peak)][0]
            if best is None or peak > best[0]:
                best = (peak, aos, tca, los)
    for u in grid:
        el = res[int(u)][0]
        if el > 0.0:
            run_pts.append((u, el))
        else:
            close_run(run_pts)
            run_pts = []
            if best is not None:
                break  # first qualifying pass is enough for a fixture
    close_run(run_pts)
    if best is None:
        raise SystemExit(f"gen_tle_doppler: no pass with peak el>={min_peak_el} deg in "
                         f"{horizon_s}s window from now_unix={now_unix} over ({lat},{lon}).")
    peak, aos, tca, los = best
    return aos, tca, los, peak


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/dop_real.iq")
    ap.add_argument("--lat", type=float, default=42.5, help="CLAIMED observer latitude (deg)")
    ap.add_argument("--lon", type=float, default=-83.5, help="CLAIMED observer longitude (deg)")
    ap.add_argument("--alt-km", type=float, default=0.18)
    ap.add_argument("--fc-hz", type=float, default=137100000.0)
    ap.add_argument("--now-unix", type=int, default=None,
                    help="predictor epoch anchor; default = data/now_unix.txt")
    ap.add_argument("--nsnap", type=int, default=80)
    ap.add_argument("--dt-snap", type=float, default=None,
                    help="snapshot spacing (s); default spreads nsnap evenly across the pass")
    ap.add_argument("--const-off", type=float, default=1850.0,
                    help="nuisance constant carrier offset (Hz) — SDR ppm + carrier error")
    # Default is a NEAR-PURE carrier (kf=200): the binding fixture isolates the Doppler
    # curve so the measured centroid/peak sits on the shifted carrier center, giving the
    # few-Hz predict-vs-measured residual Wave B binds. Pass --kf 17000 to reproduce the
    # wideband APT-like shape of gen_doppler_fm.py (a much larger centroid residual).
    ap.add_argument("--kf", type=float, default=200.0, help="FM deviation (Hz); default near-pure carrier")
    ap.add_argument("--noise", type=float, default=0.10)
    ap.add_argument("--dc-spike", type=float, default=0.45)
    ap.add_argument("--seed", type=int, default=19)
    ap.add_argument("--el-floor", type=float, default=5.0,
                    help="only emit snapshots with predicted elevation >= this (deg)")
    ap.add_argument("--tle-l1", default=BUNDLED_TLE_L1)
    ap.add_argument("--tle-l2", default=BUNDLED_TLE_L2)
    ap.add_argument("--rail-bin", default=RAIL_BIN_DEFAULT)
    args = ap.parse_args()

    now_unix = args.now_unix
    if now_unix is None:
        with open(os.path.join(REPO, "data", "now_unix.txt")) as f:
            now_unix = int(f.read().strip())

    l1, l2 = args.tle_l1, args.tle_l2

    # 1) Find a real pass (SGP4 from doppler_range.rail) over the CLAIMED geo.
    aos, tca, los, peak_el = find_pass(args.rail_bin, args.lat, args.lon, args.alt_km,
                                       args.fc_hz, now_unix, l1, l2)
    pass_dur = los - aos
    sys.stderr.write(f"gen_tle_doppler: pass AOS={aos} TCA={tca} LOS={los} "
                     f"dur={pass_dur:.0f}s peak_el={peak_el:.1f}deg\n")

    # 2) Snapshot times spread across the pass (centred on TCA for the full S-curve).
    if args.dt_snap is not None:
        dt_snap = args.dt_snap
        half = (args.nsnap - 1) * dt_snap / 2.0
        snap_unix = [tca - half + i * dt_snap for i in range(args.nsnap)]
    else:
        # span ~the central 90% of the pass so we capture the steep TCA sweep
        span = 0.9 * pass_dur
        if args.nsnap <= 1:
            snap_unix = [tca]
        else:
            dt_snap = span / (args.nsnap - 1)
            snap_unix = [tca - span / 2.0 + i * dt_snap for i in range(args.nsnap)]

    # 3) True SGP4 Doppler at each snapshot (the curve the IQ will carry).
    res = run_doppler_range(args.rail_bin, snap_unix, args.lat, args.lon, args.alt_km,
                            args.fc_hz, now_unix, l1, l2)

    # 4) Keep only above-el-floor snapshots (Doppler below horizon is not a real obs).
    kept = []
    for u in snap_unix:
        ui = int(round(u))
        el, rng_km, dop = res[ui]
        if el >= args.el_floor:
            kept.append((ui, el, rng_km, dop))
    if len(kept) < 3:
        raise SystemExit(f"gen_tle_doppler: only {len(kept)} snapshots above el_floor="
                         f"{args.el_floor}deg — widen the pass or lower --el-floor.")

    # 5) Synthesize APT-like FM IQ whose carrier CENTER follows const_off + dop(t).
    rng = np.random.default_rng(args.seed)
    n = np.arange(N) / FS
    truth_dop = []
    t_snap = []
    outdir = "/tmp/dopcap_tletrue"
    os.makedirs(outdir, exist_ok=True)
    with open(args.out, "wb") as iqout:
        for idx, (ui, el, rng_km, dop) in enumerate(kept):
            fc = dop + args.const_off                  # measured carrier center for this snap
            # APT-like multitone FM message (2400 subcarrier + video tones)
            m = (0.6 * np.sin(2 * np.pi * 2400 * n)
                 + 0.25 * np.sin(2 * np.pi * 900 * n)
                 + 0.15 * np.sin(2 * np.pi * 4160 * n))
            phi = 2 * np.pi * fc * n + 2 * np.pi * args.kf * np.cumsum(m) / FS
            z = np.exp(1j * phi)
            z = z + (rng.standard_normal(N) + 1j * rng.standard_normal(N)) * args.noise
            z = z + args.dc_spike                      # rtl_sdr DC spike (exercises DC-skip)
            I = np.clip(127.5 + 90 * z.real, 0, 255).astype(np.uint8)
            Q = np.clip(127.5 + 90 * z.imag, 0, 255).astype(np.uint8)
            iq = np.empty(2 * N, np.uint8)
            iq[0::2] = I
            iq[1::2] = Q
            iq.tofile(os.path.join(outdir, f"snap_{idx:03d}.iq"))
            iqout.write(iq.tobytes())
            truth_dop.append(dop)
            t_snap.append(ui - kept[0][0])

    truth_dop = np.array(truth_dop, np.float64)
    t_snap = np.array(t_snap, np.float64)
    np.save(os.path.join(outdir, "dop_truth.npy"), truth_dop)
    np.save(os.path.join(outdir, "t_snap.npy"), t_snap)
    # also mirror to /tmp/dopcap_synth so the existing doppler_fit.py --synth path works
    os.makedirs("/tmp/dopcap_synth", exist_ok=True)
    np.save("/tmp/dopcap_synth/dop_truth.npy", truth_dop)
    np.save("/tmp/dopcap_synth/t_snap.npy", t_snap)

    # 6) Truth file — names the EXACT TLE + geo + fc the curve was generated from, so
    #    predict-vs-measured (Wave B) knows precisely what orbit+geo to re-predict against.
    #    Geo + IQ are SYNTHETIC/CLAIMED -> _SYNTH suffix (never a committed coordinate).
    truth_lines = [
        "# gen_tle_doppler truth — SYNTHETIC IQ, CLAIMED(SYNTH) geo. Offline binding fixture.",
        f"kind=TLE_TRUE_DOPPLER_SYNTH",
        f"sat={BUNDLED_SAT}",
        f"orbit={BUNDLED_NORAD_EPOCH}",
        f"tle_l1={l1}",
        f"tle_l2={l2}",
        f"claimed_geo_SYNTH={args.lat},{args.lon},{args.alt_km}km",
        f"fc_hz={args.fc_hz:.6f}",
        f"now_unix={now_unix}",
        f"fs_hz={FS:.1f}",
        f"n_samples_per_window={N}",
        f"const_off_hz={args.const_off:.6f}",
        f"kf_hz={args.kf:.6f}",
        f"aos_unix={aos}",
        f"tca_unix={tca}",
        f"los_unix={los}",
        f"peak_el_deg={peak_el:.6f}",
        f"el_floor_deg={args.el_floor:.3f}",
        f"nsnap_kept={len(kept)}",
        f"out_iq={args.out}",
        f"first_snap_unix={kept[0][0]}",
    ]
    for idx, (ui, el, rng_km, dop) in enumerate(kept):
        truth_lines.append(f"snap {idx} unix={ui} el_deg={el:.6f} range_km={rng_km:.6f} dop_hz={dop:.6f}")
    truth_txt = "\n".join(truth_lines) + "\n"
    with open("/tmp/gen_tle_doppler_truth.txt", "w") as f:
        f.write(truth_txt)
    with open(args.out + ".truth.txt", "w") as f:
        f.write(truth_txt)

    print(f"gen_tle_doppler: sat={BUNDLED_SAT} orbit={BUNDLED_NORAD_EPOCH} "
          f"claimed_geo_SYNTH=({args.lat},{args.lon}) fc={args.fc_hz/1e6:.3f}MHz")
    print(f"  pass AOS={aos} TCA={tca} LOS={los} peak_el={peak_el:.1f}deg; "
          f"kept {len(kept)}/{len(snap_unix)} snaps above {args.el_floor}deg")
    print(f"  true SGP4 Doppler {truth_dop[0]:+.0f}..{truth_dop[-1]:+.0f} Hz "
          f"(+const_off {args.const_off:.0f} Hz carrier offset)")
    print(f"  wrote IQ -> {args.out} ({len(kept)*2*N} bytes); truth -> "
          f"/tmp/gen_tle_doppler_truth.txt + {args.out}.truth.txt")
    print("  NOTE: SYNTHETIC IQ, CLAIMED(_SYNTH) geo — offline physics-binding fixture, not real RF.")


if __name__ == "__main__":
    main()
