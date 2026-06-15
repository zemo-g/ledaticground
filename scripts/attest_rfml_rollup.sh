#!/bin/bash
# attest_rfml_rollup.sh — Mini-side RFML + PAOS-label attestation roll-up (RFML-5 / AC-5).
#
# Implements RECEIPT_CONTRACT.md (A)-(G) for the RFML stream. A SEPARATE, idempotent step
# that reads the accumulated rfml.jsonl + paos_labels.jsonl, signs the unsigned tail under
# the v=2 contract, and chains it. It NEVER touches the raw capture/decode path
# (ais_monitor.sh, pi_characterize.py, pi_ais_decode.py, refresh.sh stay untouched).
#
# Two sub-steps, each idempotent + beacon-anchored + hash-chained:
#   (1) RFML_RECEIPT  — over the rfml.jsonl classifier-summary tail. Carries the HONEST
#       health=<ok|degraded> bit + the EXACT model tag from the source row. degraded iff
#       signal_windows==0 AND unknown_windows==windows (the V3-gain regression signature).
#       RIGHT NOW the deployed serving build is degraded, so this attests health=degraded —
#       the broken state is signed truth, not hidden. The SAME pipeline flips to health=ok
#       automatically once the serving feat-dim fix lands (no attestation change needed).
#   (2) PAOS_LABEL_RECEIPT — over the sorted-canonical (LC_ALL=C) paos_labels.jsonl batch.
#       The decoder-as-oracle corpus: every CRC-valid AIS burst is a labeled example. Signs
#       the batch DIGEST (the labels are not embedded; the file is the evidence).
#
# Beacon pulse is fetched by scripts/fetch_beacon_pulse.sh (NOT Rail — shell() has no env).
# On beacon-unreachable the receipt is STILL signed+chained with pulse_id=PENDING_... .
#
# Idempotency: if there are no new rfml summary rows since the last RFML receipt's batch_end,
# the RFML sub-step exits without signing (no empty receipts). Same for the label batch:
# zero new label lines -> no PAOS receipt. So re-running back-to-back appends nothing new.
#
# bash-3.2 safe (macOS default). Integer parsing is done HERE (Rail's to_int is float-only).
set -u

REPO="/Users/ledaticempire/projects/ledaticground"
PY="/opt/homebrew/bin/python3.11"
RAILRUN="$REPO/scripts/railrun.sh"
FETCH="$REPO/scripts/fetch_beacon_pulse.sh"

# Source rows: live RFML summaries + PAOS labels. RFML_SRC is the roof node's accumulating
# classifier log; PAOS_SRC is the decoder-as-oracle label store. Both are read-only here.
RFML_SRC="${LG_RFML_SRC:-$HOME/.ledatic/roofv2/rfml.jsonl}"
PAOS_SRC="${LG_PAOS_SRC:-$REPO/data/paos_labels.jsonl}"

RFML_LEDGER="$REPO/data/rfml_receipts.jsonl"
PAOS_LEDGER="$REPO/data/paos_label_receipts.jsonl"

# Serialize against other rollups touching the shared /tmp staging + railrun lock.
exec 8>/tmp/attest_rfml_rollup.lock
flock 8 2>/dev/null || true

# ---- Beacon pulse (honest PENDING fallback baked into the fetcher) -----------------------
if [ -f "$FETCH" ]; then
    # shellcheck disable=SC1090
    . "$FETCH" || true
fi
[ -f /tmp/lg_pulse_id.txt ]  || printf '%s\n' "PENDING_beacon_unreachable" > /tmp/lg_pulse_id.txt
[ -f /tmp/lg_pulse_hex.txt ] || printf '%s\n' "PENDING"                     > /tmp/lg_pulse_hex.txt

# ---- prev-hash helper: last chain_hash of a ledger, or GENESIS --------------------------
prev_sha_of() {
    local ledger="$1"
    if [ -s "$ledger" ]; then
        tail -1 "$ledger" | "$PY" -c "import sys,json;
try:
    print(json.loads(sys.stdin.read()).get('chain_hash','GENESIS') or 'GENESIS')
except Exception:
    print('GENESIS')" 2>/dev/null || echo "GENESIS"
    else
        echo "GENESIS"
    fi
}

