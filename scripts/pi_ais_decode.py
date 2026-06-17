#!/usr/bin/env python3
"""ledaticground Pi-side AIS decoder — runs ON the roof Pi (Zero 2 W), pure Python (NO numpy),
so only tiny JSON crosses the weak roof WiFi. Same validated algorithm as src/ais_decode.rail.

EXHAUSTIVE (2026-06-01): extracts EVERY CRC-valid frame in each burst region, not just the
first. In busy traffic a strong regular AtoN beacon used to win the "first CRC" race and the
vessel sharing the window was discarded — we were throwing away frames we'd already received.
Now we collect all distinct payloads (deduped by payload bits) → the vessels come out too.

Input: FM-demod ch-A s16 @48kHz. Emits one JSON object per distinct CRC-valid message.
Usage: pi_ais_decode.py /tmp/ais_mon.s16
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
    B = int(0.005 * FS)
    nb = len(s) // B
    rough = []
    for i in range(nb):
        b = i * B; acc = 0
        for j in range(b + 1, b + B):
            d = s[j] - s[j - 1]; acc += d if d >= 0 else -d
        rough.append(acc / (B - 1))
    sr = sorted(rough); med = sr[len(sr) // 2] if sr else 0
    thr = med * 0.6; i = 0
    while i < nb:
        if rough[i] < thr:
            j = i
            while j < nb and rough[j] < thr:
                j += 1
            yield max(i * B - int(0.004 * FS), 0), min(j * B + int(0.004 * FS), len(s))
            i = j
        else:
            i += 1

def crc_res(bits):
    c = 0xFFFF
    for b in bits:
        c ^= b; c = (c >> 1) ^ 0x8408 if (c & 1) else (c >> 1)
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

def gb_safe(p, a, n):
    """gb() that returns None if the field runs past the payload. ADDITIVE-field reads use this so
    a short frame can NEVER raise an IndexError where the original decode path did not -- the
    enrichment fields are simply omitted, the validated existing fields always emit."""
    return gb(p, a, n) if (a + n) <= len(p) else None

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

def frames_in_window(s, lo, hi):
    """EXHAUSTIVE: every distinct CRC-valid payload in this region (deduped by payload bits)."""
    w = s[lo:hi]; n = len(w)
    m = sum(w) / n if n else 0
    found = {}                                       # payload-tuple -> payload list
    for pol in (1, -1):
        for ph in range(SPS):
            ns = (n - ph) // SPS - 1
            if ns < 40:
                continue
            raw = []
            for k in range(ns):
                base = ph + k * SPS; acc = 0
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
                        payload = byterev(o[:-16])
                        found[tuple(payload)] = payload   # dedup identical payloads
    return list(found.values())

def parse(p):
    typ = gb(p, 0, 6); mmsi = gb(p, 8, 30); r = {"type": typ, "mmsi": mmsi}
    if typ in (1, 2, 3):
        r["lat"] = round(sx(gb(p, 89, 27), 27) / 600000, 5); r["lon"] = round(sx(gb(p, 61, 28), 28) / 600000, 5)
        r["sog"] = gb(p, 50, 10) / 10; r["cog"] = gb(p, 116, 12) / 10
        # ADDITIVE (within the 168-bit Type 1/2/3 payload; gb_safe-guarded + try-wrapped so a new
        # field can never disturb the validated lat/lon/sog/cog above). The vessel's DECLARED state.
        try:
            ns = gb_safe(p, 38, 4)
            if ns is not None and ns != 15: r["navstat"] = ns        # 0 underway,1 anchored,5 moored,6 aground,8 sailing
            rot = gb_safe(p, 42, 8)
            if rot is not None and rot != 128: r["rot"] = sx(rot, 8)  # 128 = not available
            hd = gb_safe(p, 128, 9)
            if hd is not None and hd != 511: r["hdg"] = hd            # true heading; 511 = not available
        except Exception:
            pass
    elif typ in (18, 19):
        r["lat"] = round(sx(gb(p, 85, 27), 27) / 600000, 5); r["lon"] = round(sx(gb(p, 57, 28), 28) / 600000, 5)
        r["sog"] = gb(p, 46, 10) / 10; r["cog"] = gb(p, 112, 12) / 10
        try:
            hd = gb_safe(p, 124, 9)
            if hd is not None and hd != 511: r["hdg"] = hd            # Class B true heading
        except Exception:
            pass
    elif typ == 4:
        r["lat"] = round(sx(gb(p, 107, 27), 27) / 600000, 5); r["lon"] = round(sx(gb(p, 79, 28), 28) / 600000, 5)
        # Type-4 base-station GPS time-of-day. ADDITIVE: a new key on type-4 ONLY; the attest rollup
        # hashes mmsi|type|lat|lon|ts, so the custody chain is byte-unaffected. Reads bits 61-77 —
        # strictly LOWER than the lat read above (107-133), so it cannot throw where the existing
        # code does not.
        # *** Observed live 2026-06-16 on station 3669778: the broadcast TIME-OF-DAY (hh:mm:ss) is
        # GPS-disciplined (matches wall clock to the second) but the DATE field is BOGUS (year 2006)
        # — a common AIS base-station quirk (date from station config, time from GPS). So we extract
        # ONLY the verified GPS time-of-day and NEVER assert the station's unreliable date. The
        # consumer pairs tod_utc with its own (coarse) date to discipline a clock; that is the honest
        # down-payment on rung E's time dimension. Sentinels (hour 24 / min,sec 60) -> omit. ***
        hh = gb(p, 61, 5); mm = gb(p, 66, 6); ss = gb(p, 72, 6)
        if hh < 24 and mm < 60 and ss < 60:
            r["tod_utc"] = "%02d:%02d:%02dZ" % (hh, mm, ss)
    elif typ == 21:
        r["name"] = name6(p, 43, 20); r["lat"] = round(sx(gb(p, 192, 27), 27) / 600000, 5); r["lon"] = round(sx(gb(p, 164, 28), 28) / 600000, 5)
        # ADDITIVE AtoN status (Type 21 >= 272 bits; guarded). off_position=1 means the aid is
        # reporting itself DRIFTED off its charted spot -- a live navigation-hazard signal, and AtoN
        # is ~75% of our traffic so this is the highest-volume latent field we own.
        try:
            at = gb_safe(p, 38, 5)
            if at is not None and at != 0: r["aton_type"] = at
            op = gb_safe(p, 259, 1)
            if op is not None: r["off_position"] = op
            va = gb_safe(p, 269, 1)
            if va is not None: r["virtual"] = va                     # 1 = virtual aid (no physical object on station)
        except Exception:
            pass
    elif typ == 5:
        r["name"] = name6(p, 112, 20)
        # ADDITIVE Type-5 voyage block (424-bit payload; each read gb_safe/name6-guarded + try-wrapped).
        # The single most commercially valuable AIS content for Great Lakes logistics, decoded-then-
        # discarded until now. DESTINATION = where the hull is bound; DRAUGHT = laden vs ballast (cargo
        # state); IMO = stable hull identity across MMSI re-flag (a durable PAOS corpus key). Self-
        # reported by the vessel -> attesting proves "broadcast + CRC-valid at our node", never truth.
        try:
            imo = gb_safe(p, 40, 30)
            if imo: r["imo"] = imo
            cs = name6(p, 70, 7)
            if cs: r["callsign"] = cs
            st = gb_safe(p, 232, 8)
            if st: r["shiptype"] = st
            bo = gb_safe(p, 240, 9); st_ = gb_safe(p, 249, 9); po = gb_safe(p, 258, 6); sb = gb_safe(p, 264, 6)
            if bo is not None and st_ is not None and po is not None and sb is not None and (bo + st_ + po + sb) > 0:
                r["length_m"] = bo + st_; r["beam_m"] = po + sb
            emo = gb_safe(p, 274, 4); eda = gb_safe(p, 278, 5); ehr = gb_safe(p, 283, 5); emi = gb_safe(p, 288, 6)
            if emo:
                r["eta"] = "%02d-%02dT%02d:%02dZ" % (emo, eda or 0, ehr or 0, emi or 0)
            dr = gb_safe(p, 294, 8)
            if dr: r["draught_m"] = dr / 10.0
            dest = name6(p, 302, 20)
            if dest: r["dest"] = dest
        except Exception:
            pass
    elif typ == 8:
        # ADDITIVE: emit ONLY the application id (DAC/FI) so we can SEE what our base stations
        # (MID-369/367 US-gov senders) broadcast. We do NOT decode the binary payload content yet --
        # so NO water-level/met claim is made; content stays honestly unknown until we read a real FI.
        try:
            dac = gb_safe(p, 40, 10); fi = gb_safe(p, 50, 6)
            if dac is not None: r["dac"] = dac
            if fi is not None: r["fi"] = fi
        except Exception:
            pass
    return r

def main():
    s = load_s16(sys.argv[1])
    seen = {}                                        # dedup across regions by payload tuple
    for lo, hi in roughness_bursts(s):
        for payload in frames_in_window(s, lo, hi):
            seen[tuple(payload)] = payload
    # emit one JSON per distinct (mmsi, message-type); keying by (mmsi,type) -- not mmsi alone --
    # so a vessel that sends BOTH a Type-5 static (name/dest/draught) and a Type-1 position in the
    # same cycle keeps BOTH (the old mmsi-only key dropped one, breaking the static<->track join).
    out = {}
    for payload in seen.values():
        r = parse(payload); out[(r["mmsi"], r["type"])] = r
    for r in out.values():
        print(json.dumps(r))

if __name__ == "__main__":
    main()
