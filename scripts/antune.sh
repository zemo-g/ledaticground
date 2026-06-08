#!/bin/bash
# Antenna-tuning "meter" using the RTL-SDR itself (no NanoVNA needed). A better-matched /
# on-resonance antenna couples MORE ambient RF into the receiver, so the received noise
# floor across the antenna's band rises. We report the median floor in the 137 band and
# its delta vs a just-out-of-band reference; MAXIMIZE "137-band" while adjusting the antenna.
# Fixed gain so readings are comparable run-to-run. Usage: antune.sh [gain]
G=${1:-40}
rtl_power -f 134M:140M:25k -g "$G" -i 2 -1 /tmp/atune.csv 2>/dev/null
python3 - <<'PY'
import csv, statistics
bins=[]
for row in csv.reader(open('/tmp/atune.csv')):
    if len(row)<7: continue
    try:
        lo=float(row[2]); step=float(row[4]); dbs=[float(x) for x in row[6:]]
    except ValueError: continue
    for i,db in enumerate(dbs):
        bins.append((lo+i*step, db))
def med(a,b):
    v=[db for f,db in bins if a<=f<b]
    return statistics.median(v) if v else float('nan')
inb=med(136e6,138e6); ref=med(138.5e6,140e6)
peak=max(bins,key=lambda x:x[1]) if bins else (0,0)
print(f"137-band floor = {inb:6.2f} dB | ref(138.5-140) = {ref:6.2f} dB | delta = {inb-ref:+5.2f} dB | peak {peak[0]/1e6:.3f}MHz @ {peak[1]:.1f}dB")
PY
