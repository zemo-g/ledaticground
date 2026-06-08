#!/usr/bin/env python3
# ===========================================================================
# ACARS rung: acars_gen — PYTHON end-to-end SYNTHETIC ARINC 618 ACARS chain
# generator. Builds the FULL transmit chain from a real ACARS message down to
# int8 baseband IQ, so the whole receive chain (acars_am -> acars_msk ->
# acars_deframe rungs) is testable offline against a known ground truth. This
# is the "gen.py" for the ACARS chain: it ties together every stage the
# individual decoder rungs reverse, in the exact same parameters.
#
# TRANSMIT CHAIN (each stage's matching receive rung in [ ]):
#   ARINC 618 character block (pre-key BS BS BS, SYN SYN, SOH, header,
#     STX, text, ETX, BCS, DEL)
#     -> 7-bit ASCII + 8th ODD-parity bit per char (BCS bytes raw)   [acars_deframe parity]
#     -> 16-bit reflected CRC-CCITT BCS (poly 0x8408, init 0)         [acars_deframe BCS]
#     -> MSB-first bit stream (bit7..bit0 per char)
#     -> 2400 bps MSK audio: bit1 -> 1200 Hz, bit0 -> 2400 Hz, h=0.5  [acars_msk]
#        continuous-phase, fs=48000, sps=20 (matches gen_acars_msk/iq)
#     -> AM modulate a 0-Hz baseband carrier: z = (1 + m*msk)         [acars_am]
#     -> AWGN at the requested SNR
#     -> quantize to int8 interleaved IQ  -> /tmp/acars_in.s8
#
# Ground truth (the message fields + BCS + bits + modulation params) is written
# to /tmp/acars_gen_truth.json so check_acars_gen.py can run the FULL inverse
# chain (envelope demod -> MSK bit recovery -> bit->byte -> deframe -> parity ->
# BCS -> field parse) and confirm the generated signal decodes back to the
# original message. HONESTY: this is a SYNTHETIC vector (numpy-generated), as
# expected for the rung; no real off-air ACARS pass is decoded and no
# attestation is produced. The BCS definition is the acarsdec reflected
# CRC-CCITT convention, validated self-consistently (encoder == decoder).
#
# Cross-pol caveat (designed-for): ACARS is terrestrial VERTICALLY polarized;
# a horizontal antenna costs 15-20 dB cross-pol loss. Default operating point is
# 14 dB (the chain decodes cleanly). Pass --snr 6 to exercise the low-SNR
# tolerance of the envelope front-end (residual bit errors then get caught by
# parity + BCS downstream, which is the honest behaviour).
# ===========================================================================
import numpy as np, json, sys, math

# ----------------------------------------------------------------- parameters
FS   = 48000
BAUD = 2400
SPS  = FS // BAUD            # 20 samples / bit (matches gen_acars_iq / gen_acars_msk)
F1, F0 = 1200.0, 2400.0      # MSK tones: bit1 -> 1200 Hz, bit0 -> 2400 Hz

def argf(flag, d):
    return float(sys.argv[sys.argv.index(flag)+1]) if flag in sys.argv else d
def argi(flag, d):
    return int(sys.argv[sys.argv.index(flag)+1]) if flag in sys.argv else d

snr = argf('--snr', 14.0)   # dB operating point
m   = argf('--m', 0.8)      # AM modulation depth
A   = argf('--amp', 70.0)   # int8 quantization scale
rng = np.random.default_rng(31)

# --------------------------------------------------------- ARINC 618 framing
SOH, STX, ETX, ETB, SYN, BS, DEL = 0x01, 0x02, 0x03, 0x17, 0x16, 0x2B, 0x7F

# header fields (7-bit ASCII), matching the gen_acars_block.py reference block
mode  = ord('2')                 # mode char
reg   = '.N827NN'                # aircraft registration / address (7 chars)
ack   = 0x15                     # NAK (no ack)
label = 'H1'                     # message label (2 chars)
blkid = ord('3')                 # block id (1 char)
text  = 'OPS NORMAL FL350 ETA 1423Z'

def odd_parity_byte(c7):
    # set bit7 so total number of set bits is ODD
    ones = bin(c7 & 0x7F).count('1')
    p = 0 if (ones % 2 == 1) else 1
    return (p << 7) | (c7 & 0x7F)

