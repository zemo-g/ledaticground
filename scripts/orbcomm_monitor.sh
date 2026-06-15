#!/bin/bash
# ledaticground ORBCOMM pass scheduler/capture orchestrator (IS-3) — the DEPLOY ARTIFACT.
#
# Unlike fixed-channel ACARS, Orbcomm is a LEO constellation (~30+ active sats, ~775 km,
# ~99 min orbit), so it needs pass prediction EXACTLY like LRPT. This is its OWN scheduler
# (NOT mixed into the LRPT --all set: the FM-audio APT path can't touch Orbcomm and the
# raw-IQ path needs Orbcomm-specific characterization). It reuses pass_scheduler.sh's
# capture_iq_pass mechanics: stop roofmon -> nohup rtl_sdr raw cu8 IQ at the Orbcomm
# channel -> resume_ais ALWAYS on every exit path -> pull + characterize off-radio.
#
# THE HONEST DECODE: on each pulled capture it runs scripts/iq_to_orbcomm_char.py
# (cu8 -> 38.4k int8 IQ) then src/orbcomm_char.rail via railrun (proof-of-RECEPTION:
# carrier_present/offset/SNR/symrate + PAYLOAD=NONE). It does NOT run orbcomm_decode
# (proprietary payload — would imply a content decode we cannot honestly make).
# carrier_present 0 or 1 are BOTH valid honest outcomes. The Doppler-fingerprint
# cross-check + the chained proof-of-reception receipt are IS-5/IS-6 (the pull-sweep
# decode branch wires them); this monitor owns the CAPTURE half + the CHAR verdict.
#
# RADIO CONTENTION (ONE SDR, program-wide): roofmon (AIS) is the always-on MAIN system.
# Orbcomm uses SCHEDULED pass-window preempts and ALWAYS calls resume_ais on EVERY exit
# path (the pass_scheduler.sh discipline), with the Pi-side deadman as backstop. Capture
# budget: 1 best-elevation Orbcomm pass per window (recommend ~1 per 2h so it doesn't
# starve LRPT/AIS — set ORBCOMM_COOLDOWN).
#
# USAGE — does NOT auto-start capture when sourced:
#   orbcomm_monitor.sh            # loop: capture the next best Orbcomm pass each cycle
#   orbcomm_monitor.sh --once     # capture the single next best Orbcomm pass, then exit
#   orbcomm_monitor.sh --plan     # OFFLINE: print the next best pass (next_pass.py) + exit,
#                                 #   touches no radio. The schedule sanity-check entry point.
#
# NOW vs LNA: a strong overhead pass may clear the 3 dB floor on the bare halo, but the
# ~8 dB no-LNA deficit + cross-pol means many passes read carrier_present=0 (honest).
# The plumbing is complete + synthetic-validated now; reliable live reception is LNA-gated.
set -u
GD=/Users/ledaticempire/projects/ledaticground
PI_HOST=${PI_HOST:-100.115.30.12}; PI_USER=${PI_USER:-ledatic}    # roofv2
PI="${PI_USER}@${PI_HOST}"
SSH="ssh -o ConnectTimeout=10 -o BatchMode=yes"
PY=/opt/homebrew/bin/python3.11
RAILRUN="$GD/scripts/railrun.sh"
CHAR_RAIL="$GD/src/orbcomm_char.rail"
FRONTEND="$GD/scripts/iq_to_orbcomm_char.py"
RAWDIR=${RAWDIR:-/Users/ledaticempire/.ledatic/roofv2/raw_iq}     # where pulled raw-IQ artifacts land
MINEL=${MINEL:-25}            # Orbcomm carriers detectable lower than LRPT images
GAIN=${GAIN:-49}              # max sensitivity (no-LNA baseline)
FS=${ORBCOMM_FS:-250000}      # raw cu8 capture sample rate (program-wide)
ORBCOMM_COOLDOWN=${ORBCOMM_COOLDOWN:-7200}   # min seconds between captures (don't starve LRPT/AIS)
NODE_LAT=${NODE_LAT:-42.31}; NODE_LON=${NODE_LON:--83.08}   # geometry ONLY; receipt geo stays PENDING
MON=${ORBCOMM_MON_LOG:-/tmp/orbcomm_monitor.log}

