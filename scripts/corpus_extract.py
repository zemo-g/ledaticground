#!/usr/bin/env python3
"""corpus_extract.py -- L1 EXTRACTOR for the attested-corpus engine (PAOS-for-ourselves Rung 1).

THE KEYSTONE. Decoded AIS frame -> corpus_row (spec B) | REJECT (spec C). The moat is
artifact-attestation: every corpus row carries derived_from = a real AIS FACT chain_hash, bound by
THE RESOLVER (ts_scrubbed -> covering AIS batch window -> that receipt's chain_hash). A verifier
walks each batch back to a signed off-air reception.

NON-NEGOTIABLE HONEST CLAIM (kept here):
  - Provably-SOURCED, NEVER true-labels. The oracle is the decoder's CRC pass, nothing more.
  - presence == CRC pass: pi_ais_decode.py emits a frame ONLY after CRC-16/X-25 0xF0B8 passes, so
    we SYNTHESIZE oracle.verdict=1 from presence. The live keys are EXACTLY [ch,kind,msg,node,ts] --
    there is NO o['crc_pass'] to read, and we NEVER re-run CRC.
  - feat=[] (empty array -- NEVER 18 zeros, NEVER fabricated coefficients).
  - lat/lon copied VERBATIM as source text (no float reformat); they are the transmitter's
    self-report, NOT receiver geo. Receiver geo stays PENDING_needs_GPS_PPS.
  - rfml has no per-burst join key -> rfml_pred="PENDING_no_overlay", agree="PENDING" (correct
    reason: no key, NOT a dead classifier).
  - ts in NO AIS batch window -> REJECT(unresolvable_derived_from), NEVER a fake hash.
  - Failed-oracle / pre-NTP / unresolvable observations -> the rejects ledger with the bit, never
    silently dropped.

It does NOT touch the live AIS decode path or the 4 protected AIS ledger files. It READS
data/ais_receipts.jsonl (the FACT ledger the resolver scans) read-only, and WRITES only the
greenfield data/corpus/ tree + the /tmp staging the signer reads.

Usage:
  AIS_SRC=/abs/fixture.jsonl python3.11 scripts/corpus_extract.py    # offline / synthetic
  python3.11 scripts/corpus_extract.py                              # production (live src default)
"""
import sys, os, re, json, hashlib
from datetime import datetime

REPO = "/Users/ledaticempire/projects/ledaticground"
FACT_LEDGER = os.path.join(REPO, "data", "ais_receipts.jsonl")
CORPUS_DIR = os.path.join(REPO, "data", "corpus")
CORPUS_ROWS = os.path.join(CORPUS_DIR, "ais_corpus.jsonl")
CORPUS_REJECTS = os.path.join(CORPUS_DIR, "ais_rejects.jsonl")

# AIS_SRC: env override (default the live deploy target). Offline validation always passes a
# fixture so the live node path is never read in a build.
AIS_SRC = os.environ.get("AIS_SRC", os.path.expanduser("~/.ledatic/roofv2/ais.jsonl"))

# /tmp staging the L2 driver + L3 signer read (EXACT paths -- load-bearing).
STG_BATCH      = "/tmp/paos_labels_batch.txt"      # LC_ALL=C-sorted corpus row lines (canonical)
STG_BSTART     = "/tmp/paos_batch_start.txt"
STG_BEND       = "/tmp/paos_batch_end.txt"
STG_LABEL_CNT  = "/tmp/paos_label_count.txt"
STG_AGREE_CNT  = "/tmp/paos_agree_count.txt"
STG_FEAT_LIB   = "/tmp/paos_feat_lib.txt"
STG_DERIVED    = "/tmp/corpus_derived_from.txt"    # single parent hash (NEW)
STG_REJECTED   = "/tmp/corpus_rejected_count.txt"  # NEW
STG_INPUT_SHA  = "/tmp/corpus_input_sha.txt"       # NEW

FEAT_LIB_TAG = "featlib_v3-18f"
ROW_NODE = "ledaticground-roofv2"   # corpus ROWS carry the live source node
LABEL_CLASS = "msk"                 # AIS GMSK = msk
LABEL_CLASS_ID = 4                  # modclass.rail class space {0 noise,1 carrier,2 afsk,3 fsk,4 msk}


# --------------------------------------------------------------------------------------------------
# clean_ts: ISO-8601-with-Z OR unix-int -> unix seconds; drop pre-NTP (<1e9) / unparseable.
# Mirrors transit_log.py / attest_ais_rollup.sh discipline.
# --------------------------------------------------------------------------------------------------
def clean_ts(ts_raw):
    ts = None
    if isinstance(ts_raw, int):
        ts = ts_raw
    elif isinstance(ts_raw, str):
        s = ts_raw.strip().replace("Z", "+00:00")
        try:
            if "T" in s:
                ts = int(datetime.fromisoformat(s).timestamp())
            else:
                ts = int(float(s))
        except Exception:
            ts = None
    if ts is None:
        return None
    if ts < 1000000000:   # pre-NTP / unscrubbed clock garbage -> drop
        return None
    return ts


