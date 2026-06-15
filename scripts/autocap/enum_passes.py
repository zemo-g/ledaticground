#!/usr/bin/env python3.11
# ledaticground / autocap — MINI-SIDE schedule ENUMERATOR.
#
# Prints ALL catchable weather-sat passes for the next 48h (one per line) as the
# schedule-file contract consumed by the Pi-side capture agent. This is the
# decoupling key: the Mini computes the whole window AHEAD of time and pushes it,
# so the Pi can capture autonomously even when roof WiFi is down at AOS.
#
# REUSES the exact orbital engine of scripts/pass_schedule.py (full SGP4 via
# skyfield, the SAME data/tle_weather.txt, the SAME station LAT/LON 42.31/-83.08,
# the SAME find_events AOS/maxEl/LOS extraction). The ONLY differences are:
#   * horizon = 48h (the schedule must cover >=24h with margin so a missed push
#     still leaves the Pi a valid forward window),
#   * machine-readable TAB output instead of the human table,
#   * a hard maxel>=MINEL (default 40) cutoff (high-elevation passes only — the
#     same threshold the live iq LaunchAgent uses), and
#   * per-sat downlink FREQ in Hz + MODE pulled from the SATS table (same values
#     as scripts/next_pass.py, which is the canonical Hz+mode source).
#
# OUTPUT CONTRACT (stdout, one pass per line, sorted by AOS ascending), fields
# EXACTLY and TAB-separated:
#     AOS_EPOCH <TAB> DUR_MIN <TAB> ELEV <TAB> FREQ_HZ <TAB> MODE <TAB> SAT
#   - AOS_EPOCH : unix seconds UTC (int)
#   - DUR_MIN   : whole minutes LOS-AOS (int, >=1)
#   - ELEV      : rounded max elevation in degrees (int)
#   - FREQ_HZ   : downlink centre frequency in Hz (int)
#   - MODE      : APT | LRPT
#   - SAT       : satellite name, MAY CONTAIN SPACES (it is the LAST field so a
#                 downstream `cut -f6-` / split-on-first-5-tabs keeps it intact)
#
# On ANY error (missing/garbage TLE, skyfield failure, etc.) this prints NOTHING
# to stdout and exits 0, so the pushing wrapper degrades gracefully: it simply
# writes an empty schedule (or, better, the wrapper keeps the previous good file
# — see push_iq_schedule.sh, which only swaps in a NON-empty result).
#
#   enum_passes.py [--hours 48] [--minel 40] [--constellation weather|orbcomm]
#
# ORBCOMM mode (--constellation orbcomm): Orbcomm is a LEO constellation (~30+
# active sats, ~775 km, ~99 min orbit), so unlike fixed-channel ACARS it needs pass
# prediction EXACTLY like LRPT. It REUSES this same skyfield/full-SGP4 engine but:
#   * loads data/tle_orbcomm.txt (kept fresh by fetch_tle.sh) instead of tle_weather.txt,
#   * the in-band-on-halo per-sat downlinks sit in 137.2-137.8 MHz (see ORBCOMM dict),
#     MODE='ORBCOMM',
#   * MINEL default 25 (lower than LRPT's 40 — Orbcomm carriers are detectable lower;
#     the GOAL is proof-of-reception/characterization, not image decode),
#   * because many Orbcomm sats have OVERLAPPING passes, it emits the single
#     HIGHEST-elevation Orbcomm pass per non-overlapping time-slot (best SNR given no
#     LNA) rather than every sat — so orbcomm_monitor.sh captures the best bird only.
# Output contract is IDENTICAL (AOS_EPOCH/DUR_MIN/ELEV/FREQ_HZ/MODE/SAT, SAT last).
#
# CLI flags mirror pass_schedule.py / next_pass.py for muscle-memory consistency.
import sys

GD = "/Users/ledaticempire/projects/ledaticground"
LAT, LON = 42.31, -83.08          # Detroit Salsa Co — geometry ONLY (receipt geo stays PENDING)

# CLI overrides (same parsing idiom as pass_schedule.py).
CONST = (sys.argv[sys.argv.index('--constellation') + 1].lower()
         if '--constellation' in sys.argv else 'weather')
