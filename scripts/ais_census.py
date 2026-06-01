#!/usr/bin/env python3
"""ledaticground AIS census — consolidate every AIS capture the node has seen into one
de-duplicated table of vessels / aids-to-navigation / base stations, with positions,
names, types, and sighting counts. The "dig into the data" view of the Detroit-River feed.

  ais_census.py <capture1> [capture2 ...]
    captures = RTL-SDR IQ (uint8 @162.0MHz, *.iq) OR FM-demod s16 ch-A @48k (*.s16)
Writes data/vessel_census.json. Decode is the same pure-Rail-validated algorithm as the
decoder (Python here for fast multi-file aggregation; ais_decode.rail is the canonical decode).
"""
import sys, json, os, numpy as np

SPS = 5
NAMES_T = {1:"Class-A position",2:"Class-A position",3:"Class-A position",4:"Base station",
           5:"Static/voyage",18:"Class-B position",19:"Class-B ext",21:"Aid-to-navigation",
           24:"Static data",27:"Long-range pos"}

def crc_res(bits):
    c=0xFFFF
    for b in bits: c^=int(b); c=(c>>1)^0x8408 if (c&1) else (c>>1)
    return c
def destuff(d,c0,c1):
    o=[];ones=0;i=c0
    while i<c1:
        if ones==5: ones=0;i+=1;continue
        b=d[i];o.append(b);ones=ones+1 if b==1 else 0;i+=1
    return o
