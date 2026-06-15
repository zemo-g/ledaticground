#!/usr/bin/env python3.11
# Mini-side: print the next decodable weather-sat pass as machine-readable fields for
# pass_scheduler.sh, using ACCURATE skyfield/full-SGP4 timing (same engine as
# pass_schedule.py, reading data/tle_weather.txt).
#
# REPLACES the old passes.rail path (2026-06-08): that simplified-SGP4 source was
# ~27 min off AND emitted spurious/phantom passes (e.g. three "NOAA 19" passes inside
# 90 min, orbitally impossible), so the scheduler was preempting AIS to record empty
# sky. Output contract is a superset of before (adds MODE=), so pass_scheduler.sh is
# untouched (it just sets an extra harmless var under eval).
#
# SCOPE:
#   default   -> NOAA 15/19 only (analog APT). These decode end-to-end via the live
#                FM-demod-audio + APT path (pi_record.sh -> recv_decode.sh).
#   --all     -> also METEOR-M2 2/3/4 (LRPT). Use this for the RAW-IQ scheduler: raw IQ
#                captures any carrier and the waterfall Doppler-drift discriminator tells
#                us if the antenna heard the bird, independent of the (still-synthetic)
#                LRPT decoder. Do NOT use --all with the FM-audio APT path — it can't
#                decode LRPT and would log a misleading "noise" verdict.
#
# On any error (skyfield/TLE/etc.) prints NONE and exits 0 so the LaunchAgent loop
# degrades gracefully (sleeps + retries) instead of crashing under `set -u`.
import sys, time

GD = "/Users/ledaticempire/projects/ledaticground"
LAT, LON = 42.31, -83.08                      # Detroit Salsa Co (geometry only; receipt geo stays PENDING)
# --constellation orbcomm : load data/tle_orbcomm.txt + the Orbcomm in-band downlinks
#   (137.2-137.8 MHz, MODE=ORBCOMM); single best-elevation Orbcomm pass for --once.
#   Same SATS-dict extension + same SGP4 engine as autocap/enum_passes.py.
CONST  = (sys.argv[sys.argv.index('--constellation') + 1].lower()
          if '--constellation' in sys.argv else 'weather')
HOURS  = int(sys.argv[sys.argv.index('--hours') + 1]) if '--hours' in sys.argv else 24
ALL    = '--all' in sys.argv
# Orbcomm carriers detectable lower than LRPT images -> default MINEL 25 for orbcomm.
_DEF_MINEL = 25
MIN_EL = int(sys.argv[sys.argv.index('--minel') + 1]) if '--minel' in sys.argv else _DEF_MINEL

# name -> (downlink Hz, mode). Transmitter ground truth verified 2026-06-10
# (SatNOGS DB + usradioguy): NOAA 15/19 APT decommissioned Aug 2025 — the APT mode
# is off the air entirely, so the default (APT-only) scope now yields NONE, which
# is correct. M2-2 LRPT dead (power damage). M2-4 is 137.9 NOT 137.1 (both its
# captures were empty spectrum). Keep in sync with autocap/enum_passes.py.
SATS = {
    # "NOAA 15":     (137620000, "APT"),   # decommissioned 2025-08-19 (last APT bird)
    # "NOAA 19":     (137100000, "APT"),   # decommissioned 2025-08-13
    # "METEOR-M2 2": (137900000, "LRPT"),  # LRPT dead (micrometeorite power damage)
    "METEOR-M2 3": (137900000, "LRPT"),
    "METEOR-M2 4": (137900000, "LRPT"),   # was 137100000 — wrong freq
}
WANT = SATS if ALL else {k: v for k, v in SATS.items() if v[1] == "APT"}

# Orbcomm in-band downlinks (137.2-137.8 MHz, SD-PSK ~4800 sym/s; representative
# channels per the design — confirm exact per-sat downlink before live capture).
# Keep in sync with autocap/enum_passes.py.
ORBCOMM_CHANNELS = [137250000, 137440000, 137662500, 137737500]


def _orbcomm_freq(name):
    digits = ''.join(c for c in name if c.isdigit())
    return ORBCOMM_CHANNELS[(int(digits) if digits else 0) % len(ORBCOMM_CHANNELS)]


if CONST == 'orbcomm':
    TLE = f"{GD}/data/tle_orbcomm.txt"
else:
    TLE = f"{GD}/data/tle_weather.txt"

try:
    from skyfield.api import load, wgs84, EarthSatellite
    from datetime import timedelta

    ts = load.timescale()
    lines = [l.rstrip() for l in open(TLE)]
    sats = {}
    i = 0
    while i < len(lines) - 2:
        nm = lines[i].strip()
        if CONST == 'orbcomm':
            want = ('ORBCOMM' in nm.upper())
        else:
            want = (nm in WANT)
        if want and lines[i + 1].startswith('1 ') and lines[i + 2].startswith('2 '):
            sats[nm] = EarthSatellite(lines[i + 1], lines[i + 2], nm, ts); i += 3
        else:
            i += 1

    station = wgs84.latlon(LAT, LON)
    t0 = ts.now(); t1 = ts.from_datetime(t0.utc_datetime() + timedelta(hours=HOURS))
    try:
        open(f"{GD}/data/now_unix.txt", "w").write(str(int(time.time())))
    except OSError:
        pass

    passes = []
    for nm, sat in sats.items():
        t, ev = sat.find_events(station, t0, t1, altitude_degrees=float(MIN_EL))
        cur = None
        for ti, evi in zip(t, ev):
            if evi == 0:
                cur = {'aos': ti}
            elif evi == 1 and cur is not None:
                alt, _, _ = (sat - station).at(ti).altaz(); cur['maxel'] = alt.degrees
            elif evi == 2 and cur is not None and 'maxel' in cur:
                cur['los'] = ti; cur['name'] = nm; passes.append(cur); cur = None

    passes = [p for p in passes if p['maxel'] >= MIN_EL]
    passes.sort(key=lambda p: p['aos'].utc_datetime())
    if not passes:
        print("NONE"); sys.exit(0)

    # weather/APT/LRPT: the EARLIEST pass (the scheduler captures them in order).
    # orbcomm: the SINGLE BEST (highest-elevation) upcoming pass — many sats overlap
    # and the best SNR (given no LNA) is the highest bird; matches enum_passes.py's
    # best-per-slot policy for the --once capture decision.
    if CONST == 'orbcomm':
        p = max(passes, key=lambda q: q['maxel'])
    else:
        p = passes[0]
    aos = p['aos'].utc_datetime(); los = p['los'].utc_datetime()
    aos_epoch = int(aos.timestamp())
    mins = max(0, round((aos_epoch - int(time.time())) / 60))
    dur  = max(1, round((los - aos).total_seconds() / 60))
    elev = round(p['maxel'])
    sat  = p['name']
    if CONST == 'orbcomm':
        freq, mode = _orbcomm_freq(sat), 'ORBCOMM'
    else:
        freq, mode = SATS[sat]
    print(f'SAT="{sat}" MINS={mins} DUR={dur} ELEV={elev} FREQ={freq} MODE={mode} AOS_EPOCH={aos_epoch}')
except Exception as e:
    sys.stderr.write(f"next_pass.py: {e}\n")
    print("NONE"); sys.exit(0)