HOURS = int(sys.argv[sys.argv.index('--hours') + 1]) if '--hours' in sys.argv else 48
# Orbcomm carriers are detectable at lower elevation than LRPT images; default MINEL 25.
_DEF_MINEL = 25 if CONST == 'orbcomm' else 40
MINEL = int(sys.argv[sys.argv.index('--minel') + 1]) if '--minel' in sys.argv else _DEF_MINEL

# name -> (downlink Hz, mode). VERBATIM from scripts/next_pass.py SATS (the
# canonical Hz+mode source). APT = NOAA analog; LRPT = Meteor digital. The Pi
# capture agent records raw cu8 IQ regardless of mode, so all of these are valid
# schedule entries (unlike the FM-audio APT-only path, which would be misled by a
# Meteor pass). Keep this dict in sync with next_pass.py if frequencies change.
SATS = {
    # TRANSMITTER GROUND TRUTH (verified 2026-06-10 against SatNOGS DB + usradioguy):
    #   NOAA 19 APT: DECOMMISSIONED 2025-08-13. NOAA 15 APT: DECOMMISSIONED 2025-08-19
    #     (the last APT bird — the mode is off the air entirely). Scheduling them
    #     guaranteed flat-noise captures and poisoned every antenna conclusion.
    #   METEOR-M2 2: LRPT dead (micrometeorite power-system damage; battery can't
    #     carry the transmitter; HRPT ended 2024-07).
    #   METEOR-M2 4: transmits 137.9 MHz (NOT 137.1 — we had it wrong; both its
    #     captures were empty spectrum). M2-3 + M2-4 both 137.9 / 72k as of mid-2026.
    # Proof the chain works: M2-3 el78 2026-06-09 02:16Z @137.9 -> 1023 CADUs.
    # "NOAA 15":     (137620000, "APT"),   # decommissioned 2025-08-19
    # "NOAA 19":     (137100000, "APT"),   # decommissioned 2025-08-13
    # "METEOR-M2 2": (137900000, "LRPT"),  # LRPT dead (power damage)
    "METEOR-M2 3": (137900000, "LRPT"),
    "METEOR-M2 4": (137900000, "LRPT"),   # was 137100000 — wrong freq
}

# Orbcomm constellation (--constellation orbcomm). LEO ~775 km, ~99 min orbit; the
# in-band-on-halo subscriber/gateway downlinks sit in 137.2-137.8 MHz, ~25 kHz
# channels, SD-PSK ~4800 sym/s (FCC filings + SDR-RE; src/orbcomm_char.rail header).
# REPRESENTATIVE downlinks per the design (137.2500/137.4400/137.6625/137.7375) — the
# EXACT per-sat channel must be confirmed against the current Orbcomm constellation
# plan before live capture (open_question in inband-signals.json). MODE=ORBCOMM for
# all (the per-sat downlink is what gets tuned at capture time). Sats are matched by
# NAME against data/tle_orbcomm.txt; names use the CelesTrak 'ORBCOMM FM<NN>' form.
# Per-sat channel assignment cycles the 4 representative channels deterministically by
# the sat's catalog suffix so each bird maps to a stable in-band downlink.
ORBCOMM_CHANNELS = [137250000, 137440000, 137662500, 137737500]


def _orbcomm_freq(name):
    """Deterministic in-band channel for an Orbcomm sat by its FM number (stable
    mapping; the real per-sat downlink must be confirmed before live capture)."""
    digits = ''.join(c for c in name if c.isdigit())
    idx = (int(digits) if digits else 0) % len(ORBCOMM_CHANNELS)
    return ORBCOMM_CHANNELS[idx]


if CONST == 'orbcomm':
    TLE = f"{GD}/data/tle_orbcomm.txt"
    # SATS resolved dynamically from the TLE names (the constellation is large + churns);
    # any 'ORBCOMM' name in the TLE file is a candidate. Built after the file is read.
    SATS = None
else:
    TLE = f"{GD}/data/tle_weather.txt"

