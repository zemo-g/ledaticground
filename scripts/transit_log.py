#!/usr/bin/env python3
"""ledaticground transit log — per-vessel movement summary + canonical channel product.

PUBLIC-REPO VENDORED COPY (sanitized). The producer that runs live is
~/.ledatic/roofv2/transit_log.py (driven by refresh.sh every 5 min). This copy
carries the same logic so the public repo ships the producer; it contains NO
coordinates, NO node hostname, NO operator identity (sanitize precedent: commit
22ed4eb). All location facts come from the source ais.jsonl at runtime.

WHAT IT DOES
  Reads an AIS position feed (ais.jsonl, one JSON object per line as emitted by
  the decoder: {"type":1|2|3,"mmsi","lat","lon","sog","cog","ts"}), groups the
  type-1/2/3 Class-A position fixes by MMSI, time-orders each vessel's fixes, and
  emits a per-vessel movement summary.

OUTPUTS
  transits.txt     human-readable table (legacy, unchanged columns)
  transits.jsonl   one summary object per vessel (legacy schema, unchanged):
                     {mmsi, flag, name, fixes, net_km, bearing, speed_kn, last, status}
  vessels.jsonl    NEW canonical CHANNEL PRODUCT signed by vessel_attest.rail (VP-2):
                     the transits.jsonl summary PLUS a time-ordered track:
                     {... all summary fields ..., t0, t1,
                      track: [[unix_ts:int, lat:float, lon:float], ...]}  (len == fixes)
  vessels.meta.json NEW sidecar = {generated_unix, n_vessels, n_frames,
                     src_ais_sha256, channel:"detroit-river"}.
                     src_ais_sha256 is the FACT-CHAIN ANCHOR (shared contract #1/#2):
                     it binds this derived product to the exact raw-frame ais.jsonl
                     the AIS attestation signed, so the inference receipt's
                     derived_from has a real raw-frame digest to rest on.

  python3 transit_log.py [ais.jsonl]      # default: ./ais.jsonl next to this run
The float work (haversine, bearing) stays in Python for the demo; a Rail-native
deterministic-kinematics port is PAOS Layer-2 roadmap work (noted, NOT blocking).
"""
import hashlib
import json
import math
import os
import sys
import time
from datetime import datetime, timezone

EARTH_KM = 6371.0088
POS_TYPES = (1, 2, 3)            # Class-A position reports
MIN_TS = 1_000_000_000          # clean_ts: drop pre-NTP / unscrubbed clock garbage (< ~2001)


def haversine_km(a_lat, a_lon, b_lat, b_lon):
    p = math.pi / 180.0
    dlat = (b_lat - a_lat) * p
    dlon = (b_lon - a_lon) * p
    s = (math.sin(dlat / 2) ** 2
         + math.cos(a_lat * p) * math.cos(b_lat * p) * math.sin(dlon / 2) ** 2)
    return 2 * EARTH_KM * math.asin(min(1.0, math.sqrt(s)))


def bearing_deg(a_lat, a_lon, b_lat, b_lon):
    p = math.pi / 180.0
    y = math.sin((b_lon - a_lon) * p) * math.cos(b_lat * p)
    x = (math.cos(a_lat * p) * math.sin(b_lat * p)
         - math.sin(a_lat * p) * math.cos(b_lat * p) * math.cos((b_lon - a_lon) * p))
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def compass(brg):
    """Net-course -> direction label. N-bound (toward Lk St Clair) == upbound."""
    if brg is None:
        return "?"
    if brg >= 315 or brg < 45:
        return "N"
    if brg < 135:
        return "E"
    if brg < 225:
        return "S"
    return "W"


# MMSI MID (first 3 digits) -> ISO-ish flag. Minimal table covering Great-Lakes traffic.
MID_FLAG = {
    366: "US", 367: "US", 368: "US", 369: "US",
    316: "CA", 235: "GB", 232: "GB", 233: "GB", 234: "GB",
    477: "HK", 538: "MH", 311: "BS", 305: "AG", 215: "MT", 273: "RU",
}


