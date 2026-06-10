#!/bin/bash
# External-validation cross-check (Reilly's raw-IQ-first thesis): run satdump — the
# community reference decoder — on one of OUR raw-IQ captures and report products.
# "Outputs match" between this and our iq_apt_decode.py is the independent proof the
# pipeline is real. Because the raw IQ is saved, this is re-runnable forever.
#
#   validate_external.sh <raw_iq.bin> [apt|lrpt]      # mode auto-detected from filename if omitted
#
# Our captures are rtl_sdr raw uint8 I/Q @ 250k centered on the downlink => satdump
# baseband_format cu8. satdump must run from inside the .app bundle so it self-locates
# ../Resources (the brew symlink looks in /usr/local/share and fails).
set -u
SAT_BIN="/Applications/SatDump.app/Contents/MacOS/satdump"
BIN="${1:?usage: validate_external.sh <raw_iq.bin> [apt|lrpt]}"
[ -f "$BIN" ] || { echo "no such file: $BIN"; exit 1; }
[ -x "$SAT_BIN" ] || { echo "satdump not found at $SAT_BIN (brew install --cask satdump)"; exit 1; }

MODE="${2:-auto}"
if [ "$MODE" = auto ]; then
  case "$BIN" in *_LRPT_*|*lrpt*|*LRPT*) MODE=lrpt;; *) MODE=apt;; esac
fi
case "$MODE" in
  apt)  PIPE=noaa_apt;;
  lrpt) PIPE=meteor_m2-x_lrpt;;
  *)    echo "mode must be apt|lrpt"; exit 1;;
esac

OUT="${BIN%.bin}.satdump"; rm -rf "$OUT"; mkdir -p "$OUT"
echo "satdump[$PIPE] on $(basename "$BIN") -> $OUT/"
"$SAT_BIN" "$PIPE" baseband "$BIN" "$OUT" --samplerate 250000 --baseband_format cu8 > "${OUT}.log" 2>&1
rc=$?
echo "exit=$rc | products:"
ls -1 "$OUT" 2>/dev/null | grep -iE '\.(png|json|cbor)$' | sed 's/^/  /'
# LRPT: the CADU count is the AUTHORITATIVE verdict (1 CADU = 1024 B of deframed,
# Viterbi-locked downlink). Deterministic — no waterfall heuristic can substitute:
# 2026-06-10 the "FLAT NOISE" APT discriminator mislabeled a 1023-CADU M2-3 pass.
if [ "$MODE" = lrpt ]; then
  cb=$(stat -f %z "$OUT/meteor_m2-x_lrpt.cadu" 2>/dev/null || stat -c %s "$OUT/meteor_m2-x_lrpt.cadu" 2>/dev/null || echo 0)
  echo "CADUS=$(( cb / 1024 )) cadu_bytes=$cb"
fi
# signal indicators from satdump's own log: LRPT prints Viterbi/frames/lock; APT just renders (eyeball the PNG).
grep -iE "frames|deframer|valid|Viterbi|BER|lock|SNR|correlat" "${OUT}.log" 2>/dev/null | tail -6 | sed 's/^/  log: /'
echo "  verdict: inspect $OUT/*.png — Earth/cloud structure = SIGNAL, uniform speckle = NOISE (full log: ${OUT}.log)"
