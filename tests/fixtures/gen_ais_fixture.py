#!/usr/bin/env python3
"""SYNTHETIC ais.jsonl fixture generator (OFFLINE VALIDATION ONLY).

The real Detroit-River AIS feed (~1885 type-1/2/3 position fixes, ~61 Class-A
vessels) lives on the live roof node and is NOT in this public repo. This
generator builds a DETERMINISTIC, clearly-synthetic stand-in with the SAME
schema the live decoder emits (scripts/ais_census.py parse()):

    {"type": 1|2|3, "mmsi": <int>, "lat": <float>, "lon": <float>,
     "sog": <float kn>, "cog": <float deg>, "ts": <unix int>}

so transit_log.py can be validated offline without touching ~/.ledatic.
NOT real RF, NOT attested, NOT a fact. Honest label: synthetic fixture.

  python3 tests/fixtures/gen_ais_fixture.py > tests/fixtures/ais.jsonl

It plants ~61 vessels along the Detroit River channel (Lake Erie mouth at the
south, Lake St. Clair at the north), each with a multi-fix time-ordered track,
a mix of up/down-bound and a few holding (anchored) vessels, plus some non-
position frames (type 4 base, type 21 AtoN) that transit_log.py must IGNORE.
Deterministic: a fixed seed -> byte-identical output every run.
"""
import json
import math
import random

# Detroit River channel axis, south (Lake Erie, ~41.98N) -> north (Lk St Clair, ~42.46N).
# Roughly N-S; the longitude drifts west then east. A coarse centerline is enough for a
# fixture: real fixes scatter +/- around it.
CHANNEL = [
    (41.980, -83.140),  # Lake Erie light / Bar Point approach
    (42.050, -83.135),  # Livingstone / Amherstburg
    (42.130, -83.130),  # lower river
    (42.200, -83.130),  # Wyandotte
    (42.290, -83.100),  # Fort Wayne
    (42.320, -83.055),  # Ambassador Bridge
    (42.345, -82.990),  # Belle Isle
    (42.400, -82.870),  # upper river
    (42.460, -82.780),  # Lake St. Clair light
]

# MMSI MID (first 3 digits) -> flag, so transit_log.py flag-resolution has something to chew on.
# 366/367/368/369 = USA, 316 = Canada, 235/232 = UK, 477 = Hong Kong, 538 = Marshall Is.
MIDS = [366, 367, 368, 369, 316, 316, 235, 477, 538, 311]

NAMES = [
    "PAUL R TREGURTHA", "EDWIN H GOTT", "ARTHUR M ANDERSON", "JAMES R BARKER",
    "STEWART J CORT", "ROGER BLOUGH", "MESABI MINER", "WALTER J MCCARTHY",
    "AMERICAN INTEGRITY", "BURNS HARBOR", "INDIANA HARBOR", "CASON J CALLAWAY",
    "JOHN G MUNSON", "PHILIP R CLARKE", "ALGOMA CONVEYOR", "CSL NIAGARA",
    "FEDERAL YUKON", "MANITOULIN", "WHITEFISH BAY", "ALGOMA SAULT",
    "BAIE COMEAU", "TIM S DOOL", "FEDERAL KIVALINA", "SPRUCEGLEN",
]


def interp(frac):
    """Position along the channel polyline at arc-fraction frac in [0,1]."""
    if frac <= 0:
        return CHANNEL[0]
    if frac >= 1:
        return CHANNEL[-1]
    n = len(CHANNEL) - 1
    x = frac * n
    i = int(x)
    t = x - i
    a = CHANNEL[i]
    b = CHANNEL[i + 1]
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def main():
    rng = random.Random(20260615)  # deterministic
    rows = []

    base_ts = 1780322600  # ~2026-06; matches the live feed's ts magnitude (> 1e9)

    n_vessels = 61
    for vi in range(n_vessels):
        mid = MIDS[vi % len(MIDS)]
        mmsi = mid * 1000000 + 100000 + vi * 137 % 899999
        name = NAMES[vi % len(NAMES)] if vi % 3 != 2 else ""  # some without a name (type 1/2/3 carry no name; resolved elsewhere)

        # role: 0..0.44 upbound, 0.45..0.88 downbound, rest holding
        roll = rng.random()
        if roll < 0.45:
            role = "up"
        elif roll < 0.88:
            role = "down"
        else:
            role = "hold"

        nfix = rng.randint(3, 22)  # multi-fix track
        cadence = rng.choice([30, 45, 60, 90])  # seconds between fixes
        start_ts = base_ts + rng.randint(0, 1800)
        sog = round(rng.uniform(8.0, 15.5), 1) if role != "hold" else round(rng.uniform(0.0, 0.4), 1)

        # start fraction along channel + per-step progress
        if role == "up":
            frac = rng.uniform(0.0, 0.4)
            step = rng.uniform(0.012, 0.03)
            cog = round(rng.uniform(0.0, 30.0) if rng.random() < 0.5 else rng.uniform(330.0, 360.0), 1)
        elif role == "down":
            frac = rng.uniform(0.6, 1.0)
            step = -rng.uniform(0.012, 0.03)
            cog = round(rng.uniform(160.0, 200.0), 1)
        else:  # hold
            frac = rng.uniform(0.2, 0.8)
            step = 0.0
            cog = round(rng.uniform(0.0, 360.0), 1)

        typ = rng.choice([1, 1, 1, 3])  # mostly type 1
        for k in range(nfix):
            f = max(0.0, min(1.0, frac + step * k))
            lat, lon = interp(f)
            # scatter so net_km/bearing are non-trivial; hold vessels barely move
            jit = 0.0008 if role != "hold" else 0.00015
            lat += rng.uniform(-jit, jit)
            lon += rng.uniform(-jit, jit)
            ts = start_ts + k * cadence
            rows.append({
                "type": typ,
                "mmsi": mmsi,
                "lat": round(lat, 5),
                "lon": round(lon, 5),
                "sog": round(sog + rng.uniform(-0.3, 0.3), 1) if role != "hold" else sog,
                "cog": round((cog + rng.uniform(-3, 3)) % 360.0, 1),
                "ts": ts,
            })

    # a handful of non-position frames transit_log.py must IGNORE (type 4 base, type 21 AtoN)
    rows.append({"type": 4, "mmsi": 3669778, "lat": 42.28462, "lon": -83.14031, "ts": base_ts + 60})
    rows.append({"type": 21, "mmsi": 993672199, "name": "1", "lat": 41.82416, "lon": -83.19095, "ts": base_ts + 90})
    # a pre-NTP outlier (ts < 1e9) transit_log.py clean_ts must DROP
    rows.append({"type": 1, "mmsi": 366111999, "lat": 42.3, "lon": -83.05, "sog": 9.0, "cog": 0.0, "ts": 123})

    # shuffle so transit_log.py's per-vessel ts-sort is actually exercised (not pre-sorted)
    rng.shuffle(rows)
    for r in rows:
        print(json.dumps(r, separators=(",", ":")))


if __name__ == "__main__":
    main()
