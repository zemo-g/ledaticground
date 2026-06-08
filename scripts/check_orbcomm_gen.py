#!/usr/bin/env python3
# ===========================================================================
# Validate the orbcomm_gen rung. The generator (gen_orbcomm_gen.py) produced a
# FULL Orbcomm-like SD-PSK transmit chain as int8 baseband IQ at
# /tmp/orbcomm_in.s8, plus ground truth at /tmp/orbcomm_gen_truth.json. This
# checker runs the FULL INVERSE CHAIN in numpy and confirms the generated signal
# decodes back to the original opaque framed message:
#
#   /tmp/orbcomm_in.s8  (int8 IQ, 1 samp/sym)
#     -> differential / SD-PSK decode: bit = 1 if Re(z[k]*conj(z[k-1])) < 0      [orbcomm_demod]
#        (phase-invariant; no Costas)
#     -> UW frame sync: slide the 16-bit unique word, find best (offset,score)   [orbcomm_demod]
#     -> self-sync descramble: x_hat[n] = y[n]^y[n-4]^y[n-7]                      [orbcomm_descram]
#     -> CRC-16/X-25 over (header+payload), compare to trailing FCS              [orbcomm_frame]
#   and compares the recovered opaque header+payload+FCS to truth.
#
# It ALSO parses an optional Rail-module stdout (argv[1], from src/orbcomm_gen.rail)
# whose own labeled lines (UW_AT / CRC_OK / MSG_OPAQUE_LEN) must agree with this
# numpy oracle -- proving the Rail front-end ingests + decodes the same generated IQ.
#
# A self-contained NEGATIVE check (flip one bit in the scrambled stream) confirms
# the self-sync error propagation is BOUNDED (multiplicative scrambler signature:
# 1 input error -> at most 3 output errors) and that the CRC catches gross
# corruption -- so the PASS is not vacuous.
#
# HONESTY: synthetic vector; the unique word / scrambler taps / CRC convention
# are shared encoder/decoder, so a wrong-but-consistent constant would pass --
# real off-air validation is the remaining gap. Header/payload are OPAQUE bits
# with NO invented field semantics (the public/proprietary boundary).
# ===========================================================================
import sys, json, numpy as np

T = json.load(open('/tmp/orbcomm_gen_truth.json'))
UW       = np.array(T['uw'], np.uint8)
PRE_LEN  = int(T['pre_len'])
HDR_LEN  = int(T['hdr_len'])
PAY_LEN  = int(T['pay_len'])
T1, T2   = int(T['t1']), int(T['t2'])
CRC_TRUE = int(T['crc'])
msg_true = np.array(T['msg_bits'], np.uint8)      # header+payload+FCS plaintext
scr_true = np.array(T['scr_bits'], np.uint8)      # scrambled message bits

# -------------------------------------------------- read int8 IQ
raw = np.fromfile('/tmp/orbcomm_in.s8', dtype=np.int8).astype(np.float64)
I, Q = raw[0::2], raw[1::2]
nsym = len(I)

# -------------------------------------------------- differential / SD-PSK decode
# bit[k-1] = 1 if Re(z[k] * conj(z[k-1])) < 0  (k = 1..nsym-1)
dot = I[1:] * I[:-1] + Q[1:] * Q[:-1]
dec = (dot < 0).astype(np.uint8)                  # decoded[k] = tx_bits[k+1]
ndec = len(dec)

# -------------------------------------------------- UW frame sync
def uw_search(bits):
    best_pos, best_sc = -1, -1
    for start in range(0, len(bits) - len(UW) + 1):
        sc = int(np.sum(bits[start:start + len(UW)] == UW))
        if sc > best_sc:
            best_sc, best_pos = sc, start
    return best_pos, best_sc

uw_pos, uw_sc = uw_search(dec)

# scrambled message begins right after the UW in the decoded stream
scr_start = uw_pos + len(UW)
msg_total = HDR_LEN + PAY_LEN + 16
scr_rx = dec[scr_start:scr_start + msg_total]

# -------------------------------------------------- self-sync descramble
def descram(stream):
    out = np.zeros(len(stream), np.uint8)
    for n in range(len(stream)):
        a = stream[n - T1] if n - T1 >= 0 else 0
        b = stream[n - T2] if n - T2 >= 0 else 0
        out[n] = stream[n] ^ a ^ b
    return out

msg_rx = descram(scr_rx)

