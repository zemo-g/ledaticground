#!/usr/bin/env python3
"""gen_stringline.py — build the data for the L4 time-distance (Marey / string-line) view.

CONSUMES (VP-3, downstream of VP-1 + VP-2):
  vessels.jsonl                  the canonical signed CHANNEL PRODUCT (VP-1 schema):
                                 per-vessel {mmsi,flag,name,fixes,net_km,bearing,
                                 speed_kn,last,status,t0,t1,track:[[ts,lat,lon],...]}
  data/vessel_receipt.json       the INFERENCE receipt (VP-2): chain_hash + derived_from
                                 -> carried into the viz so the picture CARRIES its provenance
  /tmp/lg_pulse_id.txt           the live beacon pulse_id (RECEIPT_CONTRACT.md sec C staging)
  scripts/channel_geom.py        Detroit-river-as-single-track projection (shared contract #6)

EMITS:
  web/stringline.json (default)  {generated_unix, pulse_id, receipt_chain, derived_from,
                                  channel:{length_mi,waypoints}, window:{t_min,t_max},
                                  vessels:[{mmsi,name,flag,dir:up|down|hold,status,
                                            pts:[[ts,mile],...]}]}

PROVENANCE / HONESTY (logistics-over-attestation + honest-empty rules):
  - The viz CARRIES the receipt chain_hash, the beacon pulse_id, and the derived_from
    fact root in its trust strip. These are READ from disk, never fabricated.
  - If vessels.jsonl is missing/empty -> emit an explicit empty product (vessels:[],
    note:"no transiting vessels in window"), NEVER a fabricated line. The HTML renders
    a "no transits" state from this.
  - If the receipt / pulse files are absent we record the honest PENDING placeholders
    (PENDING_no_receipt / PENDING_beacon_unreachable), never 0, never wall-clock.

  python3 scripts/gen_stringline.py [vessels.jsonl] [out.json]
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import channel_geom as cg  # noqa: E402

# Cap the vessel count to keep the JSON small + the canvas legible (top-N by net_km).
MAX_VESSELS = 40
PULSE_ID_FILE = "/tmp/lg_pulse_id.txt"
RECEIPT_JSON = os.path.join(REPO, "data", "vessel_receipt.json")


def _read_text(path, default):
    try:
        with open(path) as f:
            v = f.read().strip()
        return v if v else default
    except OSError:
        return default


def _read_receipt_provenance():
    """(chain_hash, derived_from) from the VP-2 vessel receipt. Honest placeholders
    if the file is absent or unparseable -- the viz must never invent a chain_hash."""
    try:
        with open(RECEIPT_JSON) as f:
            rec = json.load(f)
        ch = rec.get("chain_hash") or "PENDING_no_receipt"
        df = rec.get("derived_from") or "PENDING_no_fact"
        return ch, df
    except (OSError, ValueError):
        return "PENDING_no_receipt", "PENDING_no_fact"


def _direction(v):
    """up | down | hold from the net bearing + status. Matches transit_log compass()
    (N == upbound). Holding/snapshot vessels -> hold (rendered as flat stubs only)."""
    if v.get("status") != "transiting":
        return "hold"
    brg = v.get("bearing")
    if brg is None:
        return "hold"
    # N-bound (315..360 or 0..45) == upbound; S-bound (135..225) == downbound.
    if brg >= 315 or brg < 45:
        return "up"
    if 135 <= brg < 225:
        return "down"
    # E/W net movement on this ~N-S channel: classify by net mile delta if a track exists.
    return "up"  # fall through; refined below using the projected mile delta


def _load_vessels(path):
    rows = []
    try:
        with open(path) as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    rows.append(json.loads(ln))
                except ValueError:
                    continue
    except OSError:
        return []
    return rows


def build(vessels_path, out_path):
    rows = _load_vessels(vessels_path)
    chain_hash, derived_from = _read_receipt_provenance()
    pulse_id = _read_text(PULSE_ID_FILE, "PENDING_beacon_unreachable")

    out = {
        "generated_unix": int(time.time()),
        "channel": "detroit-river",
        "pulse_id": pulse_id,
        "receipt_chain": chain_hash,
        "derived_from": derived_from,
        "channel_geom": cg.channel_summary(),
        "vessels": [],
    }

    if not rows:
        out["note"] = "no transiting vessels in window"
        out["window"] = {"t_min": None, "t_max": None}
        _write(out, out_path)
        return out

    # project each track fix -> [ts, mile]; classify direction by net mile delta.
    projected = []
    t_min = None
    t_max = None
    for v in rows:
        track = v.get("track") or []
        if len(track) < 2 and v.get("status") == "transiting":
            continue  # a transiting vessel needs >=2 fixes to draw a line
        pts = []
        for entry in track:
            if not isinstance(entry, (list, tuple)) or len(entry) < 3:
                continue
            ts, lat, lon = entry[0], entry[1], entry[2]
            try:
                m = round(cg.mile(float(lat), float(lon)), 3)
                ts = int(ts)
            except (TypeError, ValueError):
                continue
            pts.append([ts, m])
            t_min = ts if t_min is None else min(t_min, ts)
            t_max = ts if t_max is None else max(t_max, ts)
        if not pts:
            continue
        status = v.get("status", "")
        # holding vessels render as flat stubs only (design rule); skip empty/no-net moves
        if status != "transiting":
            # keep a short flat stub for context, but only if it has >=1 fix
            mile_delta = 0.0
        else:
            mile_delta = pts[-1][1] - pts[0][1]
        # direction from the projected mile delta (authoritative on a 1-D channel),
        # falling back to bearing-based for degenerate cases.
        if status != "transiting":
            direction = "hold"
        elif mile_delta > 0.1:
            direction = "up"
        elif mile_delta < -0.1:
            direction = "down"
        else:
            direction = _direction(v)
        name = v.get("name") or ""
        projected.append({
            "mmsi": v.get("mmsi"),
            "name": name,
            "flag": v.get("flag", "??"),
            "dir": direction,
            "status": status,
            "net_km": v.get("net_km", 0.0),
            "speed_kn": v.get("speed_kn"),
            "pts": pts,
        })

    # cap to top-N by net_km (busiest movers) for legibility
    projected.sort(key=lambda r: -(r.get("net_km") or 0.0))
    if len(projected) > MAX_VESSELS:
        projected = projected[:MAX_VESSELS]

    out["vessels"] = projected
    out["window"] = {"t_min": t_min, "t_max": t_max}
    out["n_vessels"] = len(projected)
    if not projected:
        out["note"] = "no transiting vessels in window"
    _write(out, out_path)
    return out


def _write(out, out_path):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(out, f, separators=(",", ":"))


def _inject_inline(out, html_path):
    """Inject the product JSON into web/stringline.html's #stringline-data island so
    the page renders self-contained from file:// (no server). Idempotent: replaces
    the single <script id="stringline-data" ...>...</script> body each run.

    This keeps the HTML a hand-authored static file (we never rewrite its structure,
    only the contents of the one data island), so it can ALSO be served live where
    the inline data is simply overridden by a fresh fetch('stringline.json')."""
    import re
    try:
        with open(html_path) as f:
            html = f.read()
    except OSError:
        return False
    payload = json.dumps(out, separators=(",", ":"))
    # escape </script> so the JSON can never close the script tag early
    payload = payload.replace("</", "<\\/")
    pat = re.compile(
        r'(<script id="stringline-data" type="application/json">).*?(</script>)',
        re.DOTALL)
    new_html, n = pat.subn(lambda m: m.group(1) + payload + m.group(2), html)
    if n != 1:
        return False
    with open(html_path, "w") as f:
        f.write(new_html)
    return True


def main():
    args = [a for a in sys.argv[1:] if a != "--inline"]
    do_inline = "--inline" in sys.argv[1:]
    vessels_path = args[0] if len(args) > 0 else os.path.join(
        os.path.expanduser("~/.ledatic/roofv2"), "vessels.jsonl")
    out_path = args[1] if len(args) > 1 else os.path.join(REPO, "web", "stringline.json")
    out = build(vessels_path, out_path)
    if do_inline:
        html_path = os.path.join(REPO, "web", "stringline.html")
        if _inject_inline(out, html_path):
            print("  inlined data island -> %s (renders self-contained from file://)"
                  % os.path.relpath(html_path))
        else:
            print("  WARN: could not inject inline data island into %s" % html_path)
    n = len(out["vessels"])
    print("gen_stringline: %d vessels -> %s" % (n, os.path.relpath(out_path)))
    print("  pulse_id=%s  receipt_chain=%s  derived_from=%s"
          % (out["pulse_id"], str(out["receipt_chain"])[:16], str(out["derived_from"])[:24]))
    if n:
        miles = [m for v in out["vessels"] for (_t, m) in v["pts"]]
        print("  mile range [%.2f, %.2f]  dirs=%s"
              % (min(miles), max(miles), {d: sum(1 for v in out["vessels"] if v["dir"] == d)
                                          for d in ("up", "down", "hold")}))
    else:
        print("  (honest empty: %s)" % out.get("note", ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
