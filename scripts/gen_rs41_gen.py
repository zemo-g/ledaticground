#!/usr/bin/env python3
# RS41-1: Vaisala RS41-SG synthetic GFSK radiosonde frame -> int8 interleaved IQ.
#
# This is the TOP-LEVEL integration vector for the RS41 decoder chain
# (rs41_demod -> rs41_descramble -> rs41_rs -> rs41_decode -> rs41_attest).
# Mirrors gen_beacon_gen.py's generator/checker pattern + truth-emit convention.
#
# PUBLIC RS41 FORMAT (Bazjo / dxlAPRS / radiosonde_auto_rx reverse-engineering):
#   - 2-FSK / GFSK, baud=4800, deviation ~+/-2.4 kHz, downlink 400.0-405.99 MHz.
#   - Plain NRZ 2-FSK (NOT manchester; that is DFM). Bytes are LSB-first on the wire.
#   - Frame layout (this synthetic uses the STANDARD 0x140-byte descrambled frame):
#       [0..7]   : 8-byte frame sync header (constant, post-descramble)
#       [8..]    : typed sub-blocks {0x79 type, len, payload, CRC16-CCITT}
#       last 48  : 2 x RS(255,231) parity (24 bytes each), 2x interleaved
#   - The 0x140-byte body is XOR-scrambled on the wire with a fixed 64-byte mask M
#     (publicly known, derived from a documented LCG seeded 0x00; baked as a literal).
#   - RS error correction: RS(255,231) over GF(256) poly 0x11D, FCR=0, I=2 interleave.
#
# HONESTY: SYNTHETIC TEST VECTOR. No real 400 MHz reception (the 137-MHz halo cannot
# hear 400 MHz; that needs a separate antenna + LNA -- a hardware-procurement blocker).
# The whole SOFTWARE chain validates synthetically NOW.
import numpy as np, sys, math, json

def argf(n, d): return float(sys.argv[sys.argv.index(n)+1]) if n in sys.argv else d
def argi(n, d): return int(sys.argv[sys.argv.index(n)+1]) if n in sys.argv else d
def args(n, d): return sys.argv[sys.argv.index(n)+1] if n in sys.argv else d

# ====================================================================
# 0. The canonical RS41 64-byte XOR scramble mask M.
#    Documented LCG: x_{n+1} = (x_n * 11 + 6) & 0xFF, seeded x_0 = 0x00, then
#    each output byte is the bit-reversal of x_n. This reproduces the published
#    RS41 mask used by radiosonde_auto_rx (utils/RS41/) / dxlAPRS / Bazjo.
#    We COMPUTE it here (Python ints don't overflow) and ALSO emit it as a Rail
#    literal so the Rail descrambler bakes the identical 64 bytes (Rail PRNG
#    overflows -> must use a baked literal, per CLAUDE.md).
# ====================================================================
def revbits(b):
    r = 0
    for i in range(8):
        r = (r << 1) | ((b >> i) & 1)
    return r & 0xFF

def rs41_mask():
    m = []
    x = 0x00
    for _ in range(64):
        m.append(revbits(x))
        x = (x * 11 + 6) & 0xFF
    return m

MASK = rs41_mask()

# RS41 8-byte frame sync header (constant, present after descramble at frame start).
SYNC = [0x86, 0x35, 0xF4, 0x40, 0x93, 0xDF, 0x1A, 0x60]

# ====================================================================
# 1. CRC-16/CCITT (poly 0x1021, init 0xFFFF, non-reflected) per sub-block.
#    DIFFERENT from AX.25's reflected 0x8408: shift LEFT, test the TOP bit.
# ====================================================================
def crc16_ccitt(data):
    crc = 0xFFFF
    for b in data:
        crc ^= (b << 8)
        crc &= 0xFFFF
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc & 0xFFFF

# ====================================================================
# 2. GF(256) + RS(255,231) FCR=0 systematic encoder (Python ref).
#    Engine identical to gen_rs.py but NPAR=24, FCR=0.
# ====================================================================
PRIM = 0x11D
exp = [0]*512; log = [0]*256
_x = 1
for _i in range(255):
    exp[_i] = _x; log[_x] = _i
    _x <<= 1
    if _x & 0x100: _x ^= PRIM
for _i in range(255, 512): exp[_i] = exp[_i-255]
def gmul(a, b):
    if a == 0 or b == 0: return 0
    return exp[(log[a]+log[b]) % 255]

