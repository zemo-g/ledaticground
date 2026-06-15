#!/bin/bash
# fetch_beacon_pulse.sh — THE single beacon-pulse fetcher every v=2 roll-up driver sources.
#
# Implements RECEIPT_CONTRACT.md section (C). Rail's shell() does NOT inherit env vars or
# PATH and the framed entropy endpoint is heavy for Rail's TLS path, so the SHELL wrapper
# fetches the live beacon pulse and stages it to two /tmp files the Rail signer reads via
# the existing `field_or` idiom:
#
#   line 1  pulse_id  -> /tmp/lg_pulse_id.txt   (7-digit int when online)
#   line 2  pulse_hex -> /tmp/lg_pulse_hex.txt  (first 16 hex chars of value_hex)
#
# HONEST FALLBACK (operator standing rule — attestation-chain-as-time):
#   On ANY failure (curl error, empty body, non-JSON, timeout, malformed pulse_id) it
#   writes the honest PENDING fallback and EXITS 0. An unreachable beacon must NOT block
#   attestation: a roll-up STILL signs+chains with the honest PENDING pulse (the hash-chain
#   is the local clock; the beacon is the external anchor only when reachable).
#     /tmp/lg_pulse_id.txt  = "PENDING_beacon_unreachable"
#     /tmp/lg_pulse_hex.txt = "PENDING"
#   NEVER pulse_id=0. NEVER wall-clock as the attestation clock.
#
# Idempotent + safe to either `source` or exec:
#   source scripts/fetch_beacon_pulse.sh    # stages the files, returns 0, no subshell exit
#   bash   scripts/fetch_beacon_pulse.sh    # stages the files, exits 0
#
# chmod note: `chmod +x scripts/fetch_beacon_pulse.sh` is OPTIONAL — roll-up drivers
# invoke it as `source` or `bash <path>`, neither of which needs the execute bit.

# set -u catches unset-var typos; we deliberately do NOT use `set -e` (a failing curl in a
# pipeline must fall through to the honest PENDING fallback, not abort the driver that
# sourced us). Every failure path is guarded explicitly.
set -u

# Wrap the whole thing in a function so `source` cannot leak `return`/`exit` semantics and
# so local vars don't pollute a sourcing driver's namespace.
_lg_fetch_beacon_pulse() {
    local PULSE_ID_FILE="/tmp/lg_pulse_id.txt"
    local PULSE_HEX_FILE="/tmp/lg_pulse_hex.txt"
    local PY="/opt/homebrew/bin/python3.11"
    local URL="https://ledatic.org/entropy/pulse"

    # Honest fallback writer — used on every failure path. pulse_id is the contract literal
    # PENDING_beacon_unreachable; pulse_hex is "PENDING" (a hex string, never raw NUL).
    _lg_write_pending() {
        printf '%s\n' "PENDING_beacon_unreachable" > "$PULSE_ID_FILE" 2>/dev/null
        printf '%s\n' "PENDING"                     > "$PULSE_HEX_FILE" 2>/dev/null
    }

    # Python interpreter must exist (file-based config; do not assume PATH).
    if [ ! -x "$PY" ]; then
        _lg_write_pending
        return 0
    fi

    # Fetch + parse in one pipeline. The python one-liner prints pulse_id on line 1 and the
    # first 16 hex chars of value_hex on line 2; it raises (non-zero exit, empty stdout) on
    # empty body / non-JSON / missing keys. --max-time 6 bounds curl. We capture stdout and
    # the pipeline exit status WITHOUT `set -e` aborting us.
    local OUT
    local RC
    OUT="$(curl -s --max-time 6 "$URL" 2>/dev/null | \
        "$PY" -c "import sys,json;d=json.load(sys.stdin);print(d['pulse_id']);print(d['value_hex'][:16])" 2>/dev/null)"
    RC=$?

    # PIPESTATUS would distinguish curl vs python, but for the honest-fallback contract any
    # non-zero / empty result is identical: write PENDING. Guard the pipeline result.
    if [ "$RC" -ne 0 ]; then
        _lg_write_pending
        return 0
    fi
    if [ -z "$OUT" ]; then
        _lg_write_pending
        return 0
    fi

    # Split the two lines (newline is the one legal single-char split). head/tail are POSIX.
    local PID
    local PHEX
    PID="$(printf '%s\n' "$OUT"  | sed -n '1p')"
    PHEX="$(printf '%s\n' "$OUT" | sed -n '2p')"

    # Validate pulse_id: must be a non-empty all-digits integer (the live beacon emits a
    # 7-digit int). Reject anything else to the honest PENDING fallback — never write a
    # half-parsed / garbage / 0 pulse_id.
    case "$PID" in
        ''|*[!0-9]*)
            _lg_write_pending
            return 0
            ;;
    esac

    # Validate pulse_hex: 16 lowercase/uppercase hex chars. If the endpoint shape drifts,
    # keep the (valid) integer pulse_id but stage a PENDING hex rather than garbage.
    case "$PHEX" in
        [0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F][0-9a-fA-F])
            :
            ;;
        *)
            PHEX="PENDING"
            ;;
    esac

    # Stage the validated values. Both writes guarded; a write failure falls back to PENDING.
    if ! printf '%s\n' "$PID" > "$PULSE_ID_FILE" 2>/dev/null; then
        _lg_write_pending
        return 0
    fi
    if ! printf '%s\n' "$PHEX" > "$PULSE_HEX_FILE" 2>/dev/null; then
        _lg_write_pending
        return 0
    fi

    return 0
}

# Run it. Works identically whether sourced or exec'd: the function always returns 0.
_lg_fetch_beacon_pulse