log(){ echo "$(date -u +%FT%TZ) $*" | tee -a "$MON" >&2; }

# ATTEST HOOKS (IS-2/IS-5) — append-only, OFF the capture/decode path, each guarded so a
# signer failure NEVER kills the monitor. Two distinct attestations after a characterization:
#
#   (1) the shared in-band ROLL-UP (scripts/attest_inband_rollup.sh, AC-2): its OWN idempotent
#       reader — reads the decode product /tmp/orbcomm_decode_out.txt, fetches the beacon pulse,
#       drives src/orbcomm_attest.rail (the CRC-FACT ORBCOMM_RECEIPT + the FACT ORBCOMM_POR_RECEIPT,
#       payload=NONE_proprietary) via railrun, appends chained lines to data/orbcomm_receipts.jsonl.
#       No new product -> no receipt (honest-empty). Safe to call after every characterization.
#
#   (2) the DOPPLER-FINGERPRINT proof-of-reception (src/orbcomm_por_attest.rail, IS-5): a SEPARATE,
#       LABELED-INFERENCE receipt that binds the MEASURED carrier facts to the SGP4 Doppler-fit
#       kinematics (the physics-cannot-be-faked cross-check). Driven ONLY on carrier_present=1 (a
#       fingerprint needs a carrier to track) AND only when an ORBCOMM FACT root exists to bind
#       derived_from to (the signer is fail-loud: no FACT root -> it refuses to sign). It lands in
#       the SEPARATE inference ledger data/orbcomm_por_receipts.jsonl (NEVER the FACT ledger).
#
# This monitor NEVER touches the raw capture/decode path or writes a ledger directly — the
# roll-up + the POR signer own all attestation.
GD=${GD:-/Users/ledaticempire/projects/ledaticground}
ROLLUP="$GD/scripts/attest_inband_rollup.sh"
DOPPLER_SH="$GD/scripts/orbcomm_doppler.sh"
POR_RAIL="$GD/src/orbcomm_por_attest.rail"

attest_rollup(){    # (1) shared in-band FACT roll-up; || true -> a signer failure cannot abort the loop
  [ -f "$ROLLUP" ] || { log "attest: roll-up driver missing ($ROLLUP) — skipped"; return 0; }
  log "attest: driving in-band roll-up (append-only, idempotent)"
  bash "$ROLLUP" >> "$MON" 2>&1 || log "attest: roll-up returned nonzero (guarded; loop continues)"
  return 0
}