NPAR = 24      # RS(255,231): 24 parity bytes, corrects up to 12 byte errors
FCR  = 0       # first consecutive root alpha^0  (RS41 convention)
g = [1]
for _i in range(NPAR):
    root = exp[(FCR+_i) % 255]
    ng = [0]*(len(g)+1)
    for j in range(len(g)):
        ng[j]   ^= gmul(g[j], root)
        ng[j+1] ^= g[j]
    g = ng

def rs_encode_231(data):     # 231 data bytes -> 255-byte codeword (systematic)
    assert len(data) == 231
    par = [0]*NPAR
    for d in data:
        fb = d ^ par[NPAR-1]
        for j in range(NPAR-1, 0, -1):
            par[j] = par[j-1] ^ gmul(g[j], fb)
        par[0] = gmul(g[0], fb)
    return list(data) + par[::-1]   # parity emitted high-order first

# ====================================================================
# 3. Build the clean RS41 frame body (sync + typed blocks), then layer RS.
#    Frame body = 8 sync + 462 payload region (= 2*231 RS data) where the
#    24+24 parity occupy the last 48 of the 2x231 data block. We lay the
#    blocks into the 2-interleaved RS data area so the descrambled frame is
#    exactly the RS-codeword bytes, 2x column-interleaved after the sync.
# ====================================================================
# Block builder: {0x79, type, len, payload..., crc_lo, crc_hi}
def block(btype, payload):
    body = [0x79, btype & 0xFF, len(payload) & 0xFF] + list(payload)
    crc = crc16_ccitt(body)           # CRC over the [0x79,type,len,payload] header+payload
    return body + [crc & 0xFF, (crc >> 8) & 0xFF]

# planted facts
serial = args('--serial', 'V2730155')          # 8-char ASCII RS41 serial
framenum = argi('--frame', 4242)
battery_mv = argi('--batt', 2950)
# real-ish GPS for a balloon over Michigan: 42.95N, -83.55W, alt 18300 m
lat = argf('--lat', 42.95000)
lon = argf('--lon', -83.55000)
alt = argf('--alt', 18300.0)
temp_count = argi('--tempcount', 1234567)       # raw PTU temp count (uncalibrated)
rh_count   = argi('--rhcount', 765432)
pres_count = argi('--prescount', 543210)

# ---- WGS84 geodetic -> ECEF (the FACT the sonde transmits) ----
WGS84_A = 6378137.0
WGS84_F = 1.0/298.257223563
WGS84_E2 = WGS84_F*(2-WGS84_F)
def geodetic_to_ecef(lat_deg, lon_deg, h):
    la = math.radians(lat_deg); lo = math.radians(lon_deg)
    sN = WGS84_A/math.sqrt(1-WGS84_E2*math.sin(la)**2)
    X = (sN+h)*math.cos(la)*math.cos(lo)
    Y = (sN+h)*math.cos(la)*math.sin(lo)
    Z = (sN*(1-WGS84_E2)+h)*math.sin(la)
    return X, Y, Z
ecef = geodetic_to_ecef(lat, lon, alt)
# RS41 GPS-POS carries ECEF in cm (int32 LE). round to nearest cm.
ecef_cm = [int(round(c*100.0)) for c in ecef]
vel_cms = [int(round(v)) for v in (1234, -567, 89)]   # planted ECEF velocity cm/s

def le32(v):       # signed int32 little-endian (4 bytes)
    return list((v & 0xFFFFFFFF).to_bytes(4, 'little'))
def le24(v):       # unsigned 24-bit little-endian count (3 bytes)
    return list((v & 0xFFFFFF).to_bytes(3, 'little'))

# STATUS block 0x7928: frame# (u16 LE), serial (8 ASCII), battery (u16 LE mV)
status_payload = list(framenum.to_bytes(2, 'little')) + \
                 [ord(c) for c in (serial + '        ')[:8]] + \
                 list((battery_mv & 0xFFFF).to_bytes(2, 'little'))
# GPS-POS block 0x7C: ECEF X/Y/Z cm (int32 LE) + ECEF VX/VY/VZ cm/s (int32 LE)
gpspos_payload = le32(ecef_cm[0]) + le32(ecef_cm[1]) + le32(ecef_cm[2]) + \
                 le32(vel_cms[0]) + le32(vel_cms[1]) + le32(vel_cms[2])
# MEAS / PTU block 0x7A: raw temp/rh/pressure counts (24-bit LE counts)
ptu_payload = le24(temp_count) + le24(rh_count) + le24(pres_count)

blk_status = block(0x28, status_payload)
blk_gpspos = block(0x7C, gpspos_payload)
blk_ptu    = block(0x7A, ptu_payload)