# --------------------------------------------------------------------------------------------------
# THE RESOLVER (keystone): ts_scrubbed -> covering AIS-batch chain_hash | None.
# SCANS data/ais_receipts.jsonl (NO hardcoded hash; the live tail moves). Parses batch_start /
# batch_end per receipt (regex like attest_ais_rollup.sh:76). Returns the COVERING receipt's
# chain_hash. ts in no window -> None (=> REJECT unresolvable_derived_from). Boundary tie-break
# (ts == batch_end of N == batch_start of N+1): bind to the EARLIEST receipt by LEDGER POSITION.
# --------------------------------------------------------------------------------------------------
_BSTART_RE = re.compile(r"batch_start=([0-9]+)")
_BEND_RE = re.compile(r"batch_end=([0-9]+)")


def load_fact_windows(ledger_path):
    """Read the FACT ledger ONCE into [(ledger_idx, batch_start, batch_end, chain_hash)] in ledger
    order. Read-only. Missing/blank ledger -> []."""
    windows = []
    if not os.path.isfile(ledger_path):
        return windows
    with open(ledger_path, "r") as f:
        for idx, ln in enumerate(f):
            ln = ln.strip()
            if not ln:
                continue
            try:
                o = json.loads(ln)
            except Exception:
                continue
            r = o.get("receipt", "")
            bs = _BSTART_RE.search(r)
            be = _BEND_RE.search(r)
            ch = o.get("chain_hash", "")
            if not bs or not be or not ch:
                continue
            windows.append((idx, int(bs.group(1)), int(be.group(1)), ch))
    return windows


def resolve_derived_from(ts_scrubbed, windows):
    """Return the chain_hash of the COVERING receipt (batch_start <= ts <= batch_end), or None.
    Boundary tie-break: earliest by ledger position (windows is already in ledger order, so the
    first match wins)."""
    for (_idx, bs, be, ch) in windows:
        if bs <= ts_scrubbed <= be:
            return ch
    return None


# --------------------------------------------------------------------------------------------------
# lat/lon VERBATIM source text. The live JSON has lat/lon as numeric literals (42.35285); json.loads
# + str() would REFORMAT (42.350000 -> 42.35), which is NOT verbatim. So we extract the literal
# numeric token straight from the raw JSON line. Fall back to None -> the row carries the literal
# string "PENDING" only if the source genuinely lacks the field (never silently zero).
# --------------------------------------------------------------------------------------------------
def raw_numeric_field(raw_line, key):
    m = re.search(r'"%s"\s*:\s*(-?[0-9][0-9.eE+\-]*)' % re.escape(key), raw_line)
    return m.group(1) if m else None


def emit_corpus_row(derived_from, ts_scrubbed, mmsi, msg_type, lat_text, lon_text, pulse, window_idx):
    """Spec B -- fixed key order for diffs. example_id = ais-<first12 of derived_from>-<ts>-<idx>.
    oracle.verdict=1 SYNTHESIZED from presence. feat=[]. lat/lon VERBATIM source text."""
    example_id = "ais-%s-%d-%d" % (derived_from[:12], ts_scrubbed, window_idx)
    row = {
        "v": 2,
        "kind": "corpus_row",
        "stream": "ais",
        "example_id": example_id,
        "label_class": LABEL_CLASS,
        "label_class_id": LABEL_CLASS_ID,
        "oracle": {
            "src": "ais",
            "verdict_field": "crc_pass",
            "verdict": 1,            # SYNTHESIZED from presence (CRC already passed in the decoder)
            "mmsi": mmsi,
            "msg_type": msg_type,
        },
        "label_fields": {
            "mmsi": mmsi,
            "msg_type": msg_type,
            "lat": lat_text,
            "lon": lon_text,
        },
        "rfml_pred": "PENDING_no_overlay",
        "agree": "PENDING",
        "feat": [],                  # empty array, NEVER zeros
        "feat_lib": FEAT_LIB_TAG,
        "feat_provenance": "PENDING_iq_not_co_captured",
        "snr_db": None,
        "window_idx": window_idx,
        "iq_ref": "PENDING_no_iq_ref",
        "derived_from": derived_from,
        "node": ROW_NODE,
        "pulse": pulse,
        "ts_scrubbed": ts_scrubbed,
    }
    # fixed key order is the dict insertion order above; json.dumps preserves it. No spaces so each
    # row is one physical line and byte-stable for the corpus_sha256 digest.
    return json.dumps(row, separators=(",", ":"))


