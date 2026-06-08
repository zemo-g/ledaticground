#!/usr/bin/env python3
# Validates src/orbcomm_frame.rail: PART A frame/header/integrity to the public-docs boundary,
# and PART B the honest IQ characterization. PASS requires:
#   A1: unique word found at offset 0 with full score (frame sync),
#   A2: CRC-16/X-25 over the framed region recomputes and matches the trailing FCS (CRC_OK=1),
#   A3: header reported as OPAQUE (no fabricated semantics) and payload length correct,
#   B1: measured carrier offset within +/-150 Hz of the true 600 Hz,
#   B2: measured burst length within +/-2 symbols-worth of samples of the true frame length,
#   B3: measured SNR within +/-3 dB of the true 14 dB,
#   B4: reported symbol rate == documented 4800 sym/s.
import sys, numpy as np

t = np.load('/tmp/orbcomm_frame_truth.npy')        # [uwlen, hdr, pay, crc, total]
c = np.load('/tmp/orbcomm_frame_char.npy')         # [FS, SYM_RATE, foff_hz, snr, SPS, GAP]
uwlen, HDR, PAY, CRC_TRUE, TOTAL = [int(x) for x in t]
FS, SYM_RATE, FOFF, SNR, SPS, GAP = c
SPS = int(SPS); GAP = int(GAP)
true_burst_len = TOTAL * SPS   # frame bits * samples/symbol

vals = {}
hdr_opaque = False; pay_len = None
for l in open(sys.argv[1]):
    l = l.strip()
    if l.startswith('SYNC '):
        for tok in l.split():
            if tok.startswith('uw_at='): vals['uw_at'] = int(tok.split('=')[1])
            if tok.startswith('score='): vals['score'] = int(tok.split('=')[1])
    elif l.startswith('HEADER_OPAQUE '):
        hdr_opaque = True; vals['hdr_bits'] = l.split(None,1)[1]
    elif l.startswith('PAYLOAD_OPAQUE_LEN '):
        pay_len = int(l.split()[1])
    elif l.startswith('CRC_OK='):
        vals['crc_ok'] = int(l.split('=')[1])
    elif l.startswith('CHAR burst_start='):
        for tok in l.split():
            if tok.startswith('burst_len='): vals['burst_len'] = int(tok.split('=')[1])
            if tok.startswith('gap='): vals['gap'] = int(tok.split('=')[1])
    elif l.startswith('CHAR snr_db='):
        vals['snr'] = float(l.split('=')[1].split()[0])
    elif l.startswith('CHAR carrier_offset_hz='):
        vals['foff'] = float(l.split('=')[1].split()[0])
    elif l.startswith('CHAR sym_rate_hz='):
        vals['symrate'] = float(l.split('=')[1].split()[0])

def need(k):
    if k not in vals: print(f'FAIL: missing {k}'); sys.exit(1)
    return vals[k]

A1 = (need('uw_at') == 0 and need('score') == 16)
A2 = (need('crc_ok') == 1)
A3 = hdr_opaque and (pay_len == PAY)
B1 = abs(need('foff') - FOFF) <= 150.0
B2 = abs(need('burst_len') - true_burst_len) <= 2*SPS
B3 = abs(need('snr') - SNR) <= 3.0
B4 = abs(need('symrate') - 4800.0) < 1.0

print(f"A1 sync uw@{vals['uw_at']} score {vals['score']}/16 -> {A1}")
print(f"A2 crc_ok={vals['crc_ok']} (true crc 0x{CRC_TRUE:04x}) -> {A2}")
print(f"A3 header opaque={hdr_opaque} pay_len={pay_len} (true {PAY}) -> {A3}")
print(f"B1 carrier_offset {vals['foff']:.1f}Hz (true {FOFF:.0f}) -> {B1}")
print(f"B2 burst_len {vals['burst_len']} samp (true {true_burst_len}) -> {B2}")
print(f"B3 snr {vals['snr']:.1f}dB (true {SNR:.0f}) -> {B3}")
print(f"B4 sym_rate {vals['symrate']:.0f} (documented 4800) -> {B4}")
ok = all([A1,A2,A3,B1,B2,B3,B4])
print('PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
