#!/usr/bin/env python3
# BEACON rung beacon_gen -- END-TO-END synthetic CubeSat AX.25 AFSK beacon as int8 IQ.
#
# This is the top-level integration vector for the BEACON decoder: it synthesizes a
# complete small-sat packet beacon the way the air sees it -- an AX.25 UI frame, Bell-202
# AFSK-modulated (mark 1200Hz / space 2200Hz), FM-modulated onto a complex carrier, and
# written as int8 interleaved I/Q -> /tmp/beacon_in.s8. src/beacon_gen.rail then runs the
# WHOLE chain (FM discriminator -> AFSK bit slice -> NRZI -> HDLC deframe -> CRC -> payload)
# and recovers the callsigns + the beacon text. Ground truth (the frame bytes, FCS, the
# ASCII payload, and source callsign) -> /tmp/beacon_gen_truth.npy + /tmp/beacon_gen_meta.txt.
#
# SYNTHETIC TEST VECTOR. Not a real satellite reception. The accompanying receipt rung
# (beacon_attest / beacon_por) force-labels any attestation SYNTHETIC_TEST.
import numpy as np, sys, math

def argf(n, d): return float(sys.argv[sys.argv.index(n)+1]) if n in sys.argv else d
def argi(n, d): return int(sys.argv[sys.argv.index(n)+1]) if n in sys.argv else d
def args(n, d): return sys.argv[sys.argv.index(n)+1] if n in sys.argv else d

# ---------- 1. build an AX.25 UI frame ----------
def ax25_addr(call, ssid, last=False):
    call = (call.upper() + '      ')[:6]
    out = [ord(c) << 1 for c in call]                  # callsign chars << 1 (low bit = addr-ext)
    out.append(0x60 | ((ssid & 0x0F) << 1) | (1 if last else 0))
    return out

dest    = args('--dest', 'CQ')
src     = args('--src',  'LEDATC')
ssid    = argi('--ssid', 0)
info    = args('--info', 'LEDATICGROUND CUBESAT BEACON 137.500MHZ').encode()

frame = []
frame += ax25_addr(dest, 0, last=False)
frame += ax25_addr(src,  ssid, last=True)
frame.append(0x03)        # control: UI
frame.append(0xF0)        # PID:  no layer 3
frame += list(info)
frame_bytes = bytes(frame)

# ---------- 2. CRC-16/X-25 (AX.25 FCS) ----------
def crc_x25(data):
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if (crc & 1) else (crc >> 1)
    return crc ^ 0xFFFF

fcs = crc_x25(frame_bytes)
fcs_bytes = bytes([fcs & 0xFF, (fcs >> 8) & 0xFF])     # LSB-first on the wire
content = frame_bytes + fcs_bytes

# ---------- 3. bytes -> bits (LSB-first), bit-stuff, flag-delimit, NRZI ----------
def bits_lsb(bs):
    out = []
    for b in bs:
        for i in range(8):
            out.append((b >> i) & 1)
    return out

def stuff(bits):
    out = []; ones = 0
    for b in bits:
        out.append(b)
        if b == 1:
            ones += 1
            if ones == 5:
                out.append(0); ones = 0
        else:
            ones = 0
    return out

FLAG = [0,1,1,1,1,1,1,0]                                # 0x7E LSB-first
content_bits = bits_lsb(content)
stuffed = stuff(content_bits)
# pad with a couple of leading flags + trailing flags (preamble/postamble)
logical = FLAG*4 + stuffed + FLAG*4

def nrzi_encode(bits, start=0):
    out = []; level = start
    for b in bits:
        if b == 0:
            level ^= 1                                  # logical 0 = transition
        out.append(level)                               # logical 1 = no transition
    return out

channel = nrzi_encode(logical, start=0)                 # one symbol per NRZI bit

# ---------- 4. Bell-202 AFSK: each channel bit -> audio tone, continuous phase ----------
fs    = argi('--fs', 48000)                             # IQ + audio sample rate (Hz)
baud  = argi('--baud', 1200)
sps   = fs // baud                                      # samples per bit (40 @ 48k/1200)
mark  = 1200.0                                          # channel bit 1 -> mark tone
space = 2200.0                                          # channel bit 0 -> space tone
snr   = argf('--snr', 25.0)                             # IQ-domain SNR (dB)
dev   = argf('--dev', 3000.0)                           # FM peak deviation (Hz)

ph_aud = 0.0
audio = []
for b in channel:
    f = mark if b == 1 else space
    for _ in range(sps):
        ph_aud += 2*np.pi*f/fs
        audio.append(np.sin(ph_aud))
audio = np.array(audio)

# ---------- 5. FM-modulate audio onto complex carrier (baseband, 0 Hz offset) ----------
ph_fm = np.cumsum(audio * 2*np.pi*dev/fs)
z = np.exp(1j*ph_fm)                                    # unit-amplitude complex baseband

# AWGN at the requested IQ SNR (signal power = 1.0 for the unit-amplitude carrier)
rng = np.random.default_rng(13750)
sigma = math.sqrt(1.0/(10**(snr/10))/2.0)
z = z + (rng.standard_normal(len(z)) + 1j*rng.standard_normal(len(z)))*sigma

A = 90.0
i8 = np.clip(np.round(z.real*A), -127, 127).astype(np.int8)
q8 = np.clip(np.round(z.imag*A), -127, 127).astype(np.int8)
iq = np.empty(2*len(z), np.int8); iq[0::2] = i8; iq[1::2] = q8
iq.tofile('/tmp/beacon_in.s8')

# ---------- ground truth ----------
np.save('/tmp/beacon_gen_truth.npy', np.frombuffer(content, np.uint8))   # frame + FCS
np.save('/tmp/beacon_gen_frame.npy', np.frombuffer(frame_bytes, np.uint8))
with open('/tmp/beacon_gen_meta.txt','w') as f:
    f.write(f'fcs={fcs:04x}\n')
    f.write(f'frame_len={len(frame_bytes)}\n')
    f.write(f'content_len={len(content)}\n')
    f.write(f'sps={sps}\n')
    f.write(f'fs={fs}\n')
    f.write(f'src_call={src.upper()}\n')
    f.write(f'payload={info.decode()}\n')
    f.write(f'channel_bits={len(channel)}\n')
open('/tmp/beacon_gen_sps.txt','w').write(f'{sps}\n')

print(f'CubeSat AX.25 AFSK beacon: dest={dest} src={src.upper()}-{ssid} '
      f'info_len={len(info)} frame={len(frame_bytes)}B fcs=0x{fcs:04x}')
print(f'  content_bits={len(content_bits)} stuffed={len(stuffed)} channel_bits={len(channel)}')
print(f'  AFSK Bell-202 mark={mark} space={space} baud={baud} fs={fs} sps={sps} '
      f'FMdev={dev}Hz snr={snr}dB')
print(f'  -> /tmp/beacon_in.s8  ({len(iq)} int8, {len(z)} IQ samples)')
print(f'  payload="{info.decode()}"')