# data region = the typed blocks, zero-padded out to 2*231 = 462 bytes (minus 48 parity).
# RS data per codeword = 231; with 2 interleaved CWs, the descrambled frame after the
# 8-byte sync carries cw0[0..230] and cw1[0..230] interleaved (cadu[i*2+k]).
RS_DATA_PER_CW = 231 - NPAR     # 231 total per cw includes parity? No: 231 DATA + 24 par = 255.
# We carry the typed blocks in the 231-data area of EACH codeword. The decoder reads the
# 231 data bytes of cw0 then cw1; blocks live in cw0's data area (cw1 reserved/padded here).
data0 = (blk_status + blk_gpspos + blk_ptu)
assert len(data0) <= 231, f"blocks {len(data0)} > 231 data bytes"
data0 = data0 + [0]*(231-len(data0))
data1 = [0]*231                  # reserved / extended frame area (zero in standard)

cw0 = rs_encode_231(data0)       # 255 bytes (231 data + 24 parity)
cw1 = rs_encode_231(data1)       # 255 bytes

# descrambled frame = 8-byte sync + 2x interleaved codewords (cadu[i*2+k]=cw_k[i])
cadu = [0]*(255*2)
for i in range(255):
    cadu[i*2+0] = cw0[i]
    cadu[i*2+1] = cw1[i]
descrambled = SYNC + cadu        # 8 + 510 = 518 bytes
FRAME_LEN = len(descrambled)

# ---- XOR-scramble the body (everything AFTER the sync stays scrambled on wire? )
# Public RS41: the WHOLE frame incl. sync is XOR'd with M mod 64 on the wire; the sync
# constant 0x86.. is the POST-descramble value. So scramble the full FRAME_LEN bytes.
scrambled = [descrambled[i] ^ MASK[i % 64] for i in range(FRAME_LEN)]

# ---- bits LSB-first ----
def bits_lsb(bs):
    out = []
    for b in bs:
        for i in range(8):
            out.append((b >> i) & 1)
    return out

PREAMBLE = [0x55]*8                                   # 0x55/0xAA alternating preamble
wire_bytes = PREAMBLE + scrambled
channel = bits_lsb(wire_bytes)                        # one symbol per NRZ bit (2-FSK)

# ====================================================================
# 4. GFSK / 2-FSK modulate: NRZ map bit -> +/-1, gaussian-shape, FM onto baseband.
# ====================================================================
fs   = argi('--fs', 48000)
baud = argi('--baud', 4800)
sps  = fs // baud                                     # samples per symbol (10 @ 48k/4800)
snr  = argf('--snr', 25.0)
dev  = argf('--dev', 2400.0)                          # FM peak deviation (Hz)
BT   = argf('--bt', 0.5)

# NRZ symbol stream: bit 1 -> +1 (mark/high tone), bit 0 -> -1
nrz = np.repeat(np.array([1.0 if b == 1 else -1.0 for b in channel]), sps)

# gaussian pulse shaping (BT-bandwidth), normalized to unit DC gain
def gaussian_taps(bt, sps, span=3):
    # standard GFSK gaussian pulse: sigma (in samples) = sqrt(ln2)/(2*pi*BT) * sps
    n = np.arange(-span*sps, span*sps+1)
    sigma = math.sqrt(math.log(2))/(2*math.pi*bt)*sps
    h = np.exp(-(n**2)/(2*sigma**2))
    return h/np.sum(h)
taps = gaussian_taps(BT, sps)
shaped = np.convolve(nrz, taps, mode='same')

# FM-modulate the shaped symbols onto a complex baseband carrier
ph = np.cumsum(shaped * 2*np.pi*dev/fs)
z = np.exp(1j*ph)

# AWGN at the requested IQ SNR
rng = np.random.default_rng(40642)
sigma = math.sqrt(1.0/(10**(snr/10))/2.0)
z = z + (rng.standard_normal(len(z)) + 1j*rng.standard_normal(len(z)))*sigma

A = 90.0
i8 = np.clip(np.round(z.real*A), -127, 127).astype(np.int8)
q8 = np.clip(np.round(z.imag*A), -127, 127).astype(np.int8)
iq = np.empty(2*len(z), np.int8); iq[0::2] = i8; iq[1::2] = q8
iq.tofile('/tmp/rs41_in.s8')

# ====================================================================
# 5. Ground truth + meta.
# ====================================================================
np.save('/tmp/rs41_truth.npy', np.array(descrambled, np.uint8))   # DESCRAMBLED frame bytes
np.save('/tmp/rs41_scrambled.npy', np.array(scrambled, np.uint8))
np.save('/tmp/rs41_mask.npy', np.array(MASK, np.uint8))
# planted bits (the channel bits, no preamble) for the demod checker
np.save('/tmp/rs41_bits.npy', np.array(channel, np.uint8))
np.save('/tmp/rs41_preamble_bits.npy', np.array(bits_lsb(PREAMBLE), np.uint8))

