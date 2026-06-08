#!/usr/bin/env python3
# Reference vector for src/orbcomm_frame.rail — frame/header parse (public-docs boundary) +
# HONEST characterization fallback.
#
# THE PUBLIC/PROPRIETARY BOUNDARY (this is the whole point of the rung):
#   DECODABLE FROM PUBLIC KNOWLEDGE:
#     - band 137.2-137.8 MHz, ~25 kHz channels, SD-PSK ~4800 sym/s  (FCC filings, SDR work)
#     - the PHY: differential PSK, a preamble + unique word, a self-sync scrambler
#     - link-layer FRAMING exists (sync -> a fixed-length header region -> payload region),
#       and from the recovered bits you can MEASURE structure: where sync is, header length,
#       packet length, packet timing/cadence, and integrity over the framed region.
#   NOT DECODABLE FROM PUBLIC KNOWLEDGE (do NOT fabricate):
#     - the SEMANTIC meaning of header fields (message type codes, subscriber IDs, routing)
#       is PROPRIETARY; Orbcomm user data is a commercial, partly-encrypted system with no
#       public message dictionary. We therefore parse STRUCTURE + INTEGRITY, and report the
#       payload as OPAQUE bits with a measured length + a CRC over the framed region. We never
#       emit a "message" with invented field semantics.
#
# So rung 3 produces, from the descrambled bitstream:
#   - confirmation of frame sync (unique word), header REGION bits (opaque), payload length,
#   - a CRC-16 over the framed region (integrity, recomputable),
#   - and a CHARACTERIZATION block measured from the IQ: carrier offset, symbol rate,
#     packet timing (burst gap), SNR -- none of which require knowing the payload meaning.
#
# Outputs:
#   /tmp/orbcomm_frame_in.bits  -- descrambled framed bits (1 byte/bit): UW + header + payload + CRC
#   /tmp/orbcomm_frame_iq.s8    -- an IQ burst (for the characterization measurements)
#   ground truth .npy           -- uw offset, header/payload lengths, CRC, sym-rate, gap
import numpy as np, sys

rng = np.random.default_rng(13)

# ---- framed bit layout (structure is public; field SEMANTICS are not, so payload is opaque) ----
UW = [0,0,1,0,0,1,1,0,1,1,1,0,1,0,0,1]      # same 16-bit unique word as rung 1
HDR_LEN = 24                                  # header REGION length in bits (opaque region)
PAY_LEN = 144                                 # payload REGION length in bits (opaque)
header  = rng.integers(0, 2, HDR_LEN).tolist()
payload = rng.integers(0, 2, PAY_LEN).tolist()

# CRC-16/X-25 over (header + payload) bytes for an integrity check the receiver can recompute.
def crc16_x25_bits(bits):
    # pack bits MSB-first into bytes (pad to byte boundary), reflected CRC-16/X-25
    nbytes = (len(bits)+7)//8
    by = bytearray(nbytes)
    for i,b in enumerate(bits):
        by[i//8] |= (b & 1) << (7-(i%8))
    crc = 0xFFFF
    for byte in by:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if (crc & 1) else (crc >> 1)
    return crc ^ 0xFFFF

framed_payload_region = header + payload
crc = crc16_x25_bits(framed_payload_region)
crc_bits = [(crc >> (15-i)) & 1 for i in range(16)]   # MSB-first 16-bit CRC

frame_bits = UW + header + payload + crc_bits
np.array(frame_bits, np.uint8).tofile('/tmp/orbcomm_frame_in.bits')

# ---- IQ burst for the CHARACTERIZATION fallback ----
# Build a SD-PSK burst at a known symbol rate with a known carrier offset, plus a gap (silence)
# so the characterizer can measure packet timing. We oversample to make sym-rate measurable.
FS = 38400.0          # sample rate (Hz)  -- 8 samples / symbol at 4800 sym/s
SPS = 8               # samples per symbol
SYM_RATE = FS/SPS     # = 4800 sym/s (the documented Orbcomm symbol rate)
foff_hz = 600.0       # known carrier offset within the channel (Hz)
snr_db = 14.0

# differential BPSK modulate the frame bits, oversample by SPS with rectangular pulses
phase = 0.0; syms = []
for b in frame_bits:
    phase += (np.pi if b == 1 else 0.0)
    syms.append(np.exp(1j*phase))
syms = np.repeat(np.array(syms), SPS)          # SPS samples/symbol
# leading + trailing silence (the inter-burst gap, for packet-timing measurement)
GAP = 256
burst = np.concatenate([np.zeros(GAP, complex), syms, np.zeros(GAP, complex)])
n = np.arange(len(burst))
burst = burst * np.exp(1j*(2*np.pi*(foff_hz/FS)*n + 0.4))
sigma = (1.0/np.sqrt(2)) / (10**(snr_db/20))
# add noise everywhere (incl. the gap, so SNR/timing are realistic)
burst = burst + (rng.standard_normal(len(burst)) + 1j*rng.standard_normal(len(burst)))*sigma
A = 80.0
i8 = np.clip(np.round(burst.real*A), -127, 127).astype(np.int8)
q8 = np.clip(np.round(burst.imag*A), -127, 127).astype(np.int8)
iq = np.empty(2*len(burst), np.int8); iq[0::2]=i8; iq[1::2]=q8
iq.tofile('/tmp/orbcomm_frame_iq.s8')

np.save('/tmp/orbcomm_frame_truth.npy', np.array([
    len(UW), HDR_LEN, PAY_LEN, crc, len(frame_bits)], np.int64))
np.save('/tmp/orbcomm_frame_char.npy', np.array([
    FS, SYM_RATE, foff_hz, snr_db, SPS, GAP], np.float64))
print(f'frame: UW(16)+hdr({HDR_LEN})+pay({PAY_LEN})+crc(16) = {len(frame_bits)} bits, crc=0x{crc:04x}')
print(f'IQ burst: FS={FS} SPS={SPS} sym_rate={SYM_RATE} foff={foff_hz}Hz snr={snr_db}dB gap={GAP}')
