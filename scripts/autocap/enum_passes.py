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
#   enum_passes.py [--hours 48] [--minel 40]
#
# CLI flags mirror pass_schedule.py / next_pass.py for muscle-memory consistency.
import sys

GD = "/Users/ledaticempire/projects/ledaticground"
TLE = f"{GD}/data/tle_weather.txt"
LAT, LON = 42.31, -83.08          # Detroit Salsa Co — geometry ONLY (receipt geo stays PENDING)

# CLI overrides (same parsing idiom as pass_schedule.py).
HOURS = int(sys.argv[sys.argv.index('--hours') + 1]) if '--hours' in sys.argv else 48
MINEL = int(sys.argv[sys.argv.index('--minel') + 1]) if '--minel' in sys.argv else 40

# name -> (downlink Hz, mode). VERBATIM from scripts/next_pass.py SATS (the
# canonical Hz+mode source). APT = NOAA analog; LRPT = Meteor digital. The Pi
# capture agent records raw cu8 IQ regardless of mode, so all of these are valid
# schedule entries (unlike the FM-audio APT-only path, which would be misled by a
# Meteor pass). Keep this dict in sync with next_pass.py if frequencies change.
SATS = {
    "NOAA 15":     (137620000, "APT"),
    "NOAA 19":     (137100000, "APT"),
    "METEOR-M2 2": (137900000, "LRPT"),
    "METEOR-M2 3": (137900000, "LRPT"),
    "METEOR-M2 4": (137100000, "LRPT"),
}

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
        if nm in SATS and lines[i + 1].startswith('1 ') and lines[i + 2].startswith('2 '):
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

    out = []
    for p in passes:
        aos = p['aos'].utc_datetime()
        los = p['los'].utc_datetime()
        aos_epoch = int(aos.timestamp())
        dur = max(1, round((los - aos).total_seconds() / 60))
        elev = round(p['maxel'])
        sat = p['name']
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