with open('/tmp/rs41_meta.txt', 'w') as f:
    f.write(f'serial={serial}\n')
    f.write(f'frame_num={framenum}\n')
    f.write(f'battery_mv={battery_mv}\n')
    f.write(f'lat={lat:.5f}\n')
    f.write(f'lon={lon:.5f}\n')
    f.write(f'alt={alt:.1f}\n')
    f.write(f'ecef_x_cm={ecef_cm[0]}\n')
    f.write(f'ecef_y_cm={ecef_cm[1]}\n')
    f.write(f'ecef_z_cm={ecef_cm[2]}\n')
    f.write(f'vel_x_cms={vel_cms[0]}\n')
    f.write(f'vel_y_cms={vel_cms[1]}\n')
    f.write(f'vel_z_cms={vel_cms[2]}\n')
    f.write(f'temp_count={temp_count}\n')
    f.write(f'rh_count={rh_count}\n')
    f.write(f'pres_count={pres_count}\n')
    f.write(f'sps={sps}\n')
    f.write(f'fs={fs}\n')
    f.write(f'baud={baud}\n')
    f.write(f'frame_len={FRAME_LEN}\n')
    f.write(f'preamble_bytes={len(PREAMBLE)}\n')
    f.write(f'snr={snr}\n')
open('/tmp/rs41_sps.txt', 'w').write(f'{sps}\n')

# emit the 64-byte mask as a Rail arr_set literal so rs41_descramble.rail bakes it identically
with open('/tmp/rs41_mask_rail.txt', 'w') as f:
    for i, b in enumerate(MASK):
        f.write(f'  let _ = arr_set m {i} {b}\n')

# ====================================================================
# 6. Python round-trip self-check (proves the vector is self-consistent
#    BEFORE any Rail runs): descramble -> deinterleave -> RS-decode-trivial
#    (no errors here) -> block CRC -> parse -> recover planted facts.
# ====================================================================
def selfcheck():
    # descramble
    rt = [scrambled[i] ^ MASK[i % 64] for i in range(FRAME_LEN)]
    assert rt == descrambled, "descramble round-trip mismatch"
    assert rt[:8] == SYNC, "sync header mismatch"
    cad = rt[8:]
    d0 = [cad[i*2+0] for i in range(231)]
    d1 = [cad[i*2+1] for i in range(231)]
    assert d0 == data0[:231], "cw0 data mismatch"
    # walk typed blocks in d0
    found = {}
    p = 0
    while p < len(d0) and d0[p] == 0x79:
        btype = d0[p+1]; blen = d0[p+2]
        body = d0[p:p+3+blen]
        rxcrc = d0[p+3+blen] | (d0[p+4+blen] << 8)
        calc = crc16_ccitt(body)
        assert calc == rxcrc, f"block 0x{btype:02x} CRC {calc:04x}!={rxcrc:04x}"
        found[btype] = body[3:3+blen]
        p += 3 + blen + 2
    # STATUS
    st = found[0x28]
    rec_frame = st[0] | (st[1] << 8)
    rec_serial = bytes(st[2:10]).decode('ascii').rstrip()
    assert rec_frame == framenum, f"frame {rec_frame}!={framenum}"
    assert rec_serial == serial, f"serial {rec_serial!r}!={serial!r}"
    # GPS-POS
    gp = found[0x7C]
    def rd32(o): return int.from_bytes(bytes(gp[o:o+4]), 'little', signed=True)
    rx_ecef = [rd32(0), rd32(4), rd32(8)]
    assert rx_ecef == ecef_cm, f"ecef {rx_ecef}!={ecef_cm}"
    return rec_serial, rec_frame, rx_ecef

rs, rf, re_ecef = selfcheck()
print(f"RS41-SG synthetic frame: serial={serial} frame#={framenum} "
      f"lat={lat:.4f} lon={lon:.4f} alt={alt:.0f}m")
print(f"  ECEF cm = {ecef_cm}  (descrambled {FRAME_LEN}B, RS(255,231) FCR=0 I=2, NPAR={NPAR})")
print(f"  GFSK baud={baud} dev={dev}Hz fs={fs} sps={sps} BT={BT} snr={snr}dB")
print(f"  -> /tmp/rs41_in.s8  ({len(iq)} int8, {len(z)} IQ samples)")
print(f"  python round-trip self-check: serial={rs} frame#={rf} ecef={re_ecef} OK")
print(f"  mask[0..7]={MASK[:8]}  sync={[hex(s) for s in SYNC]}")