# (2) Doppler-fingerprint POR. Args: $1=CHAR-block text, $2=capture-path (cu8 raw IQ), $3=chan Hz.
# Append-only + idempotent-via-the-signer + fail-loud-no-FACT-root; every path is guarded.
attest_doppler_por(){
  local charblock="$1" capture="$2" chan="$3"
  local cp
  cp=$(printf '%s\n' "$charblock" | sed -n 's/^CHAR carrier_present=\([0-9]*\).*/\1/p' | head -1)
  cp=${cp:-0}
  if [ "$cp" != "1" ]; then
    log "POR: carrier_present=$cp — no carrier to fingerprint (honest-empty; no POR receipt)"
    return 0
  fi
  if [ ! -f "$POR_RAIL" ]; then log "POR: signer missing ($POR_RAIL) — skipped"; return 0; fi

  # FACT root for derived_from: the tail chain_hash of the ORBCOMM FACT ledger (written by the
  # AC-2 roll-up just above). Fail-loud: if absent/empty, the signer refuses (no fabricated root).
  local fact_ledger="$GD/data/orbcomm_receipts.jsonl" fc
  fc=$("$PY" - "$fact_ledger" <<'PY' 2>/dev/null
import json,sys
try:
    ls=[l for l in open(sys.argv[1]) if l.strip()]
    print(json.loads(ls[-1])["chain_hash"] if ls else "")
except Exception:
    print("")
PY
)
  case "$fc" in ''|*[!0-9a-fA-F]*) fc="" ;; esac
  if [ -z "$fc" ]; then
    log "POR: no ORBCOMM FACT chain_hash yet (roll-up signed nothing) — fail-loud, no POR receipt"
    return 0
  fi
  printf '%s\n' "$fc" > /tmp/orbcomm_fact_chain.txt

  # MEASURED FACTS staged verbatim from the CHAR block (FAIL CLOSED on any absent field).
  local off snr sym crc
  off=$(printf '%s\n' "$charblock" | sed -n 's/^CHAR offset_hz=\(.*\)/\1/p'  | head -1)
  snr=$(printf '%s\n' "$charblock" | sed -n 's/^CHAR snr_db=\(.*\)/\1/p'     | head -1)
  sym=$(printf '%s\n' "$charblock" | sed -n 's/^CHAR symrate=\(.*\)/\1/p'    | head -1)
  crc=$(printf '%s\n' "$charblock" | sed -n 's/^CHAR crc_ok=\([0-9]*\).*/\1/p' | head -1)
  printf '%s\n' "1"                        > /tmp/orbcomm_por_carrier_present.txt
  printf '%s\n' "${off:-PENDING_no_meas}"  > /tmp/orbcomm_por_offset_hz.txt
  printf '%s\n' "${snr:-PENDING_no_meas}"  > /tmp/orbcomm_por_snr_db.txt
  printf '%s\n' "${sym:-PENDING_no_meas}"  > /tmp/orbcomm_por_symrate.txt
  printf '%s\n' "${crc:-0}"                > /tmp/orbcomm_por_crc_ok.txt

  # DERIVED Doppler-fit INFERENCE: run the off-radio fingerprint (orbcomm_doppler.sh --from-iq
  # produces the centroid drift curve at /tmp/dop_meas_orbcomm.out; the SGP4 correlation is
  # doppler_fit.py --predict). Until a clean fit lands (LNA-gated), stage PENDING-honest values
  # — NEVER a fabricated correlation. The product hashed is the CHAR block + the drift curve.
  local dop_out="/tmp/dop_meas_orbcomm.out"
  if [ -f "$DOPPLER_SH" ] && [ -n "$capture" ] && [ -f "$capture" ]; then
    bash "$DOPPLER_SH" --from-iq "$capture" --chan-hz "$chan" --center-hz "$chan" --fs "$FS" >>"$MON" 2>&1 \
      || log "POR: orbcomm_doppler --from-iq returned nonzero (guarded)"
  fi
  : > /tmp/orbcomm_por_product.txt
  printf '%s\n' "$charblock" >> /tmp/orbcomm_por_product.txt
  [ -f "$dop_out" ] && grep -E '^(INFO|DOP)' "$dop_out" >> /tmp/orbcomm_por_product.txt 2>/dev/null

  # Doppler-fit kinematics — PENDING-honest unless a real correlated fit is staged elsewhere.
  # (A future LNA-gated branch will run doppler_fit.py --predict and write these; staying honest
  # now means PENDING, never a fabricated sat_id/tca/r.) Respect an externally-staged fit if present.
  [ -s /tmp/orbcomm_por_sat_id.txt ]                 || printf '%s\n' "PENDING_no_fit" > /tmp/orbcomm_por_sat_id.txt
  [ -s /tmp/orbcomm_por_tca_unix.txt ]               || printf '%s\n' "PENDING_no_fit" > /tmp/orbcomm_por_tca_unix.txt
  [ -s /tmp/orbcomm_por_fit_r.txt ]                  || printf '%s\n' "PENDING_no_fit" > /tmp/orbcomm_por_fit_r.txt
  [ -s /tmp/orbcomm_por_predicted_curve_sha256.txt ] || printf '%s\n' "PENDING_no_fit" > /tmp/orbcomm_por_predicted_curve_sha256.txt

  # batch BOUNDS + count (provenance, NOT the attestation clock) + the POR-chain prev link.
  local ts; ts=$(date +%s)
  printf '%s\n' "$ts" > /tmp/orbcomm_por_batch_start.txt
  printf '%s\n' "$ts" > /tmp/orbcomm_por_batch_end.txt
  printf '%s\n' "1"   > /tmp/orbcomm_por_batch_n.txt
  mkdir -p "$GD/data/chain"   # Rail write_file does not create parent dirs; the chain tail lives here
  if [ -s "$GD/data/chain/orbcomm_por_prev.txt" ]; then
    cp "$GD/data/chain/orbcomm_por_prev.txt" /tmp/orbcomm_por_prev_sha.txt
  else
    printf '%s\n' "GENESIS" > /tmp/orbcomm_por_prev_sha.txt
  fi

  log "POR: driving Doppler-fingerprint signer (carrier_present=1, derived_from=${fc:0:12}..)"
  bash "$RAILRUN" "$POR_RAIL" 120 >> "$MON" 2>&1 \
    || log "POR: signer returned nonzero (guarded; loop continues)"
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

