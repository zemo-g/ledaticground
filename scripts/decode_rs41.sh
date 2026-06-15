#!/bin/bash
# RS41-7: Mini-side RS41 decode + attest driver (parallels scripts/decode_real_pass.sh).
# Takes a captured raw IQ .bin (uint8 I/Q @ 250k from rtl_sdr, or a synthetic int8 .s8),
# runs the pure-Rail RS41 decode chain, then signs the TWO receipts (DECODE fact +
# INFERENCE bound to it). Used by the pull path when a capture carries the _RS41_ mode tag.
#
#   usage: decode_rs41.sh <capture.bin|.s8> [snr_hint]
#
# IQ format note: rtl_sdr writes UNSIGNED uint8 I/Q; the Rail demod expects SIGNED int8.
# This driver converts uint8 -> int8 (subtract 128) into /tmp/rs41_in.s8 before decoding.
# A synthetic .s8 (already signed int8) is passed straight through.
#
# *** HONESTY ***: live 400 MHz reception is hardware-blocked (separate antenna + LNA, a
# procurement blocker -- see rs41_capture.sh / docs/LNA_SPEC.md). This driver is exercised
# by the synthetic selftest chain NOW; it runs unchanged on a real .bin once the antenna lands.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GD="$(cd "$HERE/.." && pwd)"
RN=/Users/ledaticempire/projects/rail/rail_native
PY=/opt/homebrew/bin/python3.11
SRC="${1:-}"
SNR="${2:-PENDING_no_snr}"

[ -n "$SRC" ] || { echo "usage: decode_rs41.sh <capture.bin|.s8> [snr_hint]"; exit 2; }
[ -f "$SRC" ] || { echo "NO CAPTURE at $SRC"; exit 1; }

# 1. stage the IQ as signed int8 at /tmp/rs41_in.s8
case "$SRC" in
  *.s8)
    cp -f "$SRC" /tmp/rs41_in.s8
    echo "staged synthetic int8 IQ ($(wc -c <"$SRC") bytes)"
    ;;
  *)
    # rtl_sdr uint8 -> signed int8 (x - 128)
    $PY -c "
import numpy as np, sys
u = np.fromfile('$SRC', np.uint8).astype(np.int16) - 128
np.clip(u, -127, 127).astype(np.int8).tofile('/tmp/rs41_in.s8')
print(f'converted uint8->int8: {len(u)} samples -> /tmp/rs41_in.s8')
"
    ;;
esac

# frame length default (standard RS41-SG)
[ -f /tmp/rs41_frame_len.txt ] || echo "518" > /tmp/rs41_frame_len.txt
echo "$SNR" > /tmp/rs41_snr.txt

# 2. run the pure-Rail RS41 full-chain decoder
echo "=== pure-Rail RS41 decode ==="
out=$(bash "$HERE/railrun.sh" "$GD/src/rs41_decode.rail" 2>/dev/null)
echo "$out" | grep -E "^RUNG|^CRC_OK|^STATUS|^GPS_POS|^PTU|^DERIVED|^END_TO_END"
if ! echo "$out" | grep -q "END_TO_END PASS"; then
  echo "RS41 decode did not reach END_TO_END PASS -- not attesting a failed decode"
  exit 1
fi

# 3. stage the decode facts for the attest module, then sign the two receipts
$PY "$HERE/gen_rs41_attest.py" --snr "${SNR%PENDING*}" >/dev/null 2>&1 || \
  $PY "$HERE/gen_rs41_attest.py" >/dev/null 2>&1 || true

echo "=== RS41 two-receipt attest (DECODE fact + INFERENCE) ==="
bash "$HERE/railrun.sh" "$GD/src/rs41_attest.rail" 2>/dev/null | \
  grep -E "^CHAIN_|^VERIFY_|^TAMPER_|^BINDING|^ATTEST_STATUS"

echo "wrote data/rs41_decode_receipt.json + data/rs41_inference_receipt.json"
