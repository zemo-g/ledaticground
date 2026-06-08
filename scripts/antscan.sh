#!/bin/bash
# Wide-spectrum survey via the RTL-SDR: what is the antenna+feed ACTUALLY receiving?
# Reveals (a) is the chain alive at all (strong broadcast/known signals present),
# (b) is a strong out-of-band signal overloading the front end, (c) noise-floor shape
# per band. Compare band medians: a live antenna shows FM broadcast WAY above the floor.
# Usage: antscan.sh [gain]
G=${1:-40}
rtl_power -f 88M:174M:100k -g "$G" -i 1 -1 /tmp/wide.csv 2>/dev/null
python3 - <<'PY'
import csv, statistics
bins=[]
for row in csv.reader(open('/tmp/wide.csv')):
    if len(row)<7: continue
    try:
        lo=float(row[2]); step=float(row[4]); dbs=[float(x) for x in row[6:]]
    except ValueError: continue
    for i,db in enumerate(dbs):
        if db==db: bins.append((lo+i*step, db))   # drop nan
bands={'FM bcast 88-108':(88e6,108e6),'airband 118-137':(118e6,137e6),
       '137 SAT 137-138':(137e6,138e6),'2m ham 144-148':(144e6,148e6),
       'marine/AIS 156-163':(156e6,163e6),'VHF-hi 163-174':(163e6,174e6)}
print("band floor (median) + peak:")
for name,(a,b) in bands.items():
    v=[db for f,db in bins if a<=f<b]
    if v: print(f"  {name:22} median={statistics.median(v):6.1f} dB   peak={max(v):6.1f} dB")
print("strongest 8 bins (what the antenna IS hearing):")
for f,db in sorted(bins,key=lambda x:-x[1])[:8]:
    print(f"  {f/1e6:8.3f} MHz  {db:6.1f} dB")
PY
