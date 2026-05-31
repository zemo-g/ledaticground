#!/usr/bin/env python3.11
# Reference pass predictor using the real SGP4 propagator, to falsify the
# Rail simplified-J2 propagator. Same observer (regional) and window.
import math
from sgp4.api import Satrec

LAT, LON, ALT = 0.0, -0.0, 0.18          # deg, deg, km
D2R = math.pi/180.0
TARGETS = ("NOAA 15", "NOAA 19", "METEOR-M 2", "METEOR-M2 2", "METEOR-M2 3", "METEOR-M2 4")

now_unix = int(open("data/now_unix.txt").read().split()[0])
jd_now = now_unix/86400.0 + 2440587.5

# observer ECEF (WGS84) — same math as the Rail version
phi, lam = LAT*D2R, LON*D2R
ae, f = 6378.137, 1/298.257223563
e2 = f*(2-f)
N = ae/math.sqrt(1-e2*math.sin(phi)**2)
ox = (N+ALT)*math.cos(phi)*math.cos(lam)
oy = (N+ALT)*math.cos(phi)*math.sin(lam)
oz = (N*(1-e2)+ALT)*math.sin(phi)

def gmst(jd):
    d = jd - 2451545.0; t = d/36525.0
    g = 280.46061837 + 360.98564736629*d + 0.000387933*t*t - t*t*t/38710000.0
    return ((g % 360.0)*D2R)

def elaz(r, jd):
    g = gmst(jd); cg, sg = math.cos(g), math.sin(g)
    xe = r[0]*cg + r[1]*sg; ye = -r[0]*sg + r[1]*cg; ze = r[2]
    rx, ry, rz = xe-ox, ye-oy, ze-oz
    slat, clat, slon, clon = math.sin(phi), math.cos(phi), math.sin(lam), math.cos(lam)
    east  = -slon*rx + clon*ry
    north = -slat*clon*rx - slat*slon*ry + clat*rz
    up    =  clat*clon*rx + clat*slon*ry + slat*rz
    rng = math.sqrt(rx*rx+ry*ry+rz*rz)
    el = math.asin(up/rng)/D2R
    az = (math.atan2(east, north)/D2R) % 360.0
    return el, az

# parse TLE file
recs, lines = [], [l.rstrip("\n") for l in open("data/tle_weather.txt")]
i = 0
while i+2 < len(lines)+1 and i+2 <= len(lines)-1:
    name, l1, l2 = lines[i].strip(), lines[i+1], lines[i+2]
    if l1.startswith("1 ") and l2.startswith("2 "):
        recs.append((name, l1, l2)); i += 3
    else:
        i += 1

for name, l1, l2 in recs:
    if name not in TARGETS: continue
    sat = Satrec.twoline2rv(l1, l2)
    inpass = False; aos_min = 0; maxel = 0; aosaz = 0
    for k in range(2880):                       # 24h @ 30s
        jd_obs = jd_now + k*30/86400.0
        jd_i = math.floor(jd_obs); fr = jd_obs - jd_i
        err, r, v = sat.sgp4(jd_i, fr)
        if err != 0: continue
        el, az = elaz(r, jd_obs)
        mins = k*30/60.0
        if el >= 0:
            if not inpass:
                inpass = True; aos_min = mins; maxel = el; aosaz = az
            elif el > maxel:
                maxel = el
        else:
            if inpass:
                if maxel >= 10:
                    print(f"{name:24s} in {int(aos_min):4d} min | {int(mins-aos_min):2d} min | max El {int(maxel):2d} deg | rises az {int(aosaz):3d} deg")
                inpass = False
