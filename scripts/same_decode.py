#!/usr/bin/env python3
"""SAME decoder — NOAA Weather Radio alert headers (the digital burst before/after a warning).
Pure Python, NO numpy, so it runs on the roof Pi. Input: FM-demod audio s16 @ 48 kHz (rtl_fm
off 162.550 MHz). Demods the AFSK (520.83 baud, mark 2083.3 / space 1562.5 Hz via Goertzel),
finds ZCZC at any bit alignment, reads the 3 header repeats, 2-of-3 votes per character (SAME's
only error correction), parses the fields, emits JSON. Usage: same_decode.py /tmp/same.s16
"""
import sys, math, json
from array import array

FS = 48000.0; BAUD = 520.833; SPB = FS / BAUD
MARK = 4 * BAUD; SPACE = 3 * BAUD
ORG = {"WXR": "National Weather Service", "EAS": "EAS Participant", "CIV": "Civil Authority", "PEP": "Primary Entry Point"}
EVT = {"RWT": "Required Weekly Test", "RMT": "Required Monthly Test", "SMW": "Special Marine Warning",
       "MWS": "Marine Weather Statement", "GLW": "Gale Warning", "SVR": "Severe Thunderstorm Warning",
       "TOR": "Tornado Warning", "FFW": "Flash Flood Warning", "SVS": "Severe Weather Statement",
       "EOM": "End of Message"}
MARINE = {"SMW", "MWS", "GLW"}      # codes the port-call product cares about

def goertzel(s, a, b, f):
    w = 2 * math.pi * f / FS; c = 2 * math.cos(w); s1 = s2 = 0.0
    for i in range(a, b):
        s0 = s[i] + c * s1 - s2; s2 = s1; s1 = s0
    return s1 * s1 + s2 * s2 - c * s1 * s2

def demod(s):
    bits = []; pos = 0.0
    while pos + SPB < len(s):
        a = int(pos); b = int(pos + SPB)
        bits.append(1 if goertzel(s, a, b, MARK) > goertzel(s, a, b, SPACE) else 0)
        pos += SPB
    return bits

def bytes_at(bits, p):
    out = []
    for i in range(p, len(bits) - 7, 8):
        b = 0
        for j in range(8):
            b |= bits[i + j] << j
        out.append(b)
    return out

def byte_phase(bits):
    # the 16-byte 0xAB preamble fixes byte alignment: pick the bit-phase with the most 0xAB bytes
    best_p, best_n = 0, -1
    for p in range(8):
        n = sum(1 for b in bytes_at(bits, p) if b == 0xAB)
        if n > best_n:
            best_p, best_n = p, n
    return best_p, best_n

def header_starts(B):
    # a header begins where a printable byte follows the 0xAB preamble run (anchors on the
    # preamble, NOT on ZCZC — so a corrupted sync word doesn't lose the whole repeat)
    starts = []
    for i in range(4, len(B) - 40):
        if 32 <= B[i] < 127 and sum(1 for k in range(i - 4, i) if B[k] == 0xAB) >= 3:
            if not starts or i - starts[-1] > 20:
                starts.append(i)
    return starts

def vote_bytes(reads):
    # SAME's only error correction: 2-of-3 per byte position across the repeated headers
    L = max((len(r) for r in reads), default=0); out = []
    for i in range(L):
        cnt = {}
        for r in reads:
            if i < len(r):
                cnt[r[i]] = cnt.get(r[i], 0) + 1
        out.append(max(cnt, key=lambda k: cnt[k]))
    return out

def parse(h):
    try:
        body = h.split("ZCZC-", 1)[1]
        left, right = body.split("+", 1)
        lf = left.split("-"); rf = right.split("-")
        org, evt = lf[0], lf[1]; fips = [f for f in lf[2:] if f]
        return {"originator": org, "org_name": ORG.get(org, org),
                "event": evt, "event_name": EVT.get(evt, evt),
                "marine": evt in MARINE, "areas_fips": fips,
                "valid_minutes": rf[0], "issued_utc": rf[1] if len(rf) > 1 else "",
                "station": rf[2].rstrip("-") if len(rf) > 2 else ""}
    except Exception as e:
        return {"parse_error": str(e), "raw": h}

def main():
    a = array("h"); a.frombytes(open(sys.argv[1], "rb").read())
    s = [float(x) for x in a]
    bits = demod(s)
    p, nab = byte_phase(bits)
    if nab < 8:                          # no 0xAB preamble run present -> no SAME burst here
        print(json.dumps({"same": "none"})); return
    B = bytes_at(bits, p)
    starts = header_starts(B)
    if not starts:
        print(json.dumps({"same": "none"})); return
    voted = vote_bytes([B[s0:s0 + 50] for s0 in starts])
    chars = []
    for b in voted:
        if 32 <= b < 127:
            chars.append(chr(b))
        else:
            break
    rec = parse("".join(chars)); rec["copies"] = len(starts)
    print(json.dumps(rec))

if __name__ == "__main__":
    main()