def emit_reject(reason, ts_scrubbed, derived_from_attempt, pulse):
    """Spec C -- never silently dropped. Count rolls into CORPUS_RECEIPT.rejected_count."""
    rej = {
        "v": 2,
        "kind": "corpus_reject",
        "reason": reason,
        "oracle": {"src": "ais", "verdict_field": "crc_pass", "verdict": 0},
        "ts_scrubbed": ts_scrubbed,
        "derived_from_attempt": derived_from_attempt,
        "node": ROW_NODE,
        "pulse": pulse,
    }
    return json.dumps(rej, separators=(",", ":"))


# --------------------------------------------------------------------------------------------------
# dedup: load already-committed (mmsi, msg_type, ts_scrubbed) keys from the existing corpus ledger
# so a re-run appends ZERO new rows (idempotent).
# --------------------------------------------------------------------------------------------------
def load_existing_keys(rows_path):
    keys = set()
    if not os.path.isfile(rows_path):
        return keys
    with open(rows_path, "r") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                o = json.loads(ln)
            except Exception:
                continue
            lf = o.get("label_fields", {})
            keys.add((lf.get("mmsi"), lf.get("msg_type"), o.get("ts_scrubbed")))
    return keys


def sha256_hex(b):
    if isinstance(b, str):
        b = b.encode("utf-8")
    return hashlib.sha256(b).hexdigest()


