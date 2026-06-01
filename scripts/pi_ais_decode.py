#!/usr/bin/env python3
"""ledaticground Pi-side AIS decoder — runs ON the roof Pi (Zero 2 W) so only tiny JSON
crosses the weak roof WiFi, never megabytes of samples. Pure Python, NO numpy (don't
assume it's installed). Same validated algorithm as src/ais_decode.rail / ais_census.py:
roughness burst-detect -> per-symbol integrate+slice (sps=5) over polarity/phase +
multi-flag-pair search -> NRZI -> HDLC deframe -> CRC-16/X-25 -> byte-reverse -> AIS parse.

Input: FM-demod ch-A s16 @48kHz (rtl_fm output). Emits one JSON object per CRC-valid
message to stdout. Usage: pi_ais_decode.py /tmp/ais_mon.s16
"""
import sys, json
from array import array

SPS = 5
FS = 48000.0

def load_s16(path):
    a = array('h')
    with open(path, 'rb') as f:
        a.frombytes(f.read())
    return a

def roughness_bursts(s):
    """Yield (lo, hi) sample windows of low-roughness (signal) regions."""
    B = int(0.005 * FS)            # 5 ms blocks
    nb = len(s) // B
    rough = []
    for i in range(nb):
        b = i * B
        acc = 0
        for j in range(b + 1, b + B):
            d = s[j] - s[j - 1]
            acc += d if d >= 0 else -d
        rough.append(acc / (B - 1))
    sr = sorted(rough)
    med = sr[len(sr) // 2] if sr else 0
    thr = med * 0.6
    i = 0
    while i < nb:
        if rough[i] < thr:
            j = i
            while j < nb and rough[j] < thr:
                j += 1
            lo = max(i * B - int(0.004 * FS), 0)
            hi = min(j * B + int(0.004 * FS), len(s))
            yield lo, hi
            i = j
        else:
            i += 1

def crc_res(bits):
    c = 0xFFFF
    for b in bits:
        c ^= b
        c = (c >> 1) ^ 0x8408 if (c & 1) else (c >> 1)
    return c

def destuff(d, c0, c1):
    out = []; ones = 0; i = c0
    while i < c1:
        if ones == 5:
            ones = 0; i += 1; continue
        b = d[i]; out.append(b); ones = ones + 1 if b == 1 else 0; i += 1
    return out

def byterev(bits):
    o = []
    for i in range(0, len(bits) // 8 * 8, 8):
        o.extend(bits[i:i + 8][::-1])
    return o

def gb(p, a, n):
    v = 0
    for i in range(n):
        v = (v << 1) | p[a + i]
    return v

def sx(v, n):
    return v - (1 << n) if v & (1 << (n - 1)) else v

def name6(p, a, nch):
    r = ""
    for k in range(nch):
        if a + 6 * k + 6 > len(p):
            break
        v = gb(p, a + 6 * k, 6)
        r += chr(v + 64) if v < 32 else chr(v)
    return r.replace('@', ' ').strip()

def decode_window(s, lo, hi):
    w = s[lo:hi]
    n = len(w)
    m = sum(w) / n if n else 0
    for pol in (1, -1):
        for ph in range(SPS):
            ns = (n - ph) // SPS - 1
            if ns < 40:
                continue
            raw = []
            for k in range(ns):
                base = ph + k * SPS
                acc = 0
                for j in range(SPS):
                    acc += w[base + j] - m
                raw.append(1 if pol * acc > 0 else 0)
            d = [1 if raw[i] == raw[i - 1] else 0 for i in range(1, len(raw))]
            ds = "".join(map(str, d))
            fl = [i for i in range(len(ds) - 8) if ds[i:i + 8] == "01111110"]
            for a in range(len(fl)):
                for b in range(a + 1, len(fl)):
                    if fl[b] - fl[a] < 48:
                        continue
                    o = destuff(d, fl[a] + 8, fl[b])
                    if len(o) >= 48 and crc_res(o) == 0xF0B8:
                        return byterev(o[:-16])
    return None

def parse(p):
    typ = gb(p, 0, 6); mmsi = gb(p, 8, 30); r = {"type": typ, "mmsi": mmsi}
    if typ in (1, 2, 3):
        r["lat"] = round(sx(gb(p, 89, 27), 27) / 600000, 5); r["lon"] = round(sx(gb(p, 61, 28), 28) / 600000, 5)
        r["sog"] = gb(p, 50, 10) / 10; r["cog"] = gb(p, 116, 12) / 10
    elif typ in (18, 19):
        r["lat"] = round(sx(gb(p, 85, 27), 27) / 600000, 5); r["lon"] = round(sx(gb(p, 57, 28), 28) / 600000, 5)
        r["sog"] = gb(p, 46, 10) / 10; r["cog"] = gb(p, 112, 12) / 10
    elif typ == 4:
        r["lat"] = round(sx(gb(p, 107, 27), 27) / 600000, 5); r["lon"] = round(sx(gb(p, 79, 28), 28) / 600000, 5)
    elif typ == 21:
        r["name"] = name6(p, 43, 20); r["lat"] = round(sx(gb(p, 192, 27), 27) / 600000, 5); r["lon"] = round(sx(gb(p, 164, 28), 28) / 600000, 5)
    elif typ == 5:
        r["name"] = name6(p, 112, 20)
    return r

def main():
    s = load_s16(sys.argv[1])
    seen = set()
    for lo, hi in roughness_bursts(s):
        p = decode_window(s, lo, hi)
        if p:
            r = parse(p)
            key = (r["type"], r["mmsi"])
            if key in seen:
                continue
            seen.add(key)
            print(json.dumps(r))

if __name__ == "__main__":
    main()
