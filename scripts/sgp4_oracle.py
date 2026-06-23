#!/usr/bin/env python3
# sgp4_oracle.py -- acceptance oracle for src/sgp4.rail (pure-Rail near-earth SGP4).
# Emits python-sgp4 (the canonical Vallado reference) position/velocity for the canonical
# verification TLE 00005 and the real NOAA/Meteor birds, at tsince grid points. The Rail port
# must match r to < 1e-6 km (Vallado vectors) and the Doppler derived from it to < 1 Hz.
#
#   pip install sgp4          (offline; no network in the binding path)
#   python3 scripts/sgp4_oracle.py
import sys
try:
    from sgp4.api import Satrec
except ImportError:
    sys.exit("need python-sgp4:  python3 -m pip install sgp4")

# canonical Vallado SGP4 verification TLE (catalog 00005) -- the bit-level reference.
CASES = [
    ("00005",
     "1 00005U 58002B   00179.78495062  .00000023  00000-0  28098-4 0  4753",
     "2 00005  34.2682 348.7242 1859667 331.7664  19.3264 10.82419157413667"),
    # real birds ledaticground binds against (137 MHz LEO); epochs ~2026-06-20.
    ("NOAA-19",
     "1 33591U 09005A   26171.44791411  .00000017  00000+0  32890-4 0  9996",
     "2 33591  98.9519 242.3252 0014118  25.5360 334.6507 14.13474744894941"),
]

for name, l1, l2 in CASES:
    s = Satrec.twoline2rv(l1, l2)
    print(f"# {name}  a={s.a:.10f}  mdot={s.mdot:.12e}  argpdot={s.argpdot:.12e}  nodedot={s.nodedot:.12e}")
    for tsince in (0.0, 360.0, 720.0):
        e, r, v = s.sgp4_tsince(tsince)
        print(f"  t={tsince:7.1f}  err={e}  r=({r[0]:.8f},{r[1]:.8f},{r[2]:.8f})  v=({v[0]:.9f},{v[1]:.9f},{v[2]:.9f})")
