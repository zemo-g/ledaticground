#!/bin/bash
# attest_lrpt_decode_all.sh -- scan the local raw-IQ dir and attest EVERY decoded LRPT capture
# that is not already in the lrpt_decode ledger. The per-bin driver
# (attest_lrpt_decode_rollup.sh) is idempotent (ledger-scan no-op), so re-running this is safe
# and cheap -- it signs only NEW decodes.
#
# This is the live-pipeline entry point: refresh.sh calls it once per cycle, guarded with
# `|| true`, exactly as it calls attest_ais_rollup.sh. It is OFF the raw capture/decode path
# (it only reads the already-written <bin> + <bin>.satdump/*.cadu products) and writes ONLY the
# lrpt_decode ledger family. A failure here can NEVER break the pull/decode/AIS pipeline.
#
# A capture is attestable iff: basename matches iq_*_LRPT_*.bin AND a sibling .satdump/*.cadu
# exists (the live decode ran). 0-CADU passes ARE attested (cadu_ok=0) -- "received, no lock" is
# a fact -- as long as the satdump dir exists (proof the decode was attempted), so we gate on the
# satdump DIR, not on a non-empty .cadu.
#
# Usage:  bash scripts/attest_lrpt_decode_all.sh [raw_iq_dir]
#   default raw_iq_dir = ~/.ledatic/roofv2/raw_iq
#
# bash-3.2 / macOS safe. set -u, no set -e (one bad capture must not abort the sweep).
set -u

REPO="/Users/ledaticempire/projects/ledaticground"
DRIVER="$REPO/scripts/attest_lrpt_decode_rollup.sh"
RAWDIR="${1:-$HOME/.ledatic/roofv2/raw_iq}"

if [ ! -f "$DRIVER" ]; then
    echo "LRPTALL_ERR: per-bin driver missing: $DRIVER" >&2
    exit 2
fi
if [ ! -d "$RAWDIR" ]; then
    echo "LRPTALL: raw_iq dir absent ($RAWDIR) -- nothing to attest (exit 0)"
    exit 0
fi

signed=0
skipped=0
errs=0
for bin in "$RAWDIR"/iq_*_LRPT_*.bin; do
    [ -f "$bin" ] || continue          # guards the literal-glob case (no matches)
    satdir="${bin%.bin}.satdump"
    if [ ! -d "$satdir" ]; then
        # decode has not run yet for this capture -- leave it for a later cycle.
        skipped=$((skipped+1))
        continue
    fi
    # Mint the raw-CAPTURE FACT first so the capture<->decode correspondence is POPULATED: the
    # iq_capture FACT's product_sha256 will equal this decode FACT's input_sha256 (both = sha256
    # of the same .bin). Without it the correspondence the decode receipt documents resolves to
    # nothing for real captures. GUARD with a ledger-scan: iq_capture's idempotency is only a
    # single "last-signed" cursor, so calling it unconditionally every sweep would re-append a
    # duplicate capture line each cycle (bloat). We sign only when this bin's digest is NOT
    # already an iq_capture product_sha256. Guarded so a capture-attest failure can't block decode.
    binsha="$(shasum -a 256 "$bin" 2>/dev/null | awk '{print $1}')"
    if [ "${#binsha}" -eq 64 ] && ! grep -Fq "product_sha256=$binsha" "$REPO/data/iq_capture_receipts.jsonl" 2>/dev/null; then
        bash "$REPO/scripts/attest_iq_capture_rollup.sh" "$bin" >/dev/null 2>&1 || true
    fi
    out="$(bash "$DRIVER" "$bin" 2>&1)"
    rc=$?
    if [ "$rc" -ne 0 ]; then
        echo "LRPTALL_ERR rc=$rc on $(basename "$bin"): $(printf '%s' "$out" | tail -1)" >&2
        errs=$((errs+1))
    elif printf '%s' "$out" | grep -q "idempotent no-op"; then
        skipped=$((skipped+1))
    else
        signed=$((signed+1))
        echo "LRPTALL: signed $(basename "$bin")  $(printf '%s' "$out" | grep -oE 'cadus=[0-9]+ +cadu_ok=[01]' | head -1)"
    fi
done

echo "LRPTALL: done -- signed=$signed skipped/already=$skipped errors=$errs (dir=$RAWDIR)"
exit 0