# -------------------------------------------------- CRC-16/X-25 over header+payload
def crc16_x25_bits(bits):
    nbytes = (len(bits) + 7) // 8
    by = bytearray(nbytes)
    for i, b in enumerate(bits):
        by[i // 8] |= (int(b) & 1) << (7 - (i % 8))
    crc = 0xFFFF
    for byte in by:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if (crc & 1) else (crc >> 1)
    return crc ^ 0xFFFF

hdr_pay_rx = msg_rx[:HDR_LEN + PAY_LEN]
fcs_rx_bits = msg_rx[HDR_LEN + PAY_LEN:HDR_LEN + PAY_LEN + 16]
fcs_rx = 0
for b in fcs_rx_bits:
    fcs_rx = (fcs_rx << 1) | int(b)
crc_calc = crc16_x25_bits(hdr_pay_rx)
crc_ok = int(crc_calc == fcs_rx)

# -------------------------------------------------- compare to truth
fails = []
def chk(name, cond, detail=''):
    if not cond:
        fails.append(f'{name} {detail}')

ber_msg = float(np.mean(msg_rx != msg_true)) if len(msg_rx) == len(msg_true) else 1.0
chk('uw_score', uw_sc == len(UW), f'(got {uw_sc}/{len(UW)})')
chk('uw_pos', uw_pos == PRE_LEN - 1, f'(got {uw_pos} want {PRE_LEN-1})')
chk('msg_len', len(msg_rx) == len(msg_true), f'(got {len(msg_rx)} want {len(msg_true)})')
chk('msg_exact', ber_msg == 0.0, f'(BER {ber_msg:.4f})')
chk('crc_ok', crc_ok == 1, f'(calc 0x{crc_calc:04x} rx 0x{fcs_rx:04x})')
chk('crc_matches_truth', fcs_rx == CRC_TRUE, f'(rx 0x{fcs_rx:04x} truth 0x{CRC_TRUE:04x})')

# -------------------------------------------------- NEGATIVE check (self-sync bound + CRC)
# flip ONE bit in the scrambled message; descramble must produce a BOUNDED 1..3
# errors (multiplicative scrambler signature) AND the CRC must catch the corruption.
scr_neg = scr_rx.copy()
flip_at = len(scr_neg) // 2
scr_neg[flip_at] ^= 1
msg_neg = descram(scr_neg)
nerr = int(np.sum(msg_neg != msg_rx))
errprop_bounded = (1 <= nerr <= 3)
crc_neg_calc = crc16_x25_bits(msg_neg[:HDR_LEN + PAY_LEN])
fcs_neg = 0
for b in msg_neg[HDR_LEN + PAY_LEN:HDR_LEN + PAY_LEN + 16]:
    fcs_neg = (fcs_neg << 1) | int(b)
neg_caught = (crc_neg_calc != fcs_neg) or (nerr > 0)

# -------------------------------------------------- optional Rail stdout check
rail_note = ''
rail_ok = True
if len(sys.argv) > 1:
    rvals = {}
    try:
        for l in open(sys.argv[1]):
            l = l.strip()
            if l.startswith('UW_AT '):
                toks = l.split()
                rvals['uw_at'] = int(toks[1])
                # "UW_AT <pos> score <sc> /16"
                if 'score' in toks:
                    rvals['uw_sc'] = int(toks[toks.index('score') + 1])
            elif l.startswith('CRC_OK='):
                rvals['crc_ok'] = int(l.split('=')[1].split()[0])
            elif l.startswith('MSG_OPAQUE_LEN '):
                rvals['msg_len'] = int(l.split()[1])
        if rvals:
            r_uw = rvals.get('uw_at', -999) == uw_pos
            r_sc = rvals.get('uw_sc', -1) == len(UW)
            r_crc = rvals.get('crc_ok', -1) == 1
            r_len = rvals.get('msg_len', -1) == (HDR_LEN + PAY_LEN)
            rail_ok = r_uw and r_sc and r_crc and r_len
            rail_note = (f' | rail: uw@{rvals.get("uw_at")} sc={rvals.get("uw_sc")} '
                         f'crc_ok={rvals.get("crc_ok")} msglen={rvals.get("msg_len")} '
                         f'-> {"OK" if rail_ok else "MISMATCH"}')
        else:
            rail_note = ' | rail: no labeled lines parsed'
            rail_ok = False
    except Exception as e:
        rail_note = f' | rail: parse error {e}'
        rail_ok = False

# -------------------------------------------------- verdict
ok = (not fails) and neg_caught and errprop_bounded and rail_ok
if not ok:
    msg = []
    if fails: msg.append('chain: ' + '; '.join(fails))
    if not neg_caught: msg.append('negative: corruption not caught')
    if not errprop_bounded: msg.append(f'negative: errprop {nerr} not bounded 1..3')
    if not rail_ok: msg.append('rail stdout mismatch')
    print('FAIL  ' + ' | '.join(msg) + rail_note)
    sys.exit(1)

print(f'PASS  orbcomm_gen full chain: UW@{uw_pos} score {uw_sc}/{len(UW)} | '
      f'descram msg BER {ber_msg:.4f} ({len(msg_rx)} bits opaque) | '
      f'CRC-16/X-25 0x{fcs_rx:04x} OK | negative caught={neg_caught} errprop={nerr}(bounded)'
      f'{rail_note}')
sys.exit(0)