def crc_ccitt_acars(data7):
    # reflected CRC-16-CCITT (poly 0x8408), init 0 — acarsdec block-check convention
    crc = 0
    for c in data7:
        crc ^= (c & 0x7F)
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if (crc & 1) else (crc >> 1)
    return crc & 0xFFFF

# the BCS is computed over every char AFTER SOH up to & incl ETX (7-bit data)
hdr          = [mode] + [ord(x) for x in reg] + [ack] + [ord(x) for x in label] + [blkid]
data_for_crc = hdr + [STX] + [ord(x) for x in text] + [ETX]
bcs          = crc_ccitt_acars(data_for_crc)
bcs_lo, bcs_hi = bcs & 0xFF, (bcs >> 8) & 0xFF   # acarsdec sends low byte first

# full character sequence: pre-key, bit-sync, SYN SYN, SOH, body, BCS, DEL
prekey = [0x00] * 4
sync   = [BS, BS, BS, SYN, SYN, SOH]
body7  = data_for_crc                                # mode..ETX (7-bit)
sync8  = [odd_parity_byte(c) for c in sync]
body8  = [odd_parity_byte(c) for c in body7]
tail8  = [bcs_lo, bcs_hi, odd_parity_byte(DEL)]      # BCS raw (no parity), DEL parity'd
chars  = prekey + sync8 + body8 + tail8

# --------------------------------------------------------- chars -> bit stream
# MSB-first per byte (bit7..bit0), matching gen_acars_block.py .bits convention
bits = []
for c in chars:
    for k in range(7, -1, -1):
        bits.append((c >> k) & 1)
bits = np.array(bits, np.uint8)

# --------------------------------------------------------- bits -> MSK audio
inst = np.where(np.repeat(bits, SPS) == 1, F1, F0).astype(np.float64)
ph   = 2 * np.pi * np.cumsum(inst) / FS
msk  = np.cos(ph)                                    # clean MSK audio, range [-1, 1]

# settling pre-key carrier region (unmodulated) so the AM demod has a DC reference
pre  = np.ones(2 * SPS, np.float64)
env  = np.concatenate([pre, 1.0 + m * msk])         # AM envelope on a 0-Hz carrier
z    = env.astype(np.complex128)

# --------------------------------------------------------- AWGN + quantize
sigpow = float(np.mean(np.abs(z) ** 2))
npow   = sigpow / (10 ** (snr / 10.0))
noise  = (rng.standard_normal(len(z)) + 1j * rng.standard_normal(len(z))) * math.sqrt(npow / 2)
z      = z + noise

i8 = np.clip(np.round(z.real * A), -127, 127).astype(np.int8)
q8 = np.clip(np.round(z.imag * A), -127, 127).astype(np.int8)
iq = np.empty(2 * len(z), np.int8); iq[0::2] = i8; iq[1::2] = q8
iq.tofile('/tmp/acars_in.s8')

# --------------------------------------------------------- ground truth
truth = dict(
    fs=FS, baud=BAUD, sps=SPS, f1=F1, f0=F0, m=m, amp=A, snr=snr,
    prekey_samples=int(len(pre)),
    n_chars=len(chars), n_prekey_chars=len(prekey),
    mode=chr(mode), reg=reg, ack=ack, label=label, blkid=chr(blkid), text=text,
    bcs=bcs, bcs_lo=bcs_lo, bcs_hi=bcs_hi,
    chars=[int(c) for c in chars],          # full 8-bit char stream (incl parity)
    body7=[int(c) for c in body7],          # 7-bit data over which BCS is computed
)
json.dump(truth, open('/tmp/acars_gen_truth.json', 'w'))
np.save('/tmp/acars_gen_bits.npy', bits)
np.save('/tmp/acars_gen_msk.npy', msk.astype(np.float32))
np.save('/tmp/acars_gen_meta.npy', np.array([FS, BAUD, SPS, len(pre), len(bits)]))

print(f'acars_gen: msg mode={chr(mode)} reg={reg} label={label} blkid={chr(blkid)} '
      f'text="{text}"')
print(f'  {len(chars)} chars, {len(bits)} bits, BCS=0x{bcs:04x} '
      f'(lo=0x{bcs_lo:02x} hi=0x{bcs_hi:02x})')
print(f'  MSK fs={FS} sps={SPS} m={m} snr={snr}dB prekey={len(pre)} samp '
      f'-> /tmp/acars_in.s8 ({len(z)} IQ samples)')
