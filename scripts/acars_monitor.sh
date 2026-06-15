#!/bin/bash
# ledaticground ACARS fixed-channel monitor (IS-1) — the DEPLOY ARTIFACT.
#
# ACARS is a FIXED-CHANNEL monitor: aircraft are always overhead, so there is NO
# pass prediction (unlike LRPT/Orbcomm). This loop round-robins the 4 most-active
# in-band ACARS VHF channels, captures a short raw-IQ burst on the Pi, channelizes
# off-radio to /tmp/acars_in.s8 (scripts/pi_acars_capture.py), runs the committed
# pure-Rail decoder src/acars_decode.rail UNMODIFIED via scripts/railrun.sh, and
# appends ONE JSON line to data/acars_log.jsonl per CRC-verified (BCS_OK==1) block.
#
# THE FACT/INFERENCE WALL (docs/CHANNEL_INTELLIGENCE.md sec1): a BCS_OK==1 block is
# an attested FACT (every char + the CRC verified). A copy with parity_errs>0 and
# bcs_ok==0 is a FAILED copy and is NOT logged as a decode. The monitor keys its
# verdict on BCS_OK==1 (the CRC = the FACT), NOT on the decoder's SELFCHECK PASS line
# (which is synthetic-only and correctly prints "SELFCHECK SKIPPED" on real RF —
# src/acars_decode.rail line 344).
#
# RADIO CONTENTION (ONE SDR, program-wide): roofmon (AIS) is the always-on MAIN
# system. ACARS gets SHORT on-demand preempt windows and ALWAYS calls resume_ais on
# EVERY exit path (the pass_scheduler.sh discipline), with the Pi-side deadman as the
# independent backstop. ACARS is NOT pass-gated, so it does NOT ride pull_iq.
#
# USAGE — does NOT auto-start capture when sourced or when given a subcommand:
#   acars_monitor.sh                       # loop forever (deploy as a LaunchAgent)
#   acars_monitor.sh --once                # one round-robin sweep of all channels, exit
#   acars_monitor.sh --dry-run <file.s8>   # OFFLINE: decode an EXISTING .s8, log if BCS_OK,
#                                          #   resume_ais NOT invoked (no radio touched).
#                                          #   This is the synthetic-validation entry point.
#
# HONEST SCOPE NOW vs LNA: ACARS is terrestrial vertically polarized; the horizontal
# halo costs 15-20 dB cross-pol + the ~8 dB no-LNA deficit, so live copy will be
# rare/marginal NOW. The plumbing is complete + synthetic-validatable + runnable on
# the live wire today; reliable continuous copy is LNA-gated (IS-5), not code-gated.
set -u
GD=/Users/ledaticempire/projects/ledaticground
PI_HOST=${PI_HOST:-100.115.30.12}; PI_USER=${PI_USER:-ledatic}    # roofv2
PI="${PI_USER}@${PI_HOST}"
SSH="ssh -o ConnectTimeout=10 -o BatchMode=yes"
PY=/opt/homebrew/bin/python3.11
RAILRUN="$GD/scripts/railrun.sh"
DECODER="$GD/src/acars_decode.rail"
FRONTEND="$GD/scripts/pi_acars_capture.py"
LOG="$GD/data/acars_log.jsonl"
MON=${ACARS_MON_LOG:-/tmp/acars_monitor.log}
SECS=${ACARS_SECS:-10}             # short capture per channel — ACARS bursts are brief/frequent
FS=${ACARS_FS:-250000}             # raw-IQ capture sample rate (program-wide cu8 rate)
GAIN=${ACARS_GAIN:-49}             # max sensitivity (no-LNA baseline)
CYCLE=${ACARS_CYCLE:-30}           # seconds between full round-robin sweeps

# canonical in-band ACARS VHF channels (Hz), primary first
CHANNELS=(136975000 136700000 136800000 136850000)

log(){ echo "$(date -u +%FT%TZ) $*" >> "$MON"; }

