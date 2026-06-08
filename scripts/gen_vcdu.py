#!/usr/bin/env python3
# Reference vector for src/vcdu.rail — LRPT VCDU / M-PDU demux into per-APID
# Meteor MCU streams. This is the deframing rung that sits after RS decoding and feeds
# the image (DCT) rung: it turns a *sequence* of CCSDS VCDUs (each 6-byte VCDU primary
# header + 2-byte M-PDU header + packet zone) into reassembled CCSDS space packets,
# routed BY APID into the separate Meteor "MCU" (minor-channel) streams.
#
# This rung is harder than a single-frame VCDU parse in three ways that match a real
# Meteor-M2 pass:
#   1) MULTIPLE VCDUs in time order, sharing one VCID, with a monotonically increasing
#      24-bit VCDU counter.
#   2) Space packets SPAN VCDU boundaries: a packet started near the end of frame N
#      continues into frame N+1. The M-PDU first-header-pointer (FHP) in frame N+1 tells
#      the demux where the *next* packet header starts, so the tail of the spanning packet
#      (everything before FHP in frame N+1's zone) is appended to the in-progress packet.
#   3) Packets carry DIFFERENT APIDs (Meteor uses APID 64/65/66 = the red/green/blue/IR
#      imager minor channels). The demux must route each completed packet to its APID's
#      stream so the downstream image assembler gets one coherent MCU stream per channel.
#
# CCSDS VCDU primary header (6 bytes):
#   [2] version(2b)|SCID(8b)|VCID(6b)
#   [3] VCDU counter (24-bit)
#   [1] signaling field
# M-PDU header (2 bytes): spare(5b)|first-header-pointer(11b)  (0x7FF = idle / no start)
# CCSDS space packet header (6 bytes):
#   [2] version(3b)|type(1b)|sec-hdr(1b)|APID(11b)
#   [2] seq-flags(2b)|seq-count(14b)
#   [2] packet-data-length-1   (total packet = 6 + (len+1) bytes)
#
# We build 3 VCDUs. The packet zone is one contiguous byte stream of concatenated CCSDS
# packets, chopped into fixed-size frame zones; FHP is computed per frame from where the
# next packet header lands. Ground truth = the original packet list (APID/seq/len/payload)
# plus the per-APID grouping.
import numpy as np, json

def u16(b): return [(b >> 8) & 0xFF, b & 0xFF]
def u24(b): return [(b >> 16) & 0xFF, (b >> 8) & 0xFF, b & 0xFF]

VER = 0; SCID = 0; VCID = 5
ZONE = 40   # M-PDU packet-zone size per VCDU (bytes)

def make_packet(apid, seqc, payload):
    h  = u16((0 << 13) | (0 << 12) | (0 << 11) | (apid & 0x7FF))  # ver/type/sec=0, APID
    h += u16((3 << 14) | (seqc & 0x3FFF))                          # seq flags=3 (unsegmented)
    h += u16((len(payload) - 1) & 0xFFFF)                          # data length - 1
    return h + list(payload)

rng = np.random.default_rng(1729)

# --- Build the source packet list (APIDs 64/65/66 interleaved, varying lengths) ----------
specs = [
    (64, 200,  8),
    (65, 300, 20),
    (64, 201, 12),
    (66, 400,  6),
    (65, 301, 18),
    (64, 202, 22),
]
packets = []
stream = []          # the contiguous concatenated packet byte stream
pkt_starts = []      # absolute byte offset of each packet header in `stream`
for apid, seqc, plen in specs:
    pl = rng.integers(0, 256, plen).astype(int).tolist()
    pkt = make_packet(apid, seqc, pl)
    pkt_starts.append(len(stream))
    stream += pkt
    packets.append({"apid": apid, "seq": seqc, "len": plen, "payload": pl})

# --- Chop the stream into ZONE-sized M-PDU zones; FHP = first header start in each zone ----
nframes = (len(stream) + ZONE - 1) // ZONE
vcdu_bytes = []
frames_meta = []
for f in range(nframes):
    z0 = f * ZONE
    z1 = z0 + ZONE
    zone = stream[z0:z1]
    if len(zone) < ZONE:                      # pad the final frame's zone (idle fill 0)
        zone = zone + [0] * (ZONE - len(zone))
    # FHP = offset within this zone of the first packet header that starts at/after z0.
    fhp = 0x7FF                               # default: idle (no header starts here)
    for s in pkt_starts:
        if z0 <= s < z1:
            fhp = s - z0
            break
    hdr  = u16((VER << 14) | (SCID << 6) | VCID) + u24(f) + [0x00]
    mpdu = u16(fhp & 0x7FF)
    vcdu_bytes += hdr + mpdu + zone
    frames_meta.append({"vcnt": f, "fhp": fhp})

np.array(vcdu_bytes, np.uint8).tofile('/tmp/vcdu_in.s8')

# --- Ground truth: packets + per-APID grouping ------------------------------------------
by_apid = {}
for i, p in enumerate(packets):
    by_apid.setdefault(p["apid"], []).append(i)

truth = {
    "scid": SCID, "vcid": VCID, "nframes": nframes, "zone": ZONE,
    "frames": frames_meta,
    "packets": packets,
    "apids": sorted(by_apid.keys()),
    "by_apid": {str(a): by_apid[a] for a in by_apid},
}
json.dump(truth, open('/tmp/vcdu_truth.json', 'w'))
print(f"LRPT VCDU vector: SCID={SCID} VCID={VCID}, {nframes} VCDUs x {ZONE}-byte zones, "
      f"{len(packets)} packets across APIDs {sorted(by_apid.keys())}, "
      f"{len(vcdu_bytes)} total bytes")
for f in frames_meta:
    print(f"  frame vcnt={f['vcnt']} fhp={f['fhp']}")
