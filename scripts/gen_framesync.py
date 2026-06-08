#!/usr/bin/env python3
# Reference vector for src/framesync.rail — CCSDS attached-sync-marker (ASM) frame sync
# over SOFT bits with QPSK 4-fold phase-ambiguity resolution. The rung that sits between
# qpsk.rail (carrier recovery) and viterbi.rail (soft-decision decode).
#
# framesync carries SOFT decisions: each sample is a signed int8 in [-128,127]. The implied
# hard bit is 0 if s>=0 else 1 (+ -> 0, - -> 1), matching qpsk.rail / viterbi.rail soft_at.
# The ASM 0x1ACFFC1D is embedded as soft values (a confident bit b -> sign(+1 for 0 / -1 for
# 1) * amplitude), at a random even bit offset under a chosen QPSK rotation, surrounded by
# random soft garbage + AWGN. The decoder must correlate the ASM under all 4 rotations,
# peak-pick (offset, rotation), and emit the de-rotated soft payload that follows.
#
# Output: /tmp/framesync_in.s8 (1 signed int8 / soft bit). Ground truth JSON:
# (offset, de-rotation, the de-rotated payload SOFT values that should be emitted).
import numpy as np, sys, json

def argf(flag, default):
    return type(default)(sys.argv[sys.argv.index(flag)+1]) if flag in sys.argv else default

rng  = np.random.default_rng(argf('--seed', 11))
ASM  = 0x1ACFFC1D
DATA_BITS = argf('--databits', 128)        # frame-aligned soft bits emitted after the ASM
# LEAD must be EVEN: a frame can only begin on a QPSK symbol boundary (2 bits/symbol).
LEAD = argf('--lead', 24)
ROT  = argf('--rot', 1)                     # forward rotation 0..3 injected by the channel
AMP  = argf('--amp', 90)                    # soft amplitude of the clean signal (pre-noise)
NOISE = argf('--noise', 18.0)              # AWGN sigma added to the soft values

# --- build the clean HARD bit stream: ASM (32 bits MSB first) + DATA random bits ---
asm_bits = [(ASM >> (31-k)) & 1 for k in range(32)]
payload_bits = rng.integers(0, 2, DATA_BITS).astype(int).tolist()
clean_bits = np.array(asm_bits + payload_bits, dtype=int)

# hard bit -> ideal soft value: bit 0 -> +AMP, bit 1 -> -AMP
def bits_to_soft(bits):
    return np.where(bits == 0, AMP, -AMP).astype(float)

clean_soft = bits_to_soft(clean_bits)

# --- QPSK 90-deg rotation on the interleaved (I,Q) SOFT pairs ---
# Hard map rot1: (I,Q)->(Q,1-I). On soft values, 1-x (bit flip) = negate the soft value:
#   rot1: (si,sq) -> (sq, -si)
def rot90_soft(soft):
    out = soft.copy()
    I = out[0::2].copy(); Q = out[1::2].copy()
    out[0::2] = Q
    out[1::2] = -I
    return out

rot_soft_clean = clean_soft.copy()
for _ in range(ROT):
    rot_soft_clean = rot90_soft(rot_soft_clean)

# lead-in + trailing soft garbage (random sign, full-ish amplitude)
lead  = (rng.integers(0, 2, LEAD)*2 - 1).astype(float) * AMP
trail = (rng.integers(0, 2, 12)*2 - 1).astype(float) * AMP
stream = np.concatenate([lead, rot_soft_clean, trail])

# add AWGN, then clamp to int8
stream = stream + rng.normal(0.0, NOISE, stream.shape)
stream = np.clip(np.rint(stream), -128, 127).astype(np.int8)
stream.tofile('/tmp/framesync_in.s8')

# --- ground truth ---
# The decoder reports the DE-rotation it applies to recover truth = inverse of the forward
# rotation injected by the channel: derot = (4 - ROT) % 4.
derot = (4 - ROT) % 4

# The de-rotated payload SOFT values the decoder should emit: take the EMITTED stream
# region (after lead+ASM), de-rotate it by `derot` applied to the WHOLE frame from offset,
# and read off the post-ASM soft values. Easiest: recompute what rot_soft(derot) gives.
# The decoder de-rotates the in-stream (already rotated by ROT) by derot -> back to clean.
# We compute the EXACT int8 soft values the decoder reads + de-rotates, so the check is
# bit-exact against what the rail module emits (no AWGN mismatch).
nbits_emit = min(DATA_BITS, stream.size - (LEAD + 32))

def soft_at_int8(arr, idx):
    return int(arr[idx])

# replicate rail rot_soft on the int8 stream for the emitted region
def derotate_emit(stream_i8, dataoff, r, nbits):
    out = []
    for k in range(nbits):
        pair = k // 2; inpair = k % 2
        si = soft_at_int8(stream_i8, dataoff + pair*2)
        sq = soft_at_int8(stream_i8, dataoff + pair*2 + 1)
        if inpair == 0:
            v = (si if r==0 else (sq if r==1 else (-si if r==2 else -sq)))
        else:
            v = (sq if r==0 else (-si if r==1 else (-sq if r==2 else si)))
        out.append(int(v))
    return out

dataoff = LEAD + 32
expected_soft = derotate_emit(stream, dataoff, derot, nbits_emit)

# also record the implied de-rotated payload HARD bits, for a sanity comparison
expected_hard = [0 if v >= 0 else 1 for v in expected_soft]

gt = {"asm_hex": f"{ASM:08x}", "offset": int(LEAD),
      "rotation": int(derot), "forward_rotation": int(ROT),
      "expected_soft": expected_soft, "expected_hard": expected_hard,
      "payload_hard_clean": payload_bits[:nbits_emit],
      "data_bits": int(nbits_emit), "total_soft": int(stream.size)}
json.dump(gt, open('/tmp/framesync_truth.json', 'w'))
print(f"ASM 0x{ASM:08X} as SOFT at bit offset {LEAD}, forward rot {ROT}*90, "
      f"derot {derot}, amp {AMP}, noise sigma {NOISE}, {nbits_emit} emitted soft bits, "
      f"{stream.size} total soft samples")