# ATTEST HOOK (IS-2) — append-only, OFF the decode path, guarded so a signer failure NEVER
# kills the monitor. After a decode batch the shared in-band roll-up (scripts/attest_inband_
# rollup.sh, AC-2) is its OWN idempotent reader: it reads the decode PRODUCT, fetches the
# beacon pulse, drives src/acars_attest.rail via railrun, and appends ONE chained line to
# data/acars_receipts.jsonl. It does nothing on an unchanged/empty product (honest-empty,
# idempotent), so calling it after every sweep is safe. This monitor NEVER touches the raw
# capture/decode path or the ledgers directly — the roll-up owns all attestation.
ROLLUP="$GD/scripts/attest_inband_rollup.sh"
attest_rollup(){    # fire-and-forget; || true means a signer failure cannot abort the loop
  [ -f "$ROLLUP" ] || { log "attest: roll-up driver missing ($ROLLUP) — skipped"; return 0; }
  log "attest: driving in-band roll-up (append-only, idempotent)"
  bash "$ROLLUP" >> "$MON" 2>&1 || log "attest: roll-up returned nonzero (guarded; loop continues)"
  return 0
}

resume_ais(){           # ALWAYS bring the MAIN system (AIS) back; retry hard.
  local a
  for i in 1 2 3 4 5; do
    $SSH "$PI" "sudo systemctl start roofmon.service" 2>/dev/null
    a=$($SSH "$PI" "systemctl is-active roofmon.service" 2>/dev/null)
    if [ "$a" = "active" ]; then log "AIS main system resumed (roofmon active)"; return 0; fi
    sleep 5
  done
  log "!! WARNING: roofmon not confirmed active — Pi-side deadman should recover it"
  return 1
}

# decode the .s8 already at /tmp/acars_in.s8; if BCS_OK==1, append ONE JSON line.
# $1 = channel Hz (for the log line). Returns 0 if a FACT was logged, 1 otherwise.
# Keyed on BCS_OK==1 (the CRC = the FACT), never on SELFCHECK PASS.
decode_and_log(){
  local ch="$1" out bcs perr mode reg label blkid text ts
  out=$(bash "$RAILRUN" "$DECODER" 30 2>/dev/null)
  bcs=$(printf '%s\n' "$out" | sed -n 's/^BCS_OK \([0-9]*\).*/\1/p' | head -1)
  perr=$(printf '%s\n' "$out" | sed -n 's/^PARITY_ERRORS \([0-9]*\).*/\1/p' | head -1)
  bcs=${bcs:-0}; perr=${perr:-}
  if [ "$bcs" != "1" ]; then
    log "ch${ch} no FACT (bcs_ok=${bcs} parity_errs=${perr:-na}) — not logged (honest-empty)"
    return 1
  fi
  # CRC verified — this is an attested FACT. Extract the fixed-width fields.
  mode=$(printf '%s\n'  "$out" | sed -n 's/^MODE \(.*\)/\1/p'  | head -1)
  reg=$(printf '%s\n'   "$out" | sed -n 's/^REG \(.*\)/\1/p'   | head -1)
  label=$(printf '%s\n' "$out" | sed -n 's/^LABEL \(.*\)/\1/p' | head -1)
  blkid=$(printf '%s\n' "$out" | sed -n 's/^BLKID \(.*\)/\1/p' | head -1)
  text=$(printf '%s\n'  "$out" | sed -n 's/^TEXT \(.*\)/\1/p'  | head -1)
  ts=$(date +%s)
  # JSON via python (canonical escaping; one physical line; space-after-colon for
  # the existing verify.rail field_val pattern compatibility downstream).
  printf '%s\n' "$out" | "$PY" -c '
import sys, json
out = sys.stdin.read()
ch = sys.argv[1]; ts = int(sys.argv[2])
f = {}
for ln in out.splitlines():
    if " " in ln:
        k, _, v = ln.partition(" ")
        f[k] = v
rec = {
    "ts": ts, "ch": int(ch), "bcs_ok": 1,
    "parity_errs": int(f.get("PARITY_ERRORS", "0") or 0),
    "mode": f.get("MODE", ""), "reg": f.get("REG", ""),
    "label": f.get("LABEL", ""), "blkid": f.get("BLKID", ""),
    "text": f.get("TEXT", ""),
}
print(json.dumps(rec, ensure_ascii=True))
' "$ch" "$ts" >> "$LOG"
  log "ch${ch} FACT bcs_ok=1 parity_errs=${perr:-0} reg=${reg:-?} label=${label:-?} -> data/acars_log.jsonl"
  return 0
}

