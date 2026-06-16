# CORPUS_ENGINE.md — the attested-corpus engine (PAOS-for-ourselves Rung 1)

> **Status:** DESIGN KEYSTONE. This is the design rationale for the corpus engine. The wire
> contract it implements is `docs/RECEIPT_CONTRACT.md` §A.10 (the `CORPUS_RECEIPT`, the ROW schema,
> the REJECT schema), §A.5 (INFERENCE kind set), §B.6 (the `data/corpus/` ledger family + the
> supersession / node-split / single-parent notes), and §E.3 (the CORPUS DEV seed). Those are
> byte-for-byte authoritative; this file is the *why*.
>
> The engine builds the **first artifact-attested training corpus we own**: every committed corpus
> row rolls up into a `CORPUS_RECEIPT` whose `derived_from` names a real, signed off-air AIS
> reception. The moat is not the labels — it is that the labels are **provably SOURCED**.

---

## 0. THE NON-NEGOTIABLE HONEST CLAIM (the guardrail — never regress)

Every layer of this engine keeps exactly this claim, no more:

- **Provably-SOURCED corpus, NEVER true-labels / ground-truth** beyond the decoder oracle. We assert
  "this row came from a CRC-passing AIS frame received in a signed batch" — we do **not** assert the
  label is correct ground truth.
- **The verifier walks RECEIPTS (batch granularity), NOT rows.** A per-row provenance walk is a
  **FUTURE rung** — it is never stated in the present tense anywhere.
- **Bound-row count TODAY is ZERO.** The 5 `paos_labels` rows are **June-2024 placeholder fixtures**
  that resolve to **NO** AIS batch window (the AIS FACT chain only covers `[1780458007, 1781650480]`).
  They are quarantined as REJECT(`unresolvable_derived_from`), **not counted** as bound corpus rows.
- **Accumulating, not train-ready.** The corpus grows as real AIS receptions accumulate; it is not
  a finished, balanced, train-ready dataset.
- **`lat`/`lon` = the transmitter's self-report**, copied verbatim as source text — NOT receiver
  geo. Receiver `geo` stays the literal `PENDING_needs_GPS_PPS`.
- **`rfml` is a disagreement signal only** — and today it cannot even disagree (no per-burst join
  key), so the overlay is honestly `PENDING_no_overlay`.
- **Failed-oracle observations → the rejects ledger with the bit**, never invented, never dropped.

---

## 1. THE BINDING RATIONALE — why this rides `verify.rail` UNCHANGED

The whole design constraint is: **emit an INFERENCE receipt that the EXISTING verifier already
knows how to walk, with ZERO change to `verify.rail` and ZERO change to the crypto.**

- A `CORPUS_RECEIPT` is `type=INFERENCE` (RECEIPT_CONTRACT §A.2 / §A.5). The verifier's INFERENCE
  gate already runs `derived_from` resolution: it reads `pipe_field receipt "derived_from"`, and for
  a `type=INFERENCE` line calls `resolve_derived_multi fact_blob dfrom` — failing the line if the
  hash does not appear as a `chain_hash` in the FACT blob (`unresolved inference parent`).
- The corpus FACT blob is `data/ais_receipts.jsonl` (the live AIS FACT ledger). A `CORPUS_RECEIPT`'s
  `derived_from` is a **single 64-hex AIS FACT `chain_hash`**. `resolve_derived_multi` with a
  single-parent (no `;`) value is identical to the old single-parent resolver — so the corpus
  receipt resolves on the **existing** code path, no extension needed.
- The crypto shape is copied verbatim from `src/ais_attest.rail` / `src/paos_label_attest.rail`:
  pipe-joined receipt string → `ed25519_sign` → `sig_hex` →
  `chain_hash = sha256_hex(cat[receipt, "|sig=", sig_hex])` → append a v=2 space-after-colon JSON
  line. **No crypto change.** The corpus signer only assembles a different field list (§A.10) and
  writes to the `data/corpus/` family.

The cold-verify run is therefore: stage `/tmp/lg_verify_target.txt = data/corpus/ais_corpus_receipts.jsonl`
and `/tmp/lg_verify_facts.txt = data/ais_receipts.jsonl`, run `src/verify.rail` unchanged, and the
single-parent INFERENCE path resolves each `CORPUS_RECEIPT` back to its AIS reception.

---

## 2. THE WALKABILITY PROOF — what a verifier can actually check today

For a committed `CORPUS_RECEIPT` line, the verifier proves, per line, all of:

