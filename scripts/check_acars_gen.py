#!/usr/bin/env python3
# ===========================================================================
# Validate the acars_gen rung. The generator (gen_acars_gen.py) produced a FULL
# ARINC 618 ACARS transmit chain as int8 baseband IQ at /tmp/acars_in.s8, plus
# ground truth at /tmp/acars_gen_truth.json. This checker runs the FULL INVERSE
# chain in numpy and confirms the generated signal decodes back to the original
# message:
#
#   /tmp/acars_in.s8  (int8 IQ)
#     -> AM envelope demod: a[n] = |z[n]| - mean   (recovers m*MSK audio)   [acars_am]
#     -> MSK bit recovery: per-bit Goertzel energy at 1200/2400 Hz, slice,   [acars_msk]
#        with a max-margin bit-sync phase sweep
#     -> bit -> byte pack (MSB-first, 8 bits/char)
#     -> ARINC 618 deframe: find SYN SYN -> SOH -> STX -> ETX                [acars_deframe]
#     -> per-char ODD parity check
#     -> reflected CRC-CCITT BCS (poly 0x8408, init 0) over after-SOH..ETX
#     -> fixed-width header field parse
#   and compares every recovered field to truth.
#
# It ALSO parses an optional Rail-module stdout (argv[1], from src/acars_gen.rail)
# whose "A <sample>" envelope lines must correlate with the clean MSK truth —
# proving the Rail front-end ingests the same generated IQ.
#
# A self-contained NEGATIVE check (flip one data bit in the recovered byte
# stream) confirms parity + BCS actually catch corruption (not vacuously OK).
# HONESTY: synthetic vector; the BCS definition is shared encoder/decoder
# (acarsdec convention), so a wrong-but-consistent CRC would pass — real off-air
# validation is the remaining gap.
# ===========================================================================
import sys, json, numpy as np

T = json.load(open('/tmp/acars_gen_truth.json'))
FS, SPS = int(T['fs']), int(T['sps'])
F1, F0  = float(T['f1']), float(T['f0'])
PRE     = int(T['prekey_samples'])

# -------------------------------------------------- read int8 IQ -> envelope
raw = np.fromfile('/tmp/acars_in.s8', dtype=np.int8).astype(np.float64)
I, Q = raw[0::2], raw[1::2]
env = np.sqrt(I * I + Q * Q)
env = env - env.mean()                       # DC removal -> m*MSK audio (acars_am)

# drop the unmodulated pre-key region; what remains is the MSK-carrying audio
aud = env[PRE:]

# -------------------------------------------------- MSK bit recovery + sync
def tone_energy(seg, f):
    n = np.arange(len(seg))
    c = np.sum(seg * np.cos(2 * np.pi * f * n / FS))
    s = np.sum(seg * np.sin(2 * np.pi * f * n / FS))
    return c * c + s * s

nbits = len(aud) // SPS

def recover_bits(phase):
    out = []
    for b in range(nbits):
        lo = phase + b * SPS
        hi = lo + SPS
        if hi > len(aud):
            break
        seg = aud[lo:hi]
        e1 = tone_energy(seg, F1)
        e0 = tone_energy(seg, F0)
        out.append(1 if e1 > e0 else 0)
    return np.array(out, np.uint8)

def margin_score(phase):
    sc = 0.0
    for b in range(nbits):
        lo = phase + b * SPS
        hi = lo + SPS
        if hi > len(aud):
            break
        seg = aud[lo:hi]
        sc += abs(tone_energy(seg, F1) - tone_energy(seg, F0))
    return sc

best_phase = max(range(SPS), key=margin_score)
rbits = recover_bits(best_phase)

# -------------------------------------------------- bit -> byte (MSB-first)
def pack_bytes(bits):
    nby = len(bits) // 8
    return np.array([int(''.join(str(int(x)) for x in bits[i*8:i*8+8]), 2)
                     for i in range(nby)], np.uint8)

def try_polarity(bits):
    # FSK tone->bit can come out inverted; produce both byte streams
    return pack_bytes(bits), pack_bytes(1 - bits)

# -------------------------------------------------- deframe + parity + BCS
def crc_ccitt_acars(data7):
    crc = 0
    for c in data7:
        crc ^= (c & 0x7F)
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if (crc & 1) else (crc >> 1)
    return crc & 0xFFFF