def flag_of(mmsi):
    try:
        return MID_FLAG.get(int(str(mmsi)[:3]), "??")
    except Exception:
        return "??"


def clean_ts(v):
    """Return an int unix ts if scrubbed-valid, else None (clean_ts discipline). Accepts a unix
    int (sanitized feed) OR an ISO-8601 'Z' string (the live node emits e.g. 2026-06-17T22:00:10Z)
    -- so this public copy runs on the live nested feed, not only a pre-transformed one."""
    if isinstance(v, str):
        try:
            import datetime as _dt
            v = int(_dt.datetime.fromisoformat(v.replace("Z", "+00:00")).timestamp())
        except Exception:
            return None
    if not isinstance(v, int):
        return None
    if v < MIN_TS:
        return None
    return v


VOY_KEYS = ("dest", "draught_m", "shiptype", "length_m", "beam_m", "imo", "callsign")
NAVSTAT = {0: "underway", 1: "anchored", 2: "not-under-command", 3: "restricted-maneuver",
           4: "constrained-by-draught", 5: "moored", 6: "aground", 7: "fishing", 8: "sailing"}


def load_fixes(src):
    """Read ais.jsonl -> {mmsi: [(ts:int, lat:float, lon:float, sog, cog), ...]} for type 1/2/3,
    plus the joins surfaced from the enriched decoder: names (type 5), voyage (type-5
    dest/draught/ship-type/dims/IMO), and declared nav-status (type 1/2/3). Also return the raw
    file bytes' sha256 (the fact-chain anchor) and the total frame count. (ts/lat/lon/fields read
    from the message object whether flat or nested under 'msg'.)"""
    by = {}
    names = {}
    voyage = {}
    navstat = {}
    n_frames = 0
    raw = open(src, "rb").read()
    src_sha = hashlib.sha256(raw).hexdigest()
    for ln in raw.decode("utf-8", "replace").splitlines():
        ln = ln.strip()
        if not ln:
            continue
        try:
            o = json.loads(ln)
        except Exception:
            continue
        n_frames += 1
        ts = o.get("ts")
        m = o.get("msg") if isinstance(o.get("msg"), dict) else o   # tolerate flat OR nested
        typ = m.get("type")
        if typ == 5:
            if m.get("name"):
                names[m.get("mmsi")] = str(m["name"]).strip()
            v = {k: m[k] for k in VOY_KEYS if m.get(k) is not None}
            if v:
                voyage[m.get("mmsi")] = v                            # last type-5 per mmsi wins (most recent voyage)
            continue
        if typ not in POS_TYPES:
            continue
        if m.get("navstat") is not None:
            navstat[m.get("mmsi")] = m["navstat"]                    # declared state (latest wins)
        t = clean_ts(ts)
        lat = m.get("lat")
        lon = m.get("lon")
        if t is None or lat is None or lon is None:
            continue
        by.setdefault(m.get("mmsi"), []).append(
            (t, float(lat), float(lon), m.get("sog"), m.get("cog")))
    return by, names, voyage, navstat, src_sha, n_frames


