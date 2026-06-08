#!/usr/bin/env python3
# beacon_por generator: stage the SYNTHETIC beacon-detection facts that the
# proof-of-reception receipt (src/beacon_por.rail) binds + signs. Writes each receipt
# input to /tmp as a plain text field so the Rail side reads them as strings (no
# float-user-fn boundary crossings -- avoids the multi-arg-float-param segfault trap):
#   /tmp/beacon_por_freq.txt        -> absolute beacon freq (MHz)  {freq}
#   /tmp/beacon_por_snr.txt         -> detector SNR (dB)           {snr}
#   /tmp/beacon_por_pulse.txt       -> capture pulse id (entropy-beacon stand-in) {pulse}
#   /tmp/beacon_por_payload_hex.txt -> de-framed payload bytes as a HEX STRING {payload-hash src}
# Ground truth (the sha256 the Rail side should compute over the payload hex) ->
#   /tmp/beacon_por_truth.txt
#
# HONESTY: this is a TEST receipt over a SYNTHETIC detection. The freq/snr/payload are a
# synthetic test vector, NOT a real reception. The receipt is force-labeled SYNTHETIC_TEST
# so it can never be mistaken for a real attestation.
import sys, hashlib

def args(n, d):
    return sys.argv[sys.argv.index(n) + 1] if n in sys.argv else d

# Synthetic detection facts (a beacon in the 137-138 MHz CubeSat band).
freq  = args('--freq',  '137.625000')        # MHz, a synthetic carrier estimate
snr   = args('--snr',   '42.7')              # dB,  a synthetic detector SNR
pulse = args('--pulse', 'SYNTH_POR_PULSE_0001')

# Synthetic de-framed payload. Includes a 0x00 byte ON PURPOSE: the hex-string carriage
# is exactly what protects against the char_from_int 0 NUL-drop trap -- if the Rail side
# ever hashed raw bytes instead of the hex, that 0x00 would silently vanish and the
# cross-check below would catch it.
payload = bytes.fromhex('00ff') + b'LEDATICGROUND BEACON SYNTH POR'

# Represent the payload as a lowercase hex STRING. BOTH sides sha256 this hex string
# (not the raw bytes) so the cross-check is exact and NUL-safe.
payload_hex = payload.hex()

open('/tmp/beacon_por_freq.txt', 'w').write(freq + '\n')
open('/tmp/beacon_por_snr.txt', 'w').write(snr + '\n')
open('/tmp/beacon_por_pulse.txt', 'w').write(pulse + '\n')
open('/tmp/beacon_por_payload_hex.txt', 'w').write(payload_hex)   # NO trailing newline

# ground-truth: sha256 of the payload HEX STRING (what the Rail side will hash)
payload_sha = hashlib.sha256(payload_hex.encode()).hexdigest()
open('/tmp/beacon_por_truth.txt', 'w').write(payload_sha + '\n')

print('staged SYNTHETIC beacon detection facts for beacon_por:')
print(f'  freq={freq}MHz  snr={snr}dB  pulse={pulse}  payload={len(payload)}B')
print(f'  payload_hex={payload_hex}')
print(f'  payload_hex_sha256={payload_sha}')
print('  -> /tmp/beacon_por_{freq,snr,pulse}.txt + /tmp/beacon_por_payload_hex.txt')