# capture one channel on the Pi (raw IQ), pull, channelize off-radio, decode+log.
# $1 = channel Hz. ALWAYS frees the radio via resume_ais before returning.
capture_channel(){
  local ch="$1" pf loc sz
  pf="/home/ledatic/acars_${ch}.cu8"
  log "ACARS WINDOW ch${ch} — short preempt of AIS (${SECS}s @ g${GAIN})"
  if ! $SSH "$PI" "sudo systemctl stop roofmon.service"; then
    log "could not stop roofmon; abort ch${ch}"; resume_ais; return 1
  fi
  sleep 1
  # raw uint8 I/Q; timeout-bounded so the SDR ALWAYS frees even if rtl_sdr hangs.
  if ! $SSH "$PI" "nohup timeout -k 5 $((SECS+3)) rtl_sdr -f $ch -s $FS -g $GAIN '$pf' >/tmp/acarsrec.log 2>&1 &"; then
    log "Pi ACARS capture trigger failed ch${ch}"; resume_ais; return 1
  fi
  sleep $((SECS + 5))
  resume_ais            # free the radio + restore AIS ASAP; channelize+decode are off-radio
  sz=$($SSH "$PI" "stat -c%s '$pf' 2>/dev/null" || echo 0); sz=${sz:-0}
  if [ "$sz" -lt 100000 ]; then
    log "ch${ch} capture too small (${sz}B) — nothing to decode"; $SSH "$PI" "rm -f '$pf'" 2>/dev/null; return 1
  fi
  loc="/tmp/acars_${ch}.cu8"
  if ! scp -C "$PI:$pf" "$loc" 2>/dev/null; then
    log "ch${ch} scp failed (cu8 left on Pi at $pf)"; return 1
  fi
  $SSH "$PI" "rm -f '$pf'" 2>/dev/null
  # channelize cu8 -> /tmp/acars_in.s8 (the decoder's input contract)
  "$PY" "$FRONTEND" "$loc" --chan-hz "$ch" --center-hz "$ch" --fs "$FS" >/dev/null 2>&1 || {
    log "ch${ch} front-end channelize failed"; return 1; }
  decode_and_log "$ch"
}

# one round-robin sweep over all channels (each is a short preempt window)
sweep_once(){
  local ch
  for ch in "${CHANNELS[@]}"; do
    capture_channel "$ch"
  done
}

main(){
  log "acars_monitor START — fixed-channel ${#CHANNELS[@]}-ch round-robin, ${SECS}s/ch, cycle ${CYCLE}s"
  case "${1:-}" in
    --dry-run)
      # OFFLINE entry point: decode an EXISTING .s8, log if BCS_OK. No radio, no ssh.
      local f="${2:-/tmp/acars_in.s8}" ch="${3:-136975000}"
      if [ ! -f "$f" ]; then echo "dry-run: no such .s8: $f" >&2; exit 2; fi
      cp "$f" /tmp/acars_in.s8
      log "DRY-RUN decode of $f as ch${ch} (no radio touched, resume_ais NOT called)"
      if decode_and_log "$ch"; then echo "DRY-RUN: FACT logged"; attest_rollup; else echo "DRY-RUN: no FACT (honest-empty, nothing logged)"; fi
      ;;
    --once)
      sweep_once
      resume_ais       # belt-and-suspenders: leave the radio with AIS on exit
      attest_rollup    # attest the batch off-radio (append-only, idempotent, guarded)
      ;;
    *)
      while true; do
        sweep_once
        attest_rollup  # attest after each sweep (append-only, idempotent, guarded)
        sleep "$CYCLE"
      done
      ;;
  esac
}

# Only run when executed directly — sourcing this file must NOT start anything.
# `(return 0 2>/dev/null)` succeeds ONLY in a sourced context (return is illegal in a
# top-level executed script), so this is the robust source-vs-exec test across bash
# invocations (BASH_SOURCE / $0 heuristics misfire when the caller unsets BASH_SOURCE).
if ! (return 0 2>/dev/null); then
  main "$@"
fi