# raw-IQ capture of one Orbcomm pass; ALWAYS resume_ais; pull + characterize off-radio.
# $1=sat $2=freq(Hz) $3=dur(min) $4=elev. Model on pass_scheduler.sh capture_iq_pass.
# Filename keeps the iq_<sat>_el<E>_<MODE>_<ts>.bin convention so pull_iq.sh can route it.
capture_orbcomm_pass(){
  local SAT="$1" FREQ="$2" DUR="$3" ELEV="${4:-0}"
  local RDUR=$(( ($3 + 2) * 60 )) ts label pf sz loc out
  ts=$(date -u +%Y%m%dT%H%MZ); label="iq_${SAT// /}_el${ELEV}_ORBCOMM_${ts}"; pf="/home/ledatic/${label}.bin"
  mkdir -p "$RAWDIR"
  log "ORBCOMM PASS WINDOW: $SAT El${ELEV} @${FREQ}Hz ~${DUR}min — preempting AIS for raw-IQ capture (g${GAIN})"
  if ! $SSH "$PI" "sudo systemctl stop roofmon.service"; then log "could not stop roofmon; abort"; resume_ais; return 1; fi
  sleep 1
  # raw uint8 I/Q; timeout-bounded so the SDR ALWAYS frees even if rtl_sdr hangs -> AIS can resume.
  if ! $SSH "$PI" "nohup timeout -k 10 $RDUR rtl_sdr -f $FREQ -s $FS -g $GAIN '$pf' >/tmp/orbrec.log 2>&1 &"; then
    log "Pi Orbcomm IQ capture trigger failed"; resume_ais; return 1
  fi
  log "raw-IQ recording ${RDUR}s on the Pi (~$(( RDUR / 2 ))MB @${FS})..."
  sleep $(( RDUR + 20 ))
  resume_ais            # free the radio + restore AIS ASAP; pull + characterize are off-radio
  sz=$($SSH "$PI" "stat -c%s '$pf' 2>/dev/null" || echo 0); sz=${sz:-0}
  if [ "$sz" -lt 1000000 ]; then log "Orbcomm IQ capture too small (${sz}B) — capture failed"; return 1; fi
  loc="$RAWDIR/${label}.bin"
  log "captured ${sz}B; pulling -> $(basename "$loc")"
  if ! scp -C "$PI:$pf" "$loc" 2>/dev/null; then log "scp failed (IQ left on Pi at $pf)"; return 1; fi
  $SSH "$PI" "rm -f '$pf'" 2>/dev/null
  local charblock
  charblock=$(characterize_capture "$loc" "$FREQ")
  printf '%s\n' "$charblock"   # preserve the existing stdout contract (IS-6 pull-sweep consumer)
  # ATTEST HOOKS (append-only, off-radio, each guarded so a signer failure cannot abort):
  #   (1) shared in-band FACT roll-up (CRC ORBCOMM_RECEIPT + FACT ORBCOMM_POR_RECEIPT, AC-2)
  #   (2) Doppler-fingerprint LABELED-INFERENCE POR (IS-5) — only fires on carrier_present=1.
  attest_rollup || true
  attest_doppler_por "$charblock" "$loc" "$FREQ" || true
}