def byterev(bits):
    o=[]
    for i in range(0,len(bits)//8*8,8): o.extend(bits[i:i+8][::-1])
    return o
def gb(p,a,n):
    v=0
    for i in range(n): v=(v<<1)|p[a+i]
    return v
def sx(v,n): return v-(1<<n) if v&(1<<(n-1)) else v
def name6(p,a,nch):
    r=""
    for k in range(nch):
        if a+6*k+6>len(p): break
        v=gb(p,a+6*k,6); r+=chr(v+64) if v<32 else chr(v)
    return r.replace('@',' ').strip()

def decode_disc(disc):
    m=disc.mean()
    for pol in (1,-1):
        for ph in range(SPS):
            ns=(len(disc)-ph)//SPS-1
            if ns<40: continue
            raw=[1 if pol*np.sum(disc[ph+k*SPS:ph+k*SPS+SPS]-m)>0 else 0 for k in range(ns)]
            d="".join('1' if raw[i]==raw[i-1] else '0' for i in range(1,len(raw)))
            fl=[i for i in range(len(d)-8) if d[i:i+8]=="01111110"]
            for a in range(len(fl)):
                for b in range(a+1,len(fl)):
                    if fl[b]-fl[a]<48: continue
                    o=destuff([int(c) for c in d],fl[a]+8,fl[b])
                    if len(o)>=48 and crc_res(o)==0xF0B8:
                        return byterev(o[:-16])
    return None

def parse(p):
    typ=gb(p,0,6); mmsi=gb(p,8,30); rec={"type":typ,"mmsi":mmsi}
    if typ in (1,2,3):
        rec["lat"]=round(sx(gb(p,89,27),27)/600000,5); rec["lon"]=round(sx(gb(p,61,28),28)/600000,5)
        rec["sog"]=gb(p,50,10)/10; rec["cog"]=gb(p,116,12)/10
    elif typ in (18,19):
        rec["lat"]=round(sx(gb(p,85,27),27)/600000,5); rec["lon"]=round(sx(gb(p,57,28),28)/600000,5)
        rec["sog"]=gb(p,46,10)/10; rec["cog"]=gb(p,112,12)/10
    elif typ==4:
        rec["lat"]=round(sx(gb(p,107,27),27)/600000,5); rec["lon"]=round(sx(gb(p,79,28),28)/600000,5)
    elif typ==21:
        rec["name"]=name6(p,43,20); rec["lat"]=round(sx(gb(p,192,27),27)/600000,5); rec["lon"]=round(sx(gb(p,164,28),28)/600000,5)
    elif typ==5:
        rec["name"]=name6(p,112,20)
    return rec

def bursts_iq(z, fs=240000.0):
    t=np.arange(len(z))/fs
    ntap=101; h=np.sinc((12000/(fs/2))*(np.arange(ntap)-ntap//2))*np.hamming(ntap); h/=h.sum()
    for shift in (25000.0,-25000.0):
        za=np.convolve(z*np.exp(2j*np.pi*shift*t),h,'same')[::5]; fsd=fs/5
        mag=np.abs(za); thr=np.median(mag)*3; hot=(mag>thr).astype(int); ed=np.diff(hot)
        st=np.where(ed==1)[0]; en=np.where(ed==-1)[0]
        if len(en) and len(st) and en[0]<st[0]: en=en[1:]
        for a,e in zip(st,en):
            if (e-a)/fsd<=0.006: continue
            lo=max(a-30,0); hi=min(e+30,len(za))
            yield np.concatenate([[0.0],np.angle(za[lo+1:hi]*np.conj(za[lo:hi-1]))])

def bursts_s16(s, fs=48000.0):
    B=int(0.005*fs); nb=len(s)//B
    diff=np.abs(np.diff(s)); rough=np.array([diff[i*B:(i+1)*B].mean() for i in range(nb)])
    thr=np.median(rough)*0.6; i=0
    while i<nb:
        if rough[i]<thr:
            j=i
            while j<nb and rough[j]<thr: j+=1
            lo=max(i*B-int(0.004*fs),0); hi=min(j*B+int(0.004*fs),len(s))
            yield s[lo:hi].astype(float); i=j
        else: i+=1

DATA = os.path.join(os.path.dirname(__file__), "..", "data")
LOG = os.path.join(DATA, "vessel_log.jsonl")     # append-only timeline (one line per msg)

def decode_file(f):
    """Decode one capture -> list of parsed AIS records (one per CRC-valid burst)."""
    if f.endswith(".iq"):
        b = np.fromfile(f, np.uint8).astype(np.float32) - 127.5
        gen = bursts_iq(b[0::2] + 1j * b[1::2])
    else:
        gen = bursts_s16(np.fromfile(f, dtype="<i2").astype(float))
    out = []
    for disc in gen:
        p = decode_disc(disc)
        if p:
            out.append(parse(p))
    return out

def fmt(r):
    loc = f"{r['lat']:.4f},{r['lon']:.4f}" if "lat" in r else "—"
    nm = f' "{r["name"]}"' if r.get("name") else ""
    return f"MMSI {r['mmsi']:>9}  type {r['type']:>2} {NAMES_T.get(r['type'],'?'):18s}{nm:14s} {loc:>20}"

# ---- mode: --ingest <capture> <unix_ts> : decode one capture, append to the timeline ----
if len(sys.argv) >= 2 and sys.argv[1] == "--ingest":
    cap, ts = sys.argv[2], int(sys.argv[3])
    recs = decode_file(cap)
    with open(LOG, "a") as fh:
        for r in recs:
            fh.write(json.dumps({**r, "ts": ts}) + "\n")
    mvs = sum(1 for r in recs if r["type"] in (1, 2, 3, 18, 19))   # moving-vessel reports
    print(f"ingest @ {ts}: {len(recs)} msgs ({mvs} moving-vessel), appended {os.path.relpath(LOG)}")
    sys.exit(0)

# ---- mode: --summary : build census + movement timeline from the log ----
if len(sys.argv) >= 2 and sys.argv[1] == "--summary":
    log = [json.loads(l) for l in open(LOG)] if os.path.exists(LOG) else []
    by = {}
    for r in log:
        by.setdefault(r["mmsi"], []).append(r)
    print(f"=== TIMELINE CENSUS — {len(by)} distinct sources, {len(log)} msgs ===")
    movers = []
    for mmsi, hist in sorted(by.items(), key=lambda kv: (kv[1][0]["type"], kv[0])):
        hist.sort(key=lambda r: r["ts"]); last = hist[-1]
        span = hist[-1]["ts"] - hist[0]["ts"]
        pos = [(r["lat"], r["lon"]) for r in hist if "lat" in r]
        moved = len(set(pos)) > 1
        if moved and last["type"] in (1, 2, 3, 18, 19): movers.append(mmsi)
        tag = "  ►MOVING" if moved and last["type"] in (1, 2, 3, 18, 19) else ""
        print(f"  {fmt(last)}  x{len(hist)} over {span//60}m{tag}")
    print(f"\n{len(movers)} moving vessels tracked: {movers}")
    json.dump([by[m][-1] for m in by], open(os.path.join(DATA, "vessel_census.json"), "w"), indent=2)
    sys.exit(0)

# ---- default mode: ad-hoc multi-file census ----
census = {}
for f in sys.argv[1:]:
    src = os.path.basename(f); recs = decode_file(f)
    for r in recs:
        k = r["mmsi"]
        if k not in census:
            census[k] = {**r, "sightings": 0, "sources": set()}
        census[k]["sightings"] += 1; census[k]["sources"].add(src)
        census[k].update({x: r[x] for x in r})
    print(f"  {src}: {len(recs)} CRC-valid messages")
for k in census: census[k]["sources"] = sorted(census[k]["sources"])
rows = sorted(census.values(), key=lambda r: (r["type"], r["mmsi"]))
print(f"\n=== VESSEL CENSUS — {len(rows)} distinct AIS sources ===")
for r in rows:
    print(f"  {fmt(r)}  x{r['sightings']}")
json.dump(rows, open(os.path.join(DATA, "vessel_census.json"), "w"), indent=2)
print(f"\nwrote data/vessel_census.json")