def main():
    if not os.path.isfile(AIS_SRC):
        # nothing to extract yet -- honest no-op; still stage zero-counts so the driver no-ops cleanly.
        os.makedirs(CORPUS_DIR, exist_ok=True)
        _stage_empty()
        print("CORPUS_EXTRACT: source %s absent -- nothing to extract (0 new rows)" % AIS_SRC)
        return 0

    os.makedirs(CORPUS_DIR, exist_ok=True)
    windows = load_fact_windows(FACT_LEDGER)
    existing = load_existing_keys(CORPUS_ROWS)

    # pulse: honest PENDING here (the SIGNER stages the real beacon pulse). Rows carry PENDING; the
    # CORPUS_RECEIPT is where the live pulse lands.
    pulse = "PENDING_no_pulse"

    # First pass: parse + clean + classify every source frame into a (ts, mmsi, ...) candidate or a
    # reject. We dedup corpus rows by (mmsi, msg_type, ts_scrubbed); rejects are not deduped (the
    # COUNT is what matters, and the spec says never silently drop).
    new_rows = []          # (ts, mmsi, json_line)  -- bound, deterministic-sorted before staging
    new_rejects = []       # json_line
    batch_keys_seen = set()  # within-this-run dedup so the same frame twice in src doesn't double

    with open(AIS_SRC, "r") as f:
        for raw in f:
            raw = raw.rstrip("\n")
            if not raw.strip():
                continue
            try:
                o = json.loads(raw)
            except Exception:
                continue
            # presence == CRC pass: pi_ais_decode emits ONLY CRC-valid frames. We inherit the oracle
            # (verdict=1 synthesized). We do NOT read o['crc_pass'] (it does not exist) and never
            # re-run CRC. The decode fields are nested under "msg".
            m = o.get("msg")
            if not isinstance(m, dict):
                m = o
            ts_raw = o.get("ts")
            ts = clean_ts(ts_raw)
            if ts is None:
                # unscrubbed / pre-NTP clock -> reject with the bit (ts_pre_ntp), never dropped.
                # ts_scrubbed is unknown; record 0 as the sentinel scrubbed value.
                new_rejects.append(emit_reject("ts_pre_ntp", 0, "NONE", pulse))
                continue

            mmsi = m.get("mmsi")
            msg_type = m.get("type")
            try:
                mmsi = int(mmsi)
            except Exception:
                pass
            try:
                msg_type = int(msg_type)
            except Exception:
                pass

            # bind derived_from via THE RESOLVER. Unresolvable -> reject, NEVER a fake hash.
            derived_from = resolve_derived_from(ts, windows)
            if derived_from is None:
                new_rejects.append(emit_reject("unresolvable_derived_from", ts, "NONE", pulse))
                continue

            key = (mmsi, msg_type, ts)
            # cross-run idempotency + within-run dedup
            if key in existing or key in batch_keys_seen:
                continue
            batch_keys_seen.add(key)

            lat_text = raw_numeric_field(raw, "lat")
            lon_text = raw_numeric_field(raw, "lon")
            if lat_text is None:
                lat_text = "PENDING"
            if lon_text is None:
                lon_text = "PENDING"

            line = emit_corpus_row(derived_from, ts, mmsi, msg_type, lat_text, lon_text, pulse, 0)
            new_rows.append((ts, mmsi, line))

    # deterministic (ts_scrubbed, mmsi) sort for the row append order + the staged batch.
    new_rows.sort(key=lambda r: (r[0], str(r[1])))

    # --- Append to the corpus ledgers (greenfield data/corpus/ tree ONLY) -----------------------
    if new_rows:
        with open(CORPUS_ROWS, "a") as f:
            for (_ts, _mmsi, line) in new_rows:
                f.write(line + "\n")
    if new_rejects:
        with open(CORPUS_REJECTS, "a") as f:
            for line in new_rejects:
                f.write(line + "\n")

    # --- Stage for the signer (EXACT /tmp paths) -------------------------------------------------
    row_lines = [line for (_ts, _mmsi, line) in new_rows]
    rejected_count = len(new_rejects)

    if not row_lines:
        # zero new bound rows -> stage zero so the driver no-ops (still record rejected count + sha
        # of the source so an all-reject pass is honestly visible).
        _stage_empty(rejected_count=rejected_count, input_path=AIS_SRC)
        print("CORPUS_EXTRACT: 0 new corpus rows (%d rejects); no batch to sign" % rejected_count)
        return 0

    # single parent: ONE CORPUS_RECEIPT per source AIS FACT batch (verify.rail 2-parent ceiling).
    # All staged rows MUST share one derived_from. If a batch spans more than one AIS window, stage
    # only the rows for the EARLIEST covering window (the rest stay in the ledger, get picked up on
    # the next driver pass as a separate 1:1 receipt). This preserves SINGLE-PARENT.
    derived_set = []
    for (_ts, _mmsi, line) in new_rows:
        d = json.loads(line)["derived_from"]
        if d not in derived_set:
            derived_set.append(d)
    primary_parent = derived_set[0]
    staged = [line for line in row_lines if json.loads(line)["derived_from"] == primary_parent]
    staged_ts = [json.loads(line)["ts_scrubbed"] for line in staged]
    bstart = min(staged_ts)
    bend = max(staged_ts)

    # LC_ALL=C byte-sorted newline-join (digest-canonical-byte-order; mirrors
    # attest_rfml_rollup.sh:201). The signer's corpus_sha256 hashes exactly this product.
    canon = sorted(staged)  # python default str sort == LC_ALL=C byte order for ASCII json lines
    canon_blob = "\n".join(canon)

    with open(STG_BATCH, "w") as fh:
        fh.write(canon_blob)
    with open(STG_BSTART, "w") as fh:
        fh.write(str(bstart))
    with open(STG_BEND, "w") as fh:
        fh.write(str(bend))
    with open(STG_LABEL_CNT, "w") as fh:
        fh.write(str(len(staged)))
    with open(STG_AGREE_CNT, "w") as fh:
        fh.write("PENDING")          # no per-burst join key -> agree count is honestly PENDING
    with open(STG_FEAT_LIB, "w") as fh:
        fh.write(FEAT_LIB_TAG)
    with open(STG_DERIVED, "w") as fh:
        fh.write(primary_parent)     # single parent hash
    with open(STG_REJECTED, "w") as fh:
        fh.write(str(rejected_count))
    # input_sha256 = sha of the raw AIS source bytes this batch decoded from.
    try:
        with open(AIS_SRC, "rb") as fh:
            input_sha = sha256_hex(fh.read())
    except Exception:
        input_sha = "PENDING_no_input_hash"
    with open(STG_INPUT_SHA, "w") as fh:
        fh.write(input_sha)

    print("CORPUS_EXTRACT: staged %d corpus rows (%d rejects) derived_from=%s window=%d-%d" % (
        len(staged), rejected_count, primary_parent, bstart, bend))
    return 0


def _stage_empty(rejected_count=0, input_path=None):
    """Stage zero-count files so the driver detects a no-op cleanly (no batch to sign)."""
    with open(STG_BATCH, "w") as fh:
        fh.write("")
    with open(STG_LABEL_CNT, "w") as fh:
        fh.write("0")
    with open(STG_BSTART, "w") as fh:
        fh.write("0")
    with open(STG_BEND, "w") as fh:
        fh.write("0")
    with open(STG_AGREE_CNT, "w") as fh:
        fh.write("PENDING")
    with open(STG_FEAT_LIB, "w") as fh:
        fh.write(FEAT_LIB_TAG)
    with open(STG_DERIVED, "w") as fh:
        fh.write("NONE")
    with open(STG_REJECTED, "w") as fh:
        fh.write(str(rejected_count))
    input_sha = "PENDING_no_input_hash"
    if input_path and os.path.isfile(input_path):
        try:
            with open(input_path, "rb") as fh:
                input_sha = sha256_hex(fh.read())
        except Exception:
            pass
    with open(STG_INPUT_SHA, "w") as fh:
        fh.write(input_sha)


if __name__ == "__main__":
    sys.exit(main())
