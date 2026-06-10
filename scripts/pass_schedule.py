#!/usr/bin/env python3.11
# Accurate (full SGP4) pass schedule for the 137 MHz polar weather sats we can catch,
# from data/tle_weather.txt, for the Detroit Salsa Co station. Prints UTC + EDT.
from skyfield.api import load, wgs84, EarthSatellite
from datetime import timedelta
from zoneinfo import ZoneInfo
import sys

GD="/Users/ledaticempire/projects/ledaticground"
TLE=f"{GD}/data/tle_weather.txt"
LAT,LON = 42.31, -83.08
HOURS = int(sys.argv[sys.argv.index('--hours')+1]) if '--hours' in sys.argv else 24
MINEL = int(sys.argv[sys.argv.index('--minel')+1]) if '--minel' in sys.argv else 20
EDT = ZoneInfo("America/Detroit")

WANT = {  # name -> (downlink, mode) — ground truth verified 2026-06-10; sync w/ autocap/enum_passes.py
 # "NOAA 15":("137.620 MHz","APT"),   # decommissioned 2025-08-19 (last APT bird — mode off-air)
 # "NOAA 19":("137.100 MHz","APT"),   # decommissioned 2025-08-13
 # "METEOR-M2 2":("137.900 MHz","LRPT"),  # LRPT dead (micrometeorite power damage)
 "METEOR-M2 3":("137.900 MHz","LRPT"),
 "METEOR-M2 4":("137.900 MHz","LRPT"),   # was 137.100 — wrong freq
}
ts = load.timescale()
lines=[l.rstrip() for l in open(TLE)]
sats={}
i=0
while i < len(lines)-2:
    nm=lines[i].strip()
    if nm in WANT and lines[i+1].startswith('1 ') and lines[i+2].startswith('2 '):
        sats[nm]=EarthSatellite(lines[i+1], lines[i+2], nm, ts); i+=3
    else: i+=1
station=wgs84.latlon(LAT, LON)
t0=ts.now(); t1=ts.from_datetime(t0.utc_datetime()+timedelta(hours=HOURS))

passes=[]
for nm, sat in sats.items():
    t, ev = sat.find_events(station, t0, t1, altitude_degrees=float(MINEL))
    cur=None
    for ti, evi in zip(t, ev):
        if evi==0: cur={'aos':ti}
        elif evi==1 and cur is not None:
            alt,az,_=(sat-station).at(ti).altaz(); cur['maxt']=ti; cur['maxel']=alt.degrees
        elif evi==2 and cur is not None and 'maxt' in cur:
            cur['los']=ti; cur['name']=nm; passes.append(cur); cur=None
passes.sort(key=lambda p: p['aos'].utc_datetime())

print(f"=== 137 MHz weather-sat passes over Detroit, next {HOURS}h, max-el >= {MINEL} deg ===")
print(f"    (full SGP4; TLE epoch ~Jun 7; now {t0.utc_datetime().astimezone(EDT):%H:%M %Z} / {t0.utc_datetime():%H:%M} UTC)\n")
print(f"{'AOS (EDT)':>12} {'AOS (UTC)':>10}  {'sat':<12} {'maxEl':>5} {'dur':>4}  {'downlink':<12} mode")
for p in passes:
    aos=p['aos'].utc_datetime(); los=p['los'].utc_datetime()
    dur=int((los-aos).total_seconds()/60)
    dl,mode=WANT[p['name']]
    star=" <== HIGH" if p['maxel']>=40 else ""
    print(f"{aos.astimezone(EDT):%I:%M %p} {aos:%H:%MZ}  {p['name']:<12} {p['maxel']:4.0f}° {dur:3d}m  {dl:<12} {mode}{star}")
print(f"\n{len(passes)} catchable passes. APT (NOAA) = our decoder works; LRPT (Meteor) = digital, decoder still synthetic-only.")
