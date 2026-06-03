#!/usr/bin/env python3
"""ledaticground ground-truth check: compare OUR decoded AIS feed against a live external
reference (aisstream.io) for the same coverage box. Answers "is it all there?" and — after
an antenna change — measures exactly how many vessels we newly recover.

Reference key: $AISSTREAM_KEY, else --key-file, else ~/.ledatic/lakes/aisstream_key.
The key is never printed. Reads our feed from data/vessel_log.jsonl.

  ais_groundtruth.py [--secs 150] [--key-file PATH]

Output: matched / ours-only (we're a unique RX) / reference-only (our gap, esp. vessels),
written to data/groundtruth_compare.json. Needs: pip install websockets.
"""
import sys, json, os, asyncio, time, argparse

BBOX = [[[0.0, 0.0], [0.0, 0.0]]]  # set your AIS coverage box (lat/lon SW + NE corners)
DATA = os.path.join(os.path.dirname(__file__), "..", "data")
VESSEL_TYPES = {1, 2, 3, 18, 19}

def load_key(path_arg):
    if os.environ.get("AISSTREAM_KEY"):
        return os.environ["AISSTREAM_KEY"].strip()
    path = path_arg or os.path.expanduser("~/.ledatic/lakes/aisstream_key")
    if not os.path.exists(path):
        sys.exit(f"no aisstream key ($AISSTREAM_KEY / --key-file / {path})")
    return open(path).read().strip()

async def pull_reference(key, secs):
    import websockets
    seen = {}
    async with websockets.connect("wss://stream.aisstream.io/v0/stream", ping_interval=None) as ws:
        await ws.send(json.dumps({"APIKey": key, "BoundingBoxes": BBOX}))
        t0 = time.time()
        while time.time() - t0 < secs:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=max(1, secs - (time.time() - t0)))
            except asyncio.TimeoutError:
                break
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            meta = msg.get("MetaData", {}) or {}
            mmsi = meta.get("MMSI")
            if mmsi is None:
                continue
            bk = next(iter(msg.get("Message", {}) or {}), "?")
            seen[mmsi] = {"mmsi": mmsi, "msg": bk, "name": (meta.get("ShipName") or "").strip(),
                          "lat": meta.get("latitude"), "lon": meta.get("longitude")}
    return seen

def is_vessel(rec):
    return rec.get("type") in VESSEL_TYPES or "PositionReport" in rec.get("msg", "") or "ClassB" in rec.get("msg", "")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--secs", type=int, default=150)
    ap.add_argument("--key-file", default=None)
    args = ap.parse_args()
    key = load_key(args.key_file)

    ours = {}
    logp = os.path.join(DATA, "vessel_log.jsonl")
    if os.path.exists(logp):
        for l in open(logp):
            r = json.loads(l); ours[r["mmsi"]] = r
    print(f"pulling {args.secs}s of aisstream reference for the Detroit-River box...", file=sys.stderr)
    ref = asyncio.run(pull_reference(key, args.secs))

    O, R = set(ours), set(ref)
    matched, ours_only, ref_only = O & R, O - R, R - O
    missed_vessels = [m for m in ref_only if is_vessel(ref[m])]
    out = {"ours": len(O), "reference": len(R), "matched": len(matched),
           "ours_only": sorted(ours_only), "reference_only": sorted(ref_only),
           "missed_vessels": [{"mmsi": m, **{k: ref[m][k] for k in ("name", "msg", "lat", "lon")}} for m in missed_vessels]}
    json.dump(out, open(os.path.join(DATA, "groundtruth_compare.json"), "w"), indent=2)

    print(f"OUR feed: {len(O)}   aisstream: {len(R)}   matched: {len(matched)}   "
          f"ours-only: {len(ours_only)}   we-missed: {len(ref_only)} ({len(missed_vessels)} vessels)")
    if missed_vessels:
        print("vessels in the river we did NOT decode (the sensitivity gap — should shrink with a better antenna):")
        for m in missed_vessels:
            r = ref[m]
            print(f"  {m}  {r['name'][:18]:18s} {r['msg']:26s} {r.get('lat')},{r.get('lon')}")
    print("wrote data/groundtruth_compare.json")

if __name__ == "__main__":
    main()