# OFF-RADIO: cu8 -> 38.4k int8 IQ -> orbcomm_char.rail. Prints the CHAR verdict.
# carrier_present 0 or 1 are BOTH honest outcomes. $1=capture path $2=chan Hz.
characterize_capture(){
  local loc="$1" chan="$2" out cp
  if ! "$PY" "$FRONTEND" "$loc" --chan-hz "$chan" --center-hz "$chan" --fs "$FS" >/tmp/orb_fe.log 2>&1; then
    log "front-end channelize failed for $(basename "$loc")"; return 1
  fi
  out=$(bash "$RAILRUN" "$CHAR_RAIL" 60 2>/dev/null)
  cp=$(printf '%s\n' "$out" | sed -n 's/^CHAR carrier_present=\([0-9]*\).*/\1/p' | head -1)
  cp=${cp:-0}
  log "CHAR $(basename "$loc"): carrier_present=${cp} | $(printf '%s\n' "$out" | grep '^CHAR ' | tr '\n' '|')"
  if [ "$cp" = "1" ]; then
    log "  -> carrier RECEIVED (attested proof-of-reception material; IS-5/IS-6 sign+chain it)"
  else
    log "  -> no carrier heard (HONEST empty — nothing to attest; .decoded marker only)"
  fi
  # echo the full CHAR block on stdout for the pull-sweep branch (IS-6) to consume.
  printf '%s\n' "$out"
}

# pick + (optionally) capture the next best Orbcomm pass.
run_next(){
  # keep TLEs fresh (radio-free, at loop top); fetch_tle.sh now also refreshes orbcomm.
  local tlef="$GD/data/tle_orbcomm.txt" tage
  tage=$(( $(date +%s) - $(stat -f %m "$tlef" 2>/dev/null || echo 0) ))
  if [ "$tage" -gt 43200 ]; then
    if bash "$GD/scripts/fetch_tle.sh" >/tmp/tle_fetch.log 2>&1; then log "TLE refreshed: $(tail -1 /tmp/tle_fetch.log)"; else log "TLE refresh failed — keeping existing (age $((tage/3600))h)"; fi
  fi
  local info; info=$($PY "$GD/scripts/next_pass.py" --constellation orbcomm --minel "$MINEL")
  if [[ "$info" == NONE* ]]; then log "no Orbcomm pass >= ${MINEL}deg soon"; return 2; fi
  eval "$info"   # SAT MINS DUR ELEV FREQ MODE AOS_EPOCH
  log "next Orbcomm: $SAT in ${MINS}min El${ELEV}deg @${FREQ}Hz (AOS $(date -u -r "$AOS_EPOCH" +%H:%MZ 2>/dev/null || echo +${MINS}min))"
  if [ "${1:-}" = "--plan" ]; then echo "$info"; return 0; fi
  local now w; now=$(date +%s); w=$(( AOS_EPOCH - now - 45 ))
  if [ "$w" -gt 0 ]; then log "AIS keeps running; sleeping ${w}s until AOS-45s"; sleep "$w"; fi
  capture_orbcomm_pass "$SAT" "$FREQ" "$DUR" "$ELEV"
}

main(){
  case "${1:-}" in
    --plan)
      run_next --plan
      ;;
    --once)
      run_next
      resume_ais   # belt-and-suspenders on exit
      ;;
    *)
      log "orbcomm_monitor START — best-elevation pass capture, MINEL ${MINEL}, cooldown ${ORBCOMM_COOLDOWN}s"
      while true; do
        run_next || true
        sleep "$ORBCOMM_COOLDOWN"
      done
      ;;
  esac
}

# Only run when executed directly — sourcing must NOT start anything.
# `(return 0 2>/dev/null)` succeeds ONLY when sourced (return is illegal at the top
# level of an executed script), so this robustly distinguishes source from exec even
# when the caller's environment unsets BASH_SOURCE.
if ! (return 0 2>/dev/null); then
  main "$@"
fi