# ---- last signed RFML batch_end (idempotency cursor) ------------------------------------
last_batch_end_of() {
    local ledger="$1"
    if [ -s "$ledger" ]; then
        tail -1 "$ledger" | "$PY" -c "import sys,json,re
try:
    r=json.loads(sys.stdin.read()).get('receipt','')
    m=re.search(r'batch_end=([0-9]+)', r)
    print(m.group(1) if m else '0')
except Exception:
    print('0')" 2>/dev/null || echo "0"
    else
        echo "0"
    fi
}

# =========================================================================================
# (1) RFML_RECEIPT — classifier-summary tail
# =========================================================================================
attest_rfml() {
    if [ ! -f "$RFML_SRC" ]; then
        echo "RFML: no source $RFML_SRC — skip"
        return 0
    fi
    local last_end
    last_end="$(last_batch_end_of "$RFML_LEDGER")"

    # Select the unsigned tail of summary rows (ts > last_end), compute the canonical label
    # digest text, health, exact model tag, batch bounds + count. Stages to /tmp for Rail.
    # NTP-scrubbed unix from the ISO ts; rows without a usable ts are dropped (provenance
    # bound integrity). health=degraded iff signal_windows==0 AND unknown_windows==windows.
    "$PY" - "$RFML_SRC" "$last_end" <<'PYEOF'
import sys, json, re
from datetime import datetime, timezone
src, last_end = sys.argv[1], int(sys.argv[2])
rows=[]
with open(src) as f:
    for ln in f:
        ln=ln.strip()
        if not ln: continue
        try: d=json.loads(ln)
        except Exception: continue
        s=d.get('summary')
        if not isinstance(s, dict): continue
        ts=d.get('ts')
        if not ts: continue
        try:
            u=int(datetime.strptime(ts,"%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())
        except Exception:
            continue
        if u <= last_end: continue
        rows.append((u, s, d.get('node','')))
if not rows:
    open('/tmp/rfml_have_batch.txt','w').write('0\n')
    sys.exit(0)
rows.sort(key=lambda r: r[0])
bs=rows[0][0]; be=rows[-1][0]; n=len(rows)
# health over the WHOLE batch: degraded if ANY row in the batch hits the regression signature
# (signal_windows==0 AND unknown_windows==windows). Honest: the node is currently degraded.
degraded=False
for _,s,_ in rows:
    sw=s.get('signal_windows'); uw=s.get('unknown_windows'); w=s.get('windows')
    if sw==0 and uw is not None and w is not None and uw==w:
        degraded=True; break
# Also flag degraded if EVERY row has signal_windows==0 (no signal recovered all batch).
if not degraded and all(s.get('signal_windows')==0 for _,s,_ in rows):
    degraded=True
health='degraded' if degraded else 'ok'
# Exact model tag: take the most recent row's tag (the model currently producing labels).
model=rows[-1][1].get('model','UNKNOWN_model')
# Canonical product digest text: one deterministic per-row line, sorted by (ts).
def canon(u,s):
    c=s.get('classes',{})
    cls='/'.join("%s:%s"%(k,c.get(k,0)) for k in sorted(c.keys()))
    return "ts=%d|windows=%s|signal_windows=%s|unknown_windows=%s|classes=%s|model=%s"%(
        u, s.get('windows'), s.get('signal_windows'), s.get('unknown_windows'), cls, s.get('model',''))
prod="\n".join(canon(u,s) for u,s,_ in rows)
open('/tmp/modclass_result.txt','w').write(prod+("\n" if prod else ""))
open('/tmp/rfml_health.txt','w').write(health+"\n")
open('/tmp/rfml_model.txt','w').write(model+"\n")
open('/tmp/rfml_batch_start.txt','w').write(str(bs)+"\n")
open('/tmp/rfml_batch_end.txt','w').write(str(be)+"\n")
open('/tmp/rfml_batch_n.txt','w').write(str(n)+"\n")
open('/tmp/rfml_have_batch.txt','w').write('1\n')
print("RFML: %d new summary rows, batch %d..%d, health=%s, model=%s"%(n,bs,be,health,model))
PYEOF

    if [ "$(cat /tmp/rfml_have_batch.txt 2>/dev/null || echo 0)" != "1" ]; then
        echo "RFML: nothing new since batch_end=$last_end — idempotent skip"
        return 0
    fi
    prev_sha_of "$RFML_LEDGER" > /tmp/rfml_prev_sha.txt
    bash "$RAILRUN" "$REPO/src/modclass_attest.rail"
}

# =========================================================================================
# (2) PAOS_LABEL_RECEIPT — decoder-as-oracle label corpus batch
# =========================================================================================
attest_paos_labels() {
    if [ ! -s "$PAOS_SRC" ]; then
        echo "PAOS: no labels at $PAOS_SRC — skip (honest empty: no CRC-valid bursts to label)"
        return 0
    fi
    # Determine the last signed label batch_end cursor from the PAOS ledger.
    local last_end
    last_end="$(last_batch_end_of "$PAOS_LEDGER")"

    # Select label lines with ts_scrubbed > last_end, sort CANONICAL (LC_ALL=C) so the digest
    # is stable across re-runs (digest-canonical-byte-order rule), stage batch + counts.
    "$PY" - "$PAOS_SRC" "$last_end" <<'PYEOF'
import sys, json
src, last_end = sys.argv[1], int(sys.argv[2])
lines=[]
with open(src) as f:
    for ln in f:
        ln=ln.rstrip("\n")
        if not ln.strip(): continue
        try: d=json.loads(ln)
        except Exception: continue
        if d.get('kind')!='paos_label': continue
        # INVARIANT enforcement: only CRC-valid bursts may be labels. Drop anything that
        # doesn't carry oracle.crc_pass==1 (defends the FACT half of the wall at sign time).
        o=d.get('oracle',{})
        if o.get('crc_pass')!=1: continue
        ts=d.get('ts_scrubbed')
        u=int(ts) if isinstance(ts,(int,float)) else -1
        if u >= 0 and u <= last_end: continue
        lines.append((u, ln, 1 if d.get('agree')==1 else 0))
if not lines:
    open('/tmp/paos_have_batch.txt','w').write('0\n')
    sys.exit(0)
# Canonical order: LC_ALL=C byte sort of the raw label lines (locale-independent).
canon=sorted(l[1] for l in lines)
prod="\n".join(canon)+"\n"
open('/tmp/paos_labels_batch.txt','w').write(prod)
ucand=[l[0] for l in lines if l[0]>=0]
bs=min(ucand) if ucand else 0
be=max(ucand) if ucand else 0
n=len(lines); agree=sum(l[2] for l in lines)
fl='featlib_v3-18f'
for _,ln,_ in lines:
    try:
        fl=json.loads(ln).get('feat_lib','featlib_v3-18f'); break
    except Exception: pass
open('/tmp/paos_batch_start.txt','w').write(str(bs)+"\n")
open('/tmp/paos_batch_end.txt','w').write(str(be)+"\n")
open('/tmp/paos_label_count.txt','w').write(str(n)+"\n")
open('/tmp/paos_agree_count.txt','w').write(str(agree)+"\n")
open('/tmp/paos_feat_lib.txt','w').write(fl+"\n")
open('/tmp/paos_have_batch.txt','w').write('1\n')
print("PAOS: %d new CRC-valid labels (%d agree), batch %d..%d, feat_lib=%s"%(n,agree,bs,be,fl))
PYEOF

    if [ "$(cat /tmp/paos_have_batch.txt 2>/dev/null || echo 0)" != "1" ]; then
        echo "PAOS: nothing new since batch_end=$last_end — idempotent skip"
        return 0
    fi
    prev_sha_of "$PAOS_LEDGER" > /tmp/paos_prev_sha.txt
    bash "$RAILRUN" "$REPO/src/paos_label_attest.rail"
}

echo "=== RFML attestation roll-up $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
attest_rfml
attest_paos_labels
echo "=== roll-up done ==="
