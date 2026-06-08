#!/usr/bin/env python3
# Validate src/vcdu.rail: a multi-VCDU LRPT demux into per-APID Meteor MCU streams.
# Checks, against the independently-computed numpy ground truth:
#   1) every VCDU header (scid/vcid) + per-frame FHP,
#   2) every reassembled CCSDS packet (apid/seq/len/payload) in stream order
#      (this is the cross-VCDU-boundary reassembly result),
#   3) the per-APID MCU demux (which APIDs exist, count + seq list per APID).
import sys, json

gt = json.load(open('/tmp/vcdu_truth.json'))
vcdus = []      # (scid, vcid, fhp) per frame, in print order
pkts = []       # {apid,seq,len,payload} in stream order
mcus = {}       # apid -> {"count":int, "seqs":[int,...]}
npkt = napid = None

for l in open(sys.argv[1]):
    if l.startswith('VCDU '):
        d = dict(tok.split('=') for tok in l.split()[1:])
        vcdus.append((int(d['scid']), int(d['vcid']), int(d['fhp'])))
    elif l.startswith('PKT '):
        head, pay = l.split(' PAY ')
        d = dict(tok.split('=') for tok in head.split()[1:])
        payload = [int(x) for x in pay.split()]
        pkts.append({"apid": int(d['apid']), "seq": int(d['seq']),
                     "len": int(d['len']), "payload": payload})
    elif l.startswith('MCU '):
        head, seqpart = l.split(' seqs=')
        d = dict(tok.split('=') for tok in head.split()[1:])
        seqs = [int(x) for x in seqpart.split()]
        mcus[int(d['apid'])] = {"count": int(d['count']), "seqs": seqs}
    elif l.startswith('NPKT '):
        npkt = int(l.split()[1])
    elif l.startswith('NAPID '):
        napid = int(l.split()[1])

ok = True

# 1) VCDU headers + FHP
hdr_ok = len(vcdus) == gt['nframes']
print(f"VCDU frame count {len(vcdus)} (want {gt['nframes']}) = {hdr_ok}")
ok = ok and hdr_ok
for i, fm in enumerate(gt['frames']):
    if i < len(vcdus):
        scid, vcid, fhp = vcdus[i]
        m = (scid == gt['scid'] and vcid == gt['vcid'] and fhp == fm['fhp'])
        print(f"  frame{i} scid/vcid/fhp = {scid}/{vcid}/{fhp} "
              f"(want {gt['scid']}/{gt['vcid']}/{fm['fhp']}) = {m}")
        ok = ok and m
    else:
        print(f"  frame{i} MISSING")
        ok = False

# 2) reassembled packets (cross-boundary) in stream order
pkt_ok = len(pkts) == len(gt['packets'])
print(f"packet count {len(pkts)} (want {len(gt['packets'])}) = {pkt_ok}")
ok = ok and pkt_ok
for i, g in enumerate(gt['packets']):
    if i < len(pkts):
        p = pkts[i]
        m = (p['apid'] == g['apid'] and p['seq'] == g['seq']
             and p['len'] == g['len'] and p['payload'] == g['payload'])
        print(f"  pkt{i} apid/seq/len/payload (apid={p['apid']} seq={p['seq']} len={p['len']}) = {m}")
        ok = ok and m
    else:
        print(f"  pkt{i} MISSING")
        ok = False

# NPKT line
npkt_ok = (npkt == len(gt['packets']))
print(f"NPKT {npkt} (want {len(gt['packets'])}) = {npkt_ok}")
ok = ok and npkt_ok

# 3) per-APID MCU demux
gt_apids = set(gt['apids'])
got_apids = set(mcus.keys())
apid_set_ok = (gt_apids == got_apids)
print(f"APID set {sorted(got_apids)} (want {sorted(gt_apids)}) = {apid_set_ok}")
ok = ok and apid_set_ok
napid_ok = (napid == len(gt_apids))
print(f"NAPID {napid} (want {len(gt_apids)}) = {napid_ok}")
ok = ok and napid_ok
for a in sorted(gt_apids):
    idxs = gt['by_apid'][str(a)]
    want_seqs = [gt['packets'][i]['seq'] for i in idxs]
    if a in mcus:
        got = mcus[a]
        m = (got['count'] == len(idxs) and got['seqs'] == want_seqs)
        print(f"  MCU apid={a} count={got['count']} seqs={got['seqs']} "
              f"(want count={len(idxs)} seqs={want_seqs}) = {m}")
        ok = ok and m
    else:
        print(f"  MCU apid={a} MISSING")
        ok = False

print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