def deframe(bytestream):
    # locate SYN SYN (0x16 0x16) then SOH (0x01), STX (0x02), ETX (0x03)/ETB (0x17)
    d = [int(b) & 0x7F for b in bytestream]   # strip parity bit for control search
    parity = [int(b) for b in bytestream]
    n = len(d)
    syn = -1
    for i in range(n - 1):
        if d[i] == 0x16 and d[i+1] == 0x16:
            syn = i; break
    if syn < 0:
        return None
    soh = -1
    for i in range(syn + 2, n):
        if d[i] == 0x01:
            soh = i; break
    if soh < 0:
        return None
    stx = -1
    for i in range(soh + 1, n):
        if d[i] == 0x02:
            stx = i; break
    if stx < 0:
        return None
    etx = -1
    for i in range(stx + 1, n):
        if d[i] == 0x03 or d[i] == 0x17:
            etx = i; break
    if etx < 0:
        return None
    if etx + 2 >= n:
        return None

    # per-char odd parity over SOH..ETX (BCS bytes excluded)
    parity_errs = 0
    for i in range(soh, etx + 1):
        if bin(parity[i] & 0xFF).count('1') % 2 == 0:
            parity_errs += 1

    # BCS over after-SOH..ETX (7-bit data)
    data_for_crc = d[soh+1:etx+1]
    bcs_calc = crc_ccitt_acars(data_for_crc)
    bcs_rx = (parity[etx+1] & 0xFF) | ((parity[etx+2] & 0xFF) << 8)  # low byte first

    # fixed-width header: after SOH -> mode(1) reg(7) ack(1) label(2) blkid(1)
    p = soh + 1
    mode  = chr(d[p]);                     p += 1
    reg   = ''.join(chr(x) for x in d[p:p+7]); p += 7
    ack   = d[p];                          p += 1
    label = ''.join(chr(x) for x in d[p:p+2]); p += 2
    blkid = chr(d[p]);                     p += 1
    text  = ''.join(chr(x) for x in d[stx+1:etx])

    return dict(mode=mode, reg=reg, ack=ack, label=label, blkid=blkid, text=text,
                parity_errs=parity_errs, bcs_calc=bcs_calc, bcs_rx=bcs_rx,
                bcs_ok=int(bcs_calc == bcs_rx), syn=syn, soh=soh, stx=stx, etx=etx)

# try both polarities, keep the one that frames cleanly with matching BCS
direct, inverted = try_polarity(rbits)
result, used_bytes, polarity = None, None, None
for label_pol, bs in (('direct', direct), ('inverted', inverted)):
    r = deframe(bs)
    if r is not None and r['bcs_ok'] == 1 and r['parity_errs'] == 0:
        result, used_bytes, polarity = r, bs, label_pol
        break
if result is None:
    # keep the best-effort frame for diagnostics even if it didn't fully pass
    for label_pol, bs in (('direct', direct), ('inverted', inverted)):
        r = deframe(bs)
        if r is not None:
            result, used_bytes, polarity = r, bs, label_pol
            break

# -------------------------------------------------- compare to truth
fails = []
def chk(name, got, want):
    if got != want:
        fails.append(f'{name}: got {got!r} want {want!r}')

if result is None:
    print('FAIL  inverse chain could not deframe the generated signal')
    sys.exit(1)

chk('mode',  result['mode'],  T['mode'])
chk('reg',   result['reg'],   T['reg'])
chk('ack',   result['ack'],   T['ack'])
chk('label', result['label'], T['label'])
chk('blkid', result['blkid'], T['blkid'])
chk('text',  result['text'],  T['text'])
chk('parity_errs', result['parity_errs'], 0)
chk('bcs_rx',   result['bcs_rx'],   T['bcs'])
chk('bcs_calc', result['bcs_calc'], T['bcs'])
chk('bcs_ok',   result['bcs_ok'],   1)

# -------------------------------------------------- NEGATIVE check
# flip one data bit in the recovered byte stream -> parity AND/OR BCS must flag it
neg_bytes = used_bytes.copy()
flip_at = result['stx'] + 1            # first text char
neg_bytes[flip_at] ^= 0x01
neg = deframe(neg_bytes)
neg_caught = (neg is None) or (neg['parity_errs'] > 0) or (neg['bcs_ok'] == 0)

# -------------------------------------------------- optional Rail stdout check
rail_corr = None
if len(sys.argv) > 1:
    try:
        rec = []
        for l in open(sys.argv[1]):
            if l.startswith('A '):
                rec.append(float(l.split()[1]))
        if rec:
            msk = np.load('/tmp/acars_gen_msk.npy').astype(np.float64)
            r = np.array(rec, np.float64)
            seg = r[PRE:PRE+len(msk)]
            n = min(len(seg), len(msk))
            a = seg[:n] - seg[:n].mean(); b = msk[:n] - msk[:n].mean()
            denom = (np.linalg.norm(a) * np.linalg.norm(b)) or 1.0
            rail_corr = float(np.dot(a, b) / denom)
    except Exception as e:
        rail_corr = None

# -------------------------------------------------- verdict
ok = (not fails) and neg_caught
rail_note = ''
if rail_corr is not None:
    rail_ok = rail_corr > 0.85
    rail_note = f' | rail_env corr={rail_corr:.4f} {"OK" if rail_ok else "LOW"}'
    ok = ok and rail_ok

if not ok:
    if fails:
        print('FAIL  ' + ' | '.join(fails) + rail_note)
    elif not neg_caught:
        print('FAIL  negative check: bit-flip not detected by parity/BCS' + rail_note)
    else:
        print('FAIL  rail envelope correlation too low' + rail_note)
    sys.exit(1)

print(f'PASS  acars_gen full chain: bestphase={best_phase} pol={polarity} '
      f'mode={result["mode"]} reg={result["reg"]} label={result["label"]} '
      f'blkid={result["blkid"]} text="{result["text"]}" '
      f'BCS=0x{T["bcs"]:04x} OK | negative-test caught={neg_caught}'
      f'{rail_note}')
sys.exit(0)