1. **sig** — the Ed25519 signature is valid over the exact pipe bytes under the stated `signer`.
2. **chain** — `chain_hash == sha256_hex(cat[receipt, "|sig=", sig_hex])`.
3. **link** — the `prev=` token equals the previous line's `chain_hash` (or `GENESIS` for line 1).
4. **derived_from** — the single `derived_from` hash appears as a `chain_hash` in
   `data/ais_receipts.jsonl`. **This is the moat:** a corpus batch that cites a hash NOT in the AIS
   FACT ledger fails with `unresolved inference parent`.

By hand, the operator closes the loop one level deeper (the batch-granularity provenance walk):
grep the receipt's `derived_from` in `data/ais_receipts.jsonl`, read that FACT's
`batch_start`/`batch_end`, and confirm the corpus batch's `batch_start`/`batch_end` (and the rows'
`ts_scrubbed`) fall inside that window. This hand-check is exactly the check that **FAILS** for the
June-2024 seed rows — proving the ZERO-bound-today reality is real, not asserted.

**What is NOT yet provable (named in the future tense, never claimed):** a per-ROW walk — each
`corpus_row`'s `ts_scrubbed` independently resolved by the verifier to a covering AIS window. Today
the verifier resolves the **receipt's** single `derived_from`; row-level binding is enforced by the
extractor's resolver at build time and is hand-checkable, but it is **not** a verifier-enforced
guarantee. That is a future rung.

---

## 3. THE NO-OVERCLAIM BOUNDARY — what each PENDING means and why it is correct

These are not failures to fill in later by faking — each PENDING is the **honest** value because the
live source genuinely lacks the data:

- **`feat=[]`** (empty array). The live `ais.jsonl` keys are EXACTLY `[ch, kind, msg, node, ts]` —
  no IQ, no features. So features are an empty array, **never** 18 zeros and never fabricated.
  `feat_provenance=PENDING_iq_not_co_captured`, `snr_db=null`, `iq_ref=PENDING_no_iq_ref` for the
  same reason: the IQ was not co-captured with the decoded frame.
- **`oracle.verdict=1` SYNTHESIZED FROM PRESENCE.** `pi_ais_decode` emits a frame ONLY after
  CRC-16/X-25 (`0xF0B8`) passes. A present row therefore inherits a passing oracle. The engine
  **never reads `o['crc_pass']`** (the key does not exist in the source) and **never re-runs CRC** —
  it inherits the upstream oracle. The `verdict_field` is kept as `"crc_pass"` for schema clarity,
  but the value is synthesized from presence, not read.
- **`rfml_pred=PENDING_no_overlay`, `agree=PENDING`.** The rfml classifier is real and alive (it
  emits real classes — msk=63432 / fsk=10189 on the survey), but there is **no per-burst join key**
  linking a specific rfml window to a specific decoded AIS frame. So the overlay is PENDING for a
  **correct structural reason** (no key), NOT because the classifier is dead and NOT because it
  disagreed. rfml stays a disagreement *signal* — and it cannot signal disagreement without a key.
- **`geo=PENDING_needs_GPS_PPS`** on the receipt. Receiver geo is never fabricated. The row's
  `lat`/`lon` are the **transmitter's** self-report, a distinct thing, copied verbatim as text.
- **`agree_count=PENDING`** on the receipt — mirrors the per-row `agree=PENDING` (no join key).
- **`code_sha256` / `input_sha256`** follow A.6 custody: a real `shasum` when available, else
  `PENDING_no_code_hash` / `PENDING_no_input_hash`. Never `0`.

---

## 4. THE HONEST-REJECTS POLICY — failed observations are recorded, never dropped

A failed observation is itself a fact about the node, so it is **never** silently dropped. Three
reject reasons, all written to `data/corpus/ais_rejects.jsonl` (A.10 REJECT schema), all counted
into `CORPUS_RECEIPT.rejected_count`:

- **`oracle_crc_fail`** — a row whose oracle did not pass (verdict=0). Recorded with the bit, not in
  the corpus.
- **`unresolvable_derived_from`** — a row whose `ts_scrubbed` lands in NO AIS receipt window. The
  reject carries `derived_from_attempt=NONE` — **NEVER a fabricated hash**. This is the bin the 5
  June-2024 placeholder seeds fall into today.
- **`ts_pre_ntp`** — a row whose `ts` is pre-NTP (`< 1e9`) or unparseable. Dropped from the corpus,
  recorded as a reject.

Rejects are NOT signed per-row; their COUNT is the only thing that enters the signed surface (via
`rejected_count` in the `CORPUS_RECEIPT`). `bound_rate_x1000=1000` is a constant precisely because
the engine rejects every unbindable row — so every row that survives into the corpus is bound, and
the bound rate is identically 1.000.

---

## 5. THE KEYSTONE FUNCTION — the ts → AIS-batch-window resolver (NO hardcoded chain_hash)

