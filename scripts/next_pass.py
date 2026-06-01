#!/usr/bin/env python3
# Mini-side: refresh now, run passes.rail, print the next decodable NOAA APT pass as
# machine-readable fields for orchestrate.sh. NOAA 15/19 only (analog APT we decode);
# require max elevation >= threshold. passes.rail prints the sat name and the pass
# details on separate lines, so carry the last-seen name forward.
import subprocess, re, time, sys
GD="/Users/ledaticempire/projects/ledaticground"; RN="/Users/ledaticempire/projects/rail/rail_native"
MIN_EL = int(sys.argv[sys.argv.index('--minel')+1]) if '--minel' in sys.argv else 25
FREQ = {"NOAA 15":137620000, "NOAA 19":137100000}
open(f"{GD}/data/now_unix.txt","w").write(str(int(time.time())))
subprocess.run([RN, f"{GD}/src/passes.rail"], cwd=GD, capture_output=True, timeout=120)  # -> /tmp/rail_out
out = subprocess.run(["/tmp/rail_out"], cwd=GD, capture_output=True, text=True, timeout=120).stdout
best=None; cur=None
for l in out.split('\n'):
    nm=re.search(r"(NOAA 1[59])", l)
    if nm: cur=nm.group(1)
    mn=re.search(r"in\s+(\d+)\s+min", l); el=re.search(r"max El\s+(\d+)", l); du=re.search(r"\)\s*\|\s*(\d+)\s+min", l)
    if not (cur and mn and el and du): continue
    mins,dur,elev=int(mn.group(1)),int(du.group(1)),int(el.group(1))
    if elev<MIN_EL: continue
    if best is None or mins<best[1]: best=(cur,mins,dur,elev)
if best is None: print("NONE"); sys.exit(0)
sat,mins,dur,elev=best
print(f'SAT="{sat}" MINS={mins} DUR={dur} ELEV={elev} FREQ={FREQ[sat]} AOS_EPOCH={int(time.time())+mins*60}')
