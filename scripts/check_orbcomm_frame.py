#!/usr/bin/env python3
# check_orbcomm_frame.py -- validator for src/orbcomm_frame.rail (Orbcomm rung 3:
# frame/header parse to the PUBLIC-DOCS boundary + honest IQ characterization).
#
# Two ways to run:
#   1) Standalone (reproducible from scratch -- regenerates the vector, compiles+runs
#      the Rail module via scripts/railrun.sh, then validates):
#         /opt/homebrew/bin/python3.11 scripts/check_orbcomm_frame.py
#   2) Against a captured stdout file (the repo harness convention):
#         /opt/homebrew/bin/python3.11 scripts/check_orbcomm_frame.py /tmp/orbcomm_frame_out.txt
#
# PASS requires all 7 sub-checks against the SYNTHETIC ground truth:
#   A1: unique word found at offset 0 with full 16/16 score (frame sync),
#   A2: CRC-16/X-25 over the framed (header+payload) region recomputes == trailing FCS,
#   A3: header reported OPAQUE (no fabricated field semantics) and payload length correct,
#   B1: measured carrier offset within +/-150 Hz of the true 600 Hz,
#   B2: measured burst length within +/-2 symbols-worth of samples of the true frame length,
#   B3: measured SNR within +/-3 dB of the true 14 dB,
#   B4: reported symbol rate == documented 4800 sym/s.
#
# HONESTY: every vector here is a SYNTHETIC test vector. This proves the public PHY/framing
# *mechanism* (diff-PSK / unique-word sync / self-sync scrambler upstream / CRC integrity /
# physical characterization). It is NOT a real off-air Orbcomm decode and invents no
# proprietary payload semantics.
import os, sys, subprocess, numpy as np

GD = '/Users/ledaticempire/projects/ledaticground'
GEN = os.path.join(GD, 'scripts', 'gen_orbcomm_frame.py')
RAILRUN = os.path.join(GD, 'scripts', 'railrun.sh')
SRC = os.path.join(GD, 'src', 'orbcomm_frame.rail')
PY = '/opt/homebrew/bin/python3.11'


def get_stdout():
    """Return the Rail module's stdout. If an arg is given, read it as a captured file;
    otherwise regenerate the vector, compile+run the module, and capture live."""
    if len(sys.argv) > 1:
        with open(sys.argv[1]) as f:
            return f.read()
    # standalone: regenerate vector then compile+run
    subprocess.run([PY, GEN], check=True)
    res = subprocess.run(['bash', RAILRUN, SRC], capture_output=True, text=True)
    return res.stdout


def main():
    out = get_stdout()

    t = np.load('/tmp/orbcomm_frame_truth.npy')   # [uwlen, hdr, pay, crc, total]
    c = np.load('/tmp/orbcomm_frame_char.npy')    # [FS, SYM_RATE, foff_hz, snr, SPS, GAP]
    uwlen, HDR, PAY, CRC_TRUE, TOTAL = [int(x) for x in t]
    FS, SYM_RATE, FOFF, SNR, SPS, GAP = c
    SPS = int(SPS); GAP = int(GAP)
    true_burst_len = TOTAL * SPS   # frame bits * samples/symbol

    vals = {}
    hdr_opaque = False
    pay_len = None
    for l in out.splitlines():
        l = l.strip()
        if l.startswith('SYNC '):
            for tok in l.split():
                if tok.startswith('uw_at='): vals['uw_at'] = int(tok.split('=')[1])
                if tok.startswith('score='): vals['score'] = int(tok.split('=')[1])
        elif l.startswith('HEADER_OPAQUE '):
            hdr_opaque = True
            vals['hdr_bits'] = l.split(None, 1)[1]
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
        if k not in vals:
            print(f'FAIL: missing {k}')
            print('--- module stdout was ---')
            print(out)
            sys.exit(1)
        return vals[k]

    A1 = (need('uw_at') == 0 and need('score') == 16)
    A2 = (need('crc_ok') == 1)
    A3 = hdr_opaque and (pay_len == PAY)
    B1 = abs(need('foff') - FOFF) <= 150.0
    B2 = abs(need('burst_len') - true_burst_len) <= 2 * SPS
    B3 = abs(need('snr') - SNR) <= 3.0
    B4 = abs(need('symrate') - 4800.0) < 1.0

    print(f"A1 sync uw@{vals['uw_at']} score {vals['score']}/16 -> {A1}")
    print(f"A2 crc_ok={vals['crc_ok']} (true crc 0x{CRC_TRUE:04x}) -> {A2}")
    print(f"A3 header opaque={hdr_opaque} pay_len={pay_len} (true {PAY}) -> {A3}")
    print(f"B1 carrier_offset {vals['foff']:.1f}Hz (true {FOFF:.0f}) -> {B1}")
    print(f"B2 burst_len {vals['burst_len']} samp (true {true_burst_len}) -> {B2}")
    print(f"B3 snr {vals['snr']:.1f}dB (true {SNR:.0f}) -> {B3}")
    print(f"B4 sym_rate {vals['symrate']:.0f} (documented 4800) -> {B4}")
    ok = all([A1, A2, A3, B1, B2, B3, B4])
    print('PASS' if ok else 'FAIL')
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
