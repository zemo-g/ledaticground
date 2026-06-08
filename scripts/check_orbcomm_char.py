#!/usr/bin/env python3
# Validates src/orbcomm_char.rail -- the Orbcomm HONEST CHARACTERIZATION rung.
#
# This rung's CONTRACT is twofold:
#   (1) it MEASURES physical observables from the IQ correctly, and
#   (2) it does NOT fabricate payload content (the honesty boundary).
#
# PASS requires:
#   C1: carrier_present == 1               (a carrier IS present in the burst)
#   C2: carrier offset within +/-150 Hz of the true offset
#   C3: measured burst length within +/-2 symbols-worth of samples of the true length
#   C4: measured SNR within +/-3 dB of the true SNR
#   C5: measured symbol rate == documented 4800 sym/s (genuinely measured via autocorr)
#   C6: HONESTY -- NO fabricated payload is emitted. The module prints an explicit
#       "PAYLOAD=NONE" line and emits NO bit/symbol/message-field decode lines (no SYM/BITS/
#       PAYLOAD_OPAQUE/DECODE_OK/MMSI/etc). A characterizer that invented payload would FAIL.
import sys, numpy as np

t = np.load('/tmp/orbcomm_char_truth.npy')   # [FS, SYM_RATE, FOFF, SNR, SPS, bstart, blen, GAP, NSYM]
FS, SYM_RATE, FOFF, SNR, SPS, BSTART, BLEN, GAP, NSYM = [float(x) for x in t]
SPS = int(SPS)
true_burst_len = int(BLEN)

vals = {}
payload_none = False
# any of these line prefixes would indicate FABRICATED payload content -- must NOT appear
FORBIDDEN = ('SYM ', 'BITS ', 'PAYLOAD_OPAQUE', 'PAYLOAD_BITS', 'DECODE_OK', 'MMSI',
             'MESSAGE', 'MSG ', 'HEADER_OPAQUE', 'TYPE=', 'LAT=', 'LON=')
forbidden_hit = None

for l in open(sys.argv[1]):
    s = l.strip()
    if s.startswith('CHAR carrier_present='):
        vals['carrier_present'] = int(s.split('=')[1].split()[0])
    elif s.startswith('CHAR burst_start='):
        for tok in s.split():
            if tok.startswith('burst_len='): vals['burst_len'] = int(tok.split('=')[1])
            if tok.startswith('gap='):       vals['gap'] = int(tok.split('=')[1])
    elif s.startswith('CHAR snr_db='):
        vals['snr'] = float(s.split('=')[1].split()[0])
    elif s.startswith('CHAR carrier_offset_hz='):
        vals['foff'] = float(s.split('=')[1].split()[0])
    elif s.startswith('CHAR sps='):
        for tok in s.split():
            if tok.startswith('sym_rate_hz='): vals['symrate'] = float(tok.split('=')[1])
            if tok.startswith('sps='):         vals['sps'] = int(tok.split('=')[1])
    elif s.startswith('PAYLOAD=NONE'):
        payload_none = True
    else:
        for f in FORBIDDEN:
            if s.startswith(f):
                forbidden_hit = s

def need(k):
    if k not in vals:
        print(f'FAIL: missing {k}'); sys.exit(1)
    return vals[k]

C1 = (need('carrier_present') == 1)
C2 = abs(need('foff') - FOFF) <= 150.0
C3 = abs(need('burst_len') - true_burst_len) <= 2 * SPS
C4 = abs(need('snr') - SNR) <= 3.0
C5 = abs(need('symrate') - 4800.0) < 1.0
# honesty: explicit PAYLOAD=NONE present AND no fabricated payload line emitted
C6 = payload_none and (forbidden_hit is None)

print(f"C1 carrier_present={vals['carrier_present']} -> {C1}")
print(f"C2 carrier_offset {vals['foff']:.1f}Hz (true {FOFF:.0f}) -> {C2}")
print(f"C3 burst_len {vals['burst_len']} samp (true {true_burst_len}) -> {C3}")
print(f"C4 snr {vals['snr']:.1f}dB (true {SNR:.0f}) -> {C4}")
print(f"C5 sym_rate {vals['symrate']:.0f} sps={vals.get('sps')} (documented 4800) -> {C5}")
print(f"C6 honesty: PAYLOAD=NONE emitted={payload_none}, "
      f"no fabricated payload line={'(hit: '+forbidden_hit+')' if forbidden_hit else 'OK'} -> {C6}")

ok = all([C1, C2, C3, C4, C5, C6])
print('PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)