def summarize(mmsi, fixes, name=None, voy=None, ns=None):
    """Build the per-vessel summary + the time-ordered track. fixes = list of tuples.
    name/voy/ns are the enriched joins (type-5 name + voyage, type-1/3 declared nav-status)."""
    # ts-sorted, deduped on identical (ts) keeping first; pts is the cleaned ordered list.
    pts = sorted(fixes, key=lambda r: r[0])
    if not pts:
        return None
    t0 = pts[0][0]
    t1 = pts[-1][0]
    # net displacement first->last (km) and net bearing.
    net_km = haversine_km(pts[0][1], pts[0][2], pts[-1][1], pts[-1][2])
    brg = bearing_deg(pts[0][1], pts[0][2], pts[-1][1], pts[-1][2]) if net_km > 0 else None
    # speed over ground: mean of reported sog if present, else derived from net path.
    sogs = [r[3] for r in pts if isinstance(r[3], (int, float))]
    if sogs:
        speed_kn = round(sum(sogs) / len(sogs), 1)
    else:
        dt_h = (t1 - t0) / 3600.0
        speed_kn = round((net_km / 1.852) / dt_h, 1) if dt_h > 0 else 0.0
    # status: moving vs holding. < 0.2 km net over the window == holding/moored.
    status = "transiting" if net_km >= 0.2 else "holding"
    # track: [unix_ts:int, lat:float, lon:float] per fix, ts non-decreasing.
    track = [[int(t), round(la, 5), round(lo, 5)] for (t, la, lo, _s, _c) in pts]
    summary = {
        "mmsi": mmsi,
        "flag": flag_of(mmsi),
        "name": name or "",              # joined from the type-5 static (enriched decoder)
        "fixes": len(pts),
        "net_km": round(net_km, 3),
        "bearing": round(brg, 1) if brg is not None else None,
        "speed_kn": speed_kn,
        "last": t1,
        "status": status,
    }
    if voy:
        summary.update(voy)              # dest, draught_m, shiptype, length_m, beam_m, imo, callsign (when present)
    if ns is not None:
        summary["navstatus"] = NAVSTAT.get(ns, "code-%d" % ns)   # the vessel's DECLARED state
    return summary, track, t0, t1


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), "ais.jsonl")
    outdir = os.path.dirname(os.path.abspath(src))

    by, names, voyage, navstat, src_sha, n_frames = load_fixes(src)

    summaries = []   # legacy summary rows (transits.jsonl)
    vessels = []     # canonical product rows (vessels.jsonl) = summary + track
    for mmsi in sorted(by.keys()):
        r = summarize(mmsi, by[mmsi], names.get(mmsi), voyage.get(mmsi), navstat.get(mmsi))
        if r is None:
            continue
        summary, track, t0, t1 = r
        summaries.append(summary)
        vrow = dict(summary)
        vrow["t0"] = t0
        vrow["t1"] = t1
        vrow["track"] = track
        vessels.append(vrow)

    # ---- transits.txt (human-readable, legacy columns) --------------------------------------
    txt = os.path.join(outdir, "transits.txt")
    with open(txt, "w") as f:
        f.write("MMSI       FLAG NAME                 FIXES  NET_KM  BRG  KN  STATUS\n")
        for s in summaries:
            f.write("%-10s %-4s %-20s %5d %7.3f %4s %4s  %s\n" % (
                s["mmsi"], s["flag"], (s["name"] or "")[:20], s["fixes"], s["net_km"],
                ("%.0f" % s["bearing"]) if s["bearing"] is not None else "-",
                ("%.1f" % s["speed_kn"]), s["status"]))

    # ---- transits.jsonl (legacy summary, one object per vessel) -----------------------------
    tj = os.path.join(outdir, "transits.jsonl")
    with open(tj, "w") as f:
        for s in summaries:
            f.write(json.dumps(s) + "\n")

    # ---- vessels.jsonl (NEW canonical channel product = summary + track) --------------------
    vj = os.path.join(outdir, "vessels.jsonl")
    with open(vj, "w") as f:
        for v in vessels:
            f.write(json.dumps(v) + "\n")

    # ---- vessels.meta.json (NEW sidecar; src_ais_sha256 = fact-chain anchor) ----------------
    meta = {
        "generated_unix": int(time.time()),
        "n_vessels": len(vessels),
        "n_frames": n_frames,
        "src_ais_sha256": src_sha,
        "channel": "detroit-river",
    }
    with open(os.path.join(outdir, "vessels.meta.json"), "w") as f:
        json.dump(meta, f, indent=2)

    print("transit_log: %d vessels (%d transiting), %d frames, src_ais_sha256=%s"
          % (len(vessels),
             sum(1 for v in vessels if v["status"] == "transiting"),
             n_frames, src_sha[:16]))
    print("  wrote %s, %s, %s, vessels.meta.json" % (
        os.path.relpath(txt), os.path.relpath(tj), os.path.relpath(vj)))


if __name__ == "__main__":
    main()