The piece missing today and the heart of the engine. Given a row's `ts_scrubbed`, it returns the
`chain_hash` of the covering AIS FACT batch — by **SCANNING** `data/ais_receipts.jsonl`, never by a
hardcoded hash (the live AIS tail moves every rollup; a hardcoded hash would rot immediately).

- Scan each AIS receipt, parse its `batch_start`/`batch_end` from the pipe receipt string (the same
  `re.search(r'batch_(start|end)=([0-9]+)')` shape as `attest_ais_rollup.sh`).
- Return the COVERING receipt's `chain_hash` (the FACT line's `chain_hash` JSON field).
- `data/ais_fact_chain.txt` holds only the **TAIL** chain_hash — historical binding therefore
  **needs** this full ledger scan, not the tail file.
- **Boundary tie-break:** if `ts == batch_end` of receipt N == `batch_start` of receipt N+1, bind to
  the **EARLIEST** receipt by ledger position (deterministic, lowest line number).
- `ts` in no window → REJECT(`unresolvable_derived_from`), never a fake hash.

This resolver is what makes the moat real: the bind is computed by scanning the signed FACT ledger,
so a corpus row can only claim a `derived_from` that actually exists.

---

## 6. THE ZERO-BOUND-ROWS-TODAY REALITY (the seeds are placeholder fixtures)

This is stated plainly so no downstream surface can drift into overclaiming:

- The 5 `paos_labels` rows are **June-2024 placeholder fixtures**. Their timestamps predate the AIS
  FACT chain's covered window (`[1780458007, 1781650480]`), so the resolver finds **NO** covering
  batch for any of them.
- They therefore resolve to **REJECT(`unresolvable_derived_from`)** — quarantined, NOT counted as
  bound corpus rows. **The bound-corpus-row count TODAY is ZERO.**
- This is not a bug to paper over; it is the honest state, and the cold-verify acceptance run (§ in
  the build spec) explicitly requires demonstrating that the seeds do NOT resolve. The engine works
  the moment real AIS receptions accumulate rows whose `ts` lands inside a signed AIS window — at
  which point the count climbs from zero, provably and per-batch.

The corpus is **accumulating, sourced, and walkable at batch granularity** — and honest that it
holds zero bound rows the day it ships.

---

## 7. ENGINE LAYERS (where each piece lives — implemented in later steps, not here)

| Layer | File | Role |
|---|---|---|
| L1 EXTRACTOR | `scripts/corpus_extract.py` | decoded AIS frame → corpus_row \| REJECT; resolver binds `derived_from`; `feat=[]`; presence→verdict=1; dedup key `(mmsi,msg_type,ts_scrubbed)`; deterministic `(ts,mmsi)` sort; stages the `LC_ALL=C`-sorted batch + counts to the exact `/tmp` paths the signer reads. `AIS_SRC` overridable (default `~/.ledatic/roofv2/ais.jsonl`). |
| L2 DRIVER | `scripts/corpus_engine_rollup.sh` | `set -u`, NO `set -e`; single/all/since-cursor; cursor = `data/corpus/corpus_rollup_cursor.txt`; 1:1 receipt-per-AIS-batch; audit-hardened no-op (fire only if cursor present AND `corpus_fact_chain.txt` present+non-empty AND extractor zero-new — else re-sign, the wedge guard); `prev`=tail chain_hash (GENESIS if empty); beacon via `fetch_beacon_pulse.sh`; railrun the signer; advance cursor ONLY after RC==0. |
| L3 SIGNER | `src/corpus_attest.rail` | copy `ais_attest.rail` / `paos_label_attest.rail` crypto shape; emit the §A.10 `CORPUS_RECEIPT` (`derived_from` first-post-`n`, read from `/tmp/corpus_derived_from.txt`); write `data/corpus/corpus_fact_chain.txt`; append the v=2 space-after-colon JSON line. NO crypto change. |
| VERIFIER | `src/verify.rail` **UNCHANGED** | stage `/tmp/lg_verify_target.txt=ais_corpus_receipts.jsonl` + `/tmp/lg_verify_facts.txt=data/ais_receipts.jsonl`; the single-parent INFERENCE path resolves. |

> This doc and `docs/RECEIPT_CONTRACT.md` §A.10/§A.5/§B.6/§E.3 are the contract. The L1/L2/L3 files
> above are implemented in subsequent steps — **this step writes only the contract + this design
> doc, no engine code.**

---

*End of corpus-engine design. The acceptance bar is the 7-point cold-verify run in the build spec,
all offline with `AIS_SRC=<fixture>`, plus both negatives rejecting, plus the seeds demonstrably not
resolving (ZERO-bound-today).*