try:
    from skyfield.api import load, wgs84, EarthSatellite
    from datetime import timedelta

    ts = load.timescale()

    # Parse the TLE file EXACTLY as pass_schedule.py does: 3-line groups
    # (name / "1 " / "2 "), matching only the sats we WANT. Name lines in the
    # CelesTrak file carry trailing whitespace, so .strip() the name before lookup.
    lines = [l.rstrip() for l in open(TLE)]
    sats = {}
    i = 0
    while i < len(lines) - 2:
        nm = lines[i].strip()
        # weather: match the curated SATS dict by exact name.
        # orbcomm: the constellation is large + churns, so match ANY 'ORBCOMM' name in
        # the TLE file (FREQ/MODE assigned per-sat below). This makes SATS dynamic.
        if CONST == 'orbcomm':
            want = ('ORBCOMM' in nm.upper())
        else:
            want = (nm in SATS)
        if want and lines[i + 1].startswith('1 ') and lines[i + 2].startswith('2 '):
            sats[nm] = EarthSatellite(lines[i + 1], lines[i + 2], nm, ts)
            i += 3
        else:
            i += 1

    station = wgs84.latlon(LAT, LON)
    t0 = ts.now()
    t1 = ts.from_datetime(t0.utc_datetime() + timedelta(hours=HOURS))

    # Per-sat AOS/maxEl/LOS extraction — identical event-walk to pass_schedule.py.
    # find_events with altitude_degrees=MINEL means an event triple is only emitted
    # when the sat clears MINEL, so 'rise' here is "rise above MINEL". We additionally
    # filter on the recorded culmination elevation below (belt-and-suspenders, and to
    # match next_pass.py's explicit post-filter).
    passes = []
    for nm, sat in sats.items():
        t, ev = sat.find_events(station, t0, t1, altitude_degrees=float(MINEL))
        cur = None
        for ti, evi in zip(t, ev):
            if evi == 0:                       # rise above MINEL
                cur = {'aos': ti}
            elif evi == 1 and cur is not None:  # culmination
                alt, _, _ = (sat - station).at(ti).altaz()
                cur['maxel'] = alt.degrees
            elif evi == 2 and cur is not None and 'maxel' in cur:  # set below MINEL
                cur['los'] = ti
                cur['name'] = nm
                passes.append(cur)
                cur = None

    # Enforce maxel>=MINEL explicitly and sort by AOS ascending (schedule contract).
    passes = [p for p in passes if p['maxel'] >= MINEL]
    passes.sort(key=lambda p: p['aos'].utc_datetime())

    # ORBCOMM: many sats overlap, so emit the single BEST-elevation pass per
    # non-overlapping time-slot (best SNR given no LNA), not every bird. Greedy:
    # walk AOS-sorted passes, keep the highest-maxel pass whose window does not
    # overlap an already-kept one. This guarantees no two kept passes overlap, and
    # it implicitly prevents two impossible same-sat passes inside one slot.
    if CONST == 'orbcomm':
        kept = []
        for p in sorted(passes, key=lambda q: -q['maxel']):   # highest elevation first
            a0 = p['aos'].utc_datetime(); l0 = p['los'].utc_datetime()
            overlap = False
            for k in kept:
                a1 = k['aos'].utc_datetime(); l1 = k['los'].utc_datetime()
                if a0 < l1 and a1 < l0:
                    overlap = True
                    break
            if not overlap:
                kept.append(p)
        passes = sorted(kept, key=lambda q: q['aos'].utc_datetime())

    out = []
    for p in passes:
        aos = p['aos'].utc_datetime()
        los = p['los'].utc_datetime()
        aos_epoch = int(aos.timestamp())
        dur = max(1, round((los - aos).total_seconds() / 60))
        elev = round(p['maxel'])
        sat = p['name']
        if CONST == 'orbcomm':
            freq, mode = _orbcomm_freq(sat), 'ORBCOMM'
        else:
            freq, mode = SATS[sat]
        # SAT is last so spaces in the name don't break TAB parsing downstream.
        out.append(f"{aos_epoch}\t{dur}\t{elev}\t{freq}\t{mode}\t{sat}")

    # Single write; empty output (no passes) is a valid, expected result.
    sys.stdout.write("\n".join(out))
    if out:
        sys.stdout.write("\n")
    sys.exit(0)

except Exception as e:
    # Degrade gracefully: nothing to stdout, note on stderr, exit 0. The pushing
    # wrapper treats an empty enumeration as "keep the previous schedule".
    sys.stderr.write(f"enum_passes.py: {e}\n")
    sys.exit(0)
