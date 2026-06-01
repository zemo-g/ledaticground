#!/usr/bin/env python3
import sys, json, numpy as np
t=json.load(open('/tmp/ais_truth.json')); got={}
for l in open(sys.argv[1]):
    for k in ("TYPE","MMSI","LAT","LON","SOG","COG"):
        if l.startswith(k+"="): got[k]=l.strip().split("=")[1]
print("parsed:",got)
ok = (int(got.get("MMSI",-1))==t["mmsi"]
      and abs(float(got.get("LAT",9e9))-t["lat"])<1e-3
      and abs(float(got.get("LON",9e9))-t["lon"])<1e-3)
print(f"MMSI match + lat/lon within 0.001 deg: {ok}")
sys.exit(0 if ok else 1)
