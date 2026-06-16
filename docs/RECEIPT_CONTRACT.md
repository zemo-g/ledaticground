# RECEIPT_CONTRACT.md — Canonical Signed-Receipt Contract v=2

> **Status:** AUTHORITATIVE. This is the single source every other attestation-core
> ticket (AC-1, AC-2, AC-3, AC-5, AC-6) and every viz/radio/ML ticket that touches a
> ledger implements against, byte-for-byte. Built by ticket **AC-0** (KEYSTONE) — this
> ticket changes **zero** `.rail` / `.sh` files; it only writes this spec.
>
> Grounded in the six existing signers that already share one identical shape
> (pipe-joined receipt string → `ed25519_sign` → `sig_hex` → `chain_hash =
> sha256_hex(cat[receipt,"|sig=",sig_hex])` → single-object `data/<x>_receipt.json`):
> `src/ais_attest.rail`, `src/acars_attest.rail`, `src/beacon_por.rail`,
> `src/modclass_attest.rail`, plus `src/attest.rail` and `src/survey_attest.rail`.
> v=2 keeps that exact crypto shape and adds **real hash-chaining**, **beacon anchoring**,
> the **FACT/INFERENCE wall**, and **append-only per-stream JSONL ledgers**.
>
> Hard rules this contract enforces (operator standing rules):
> - **Never fabricate** geo / coords / attestations / decodes.
> - `geo` stays the literal `PENDING_needs_GPS_PPS` until a surveyed GPS-PPS fix lands.
> - Beacon-fetch failure → `pulse_id=PENDING_beacon_unreachable`. **NEVER** `pulse_id=0`,
>   **NEVER** wall-clock as the attestation clock (attestation-chain-as-time rule).
> - Failed-check frames (CRC/BCS/RS) are **recorded with the bit=0**, never dropped.

---

## (A) THE SIGNED RECEIPT STRING — field order is LOAD-BEARING

The Ed25519 signature is computed over **this exact pipe-joined string**. Any change to
field order, separators, key names, or whitespace changes the signed bytes and breaks
verification. Emit it verbatim.

### A.1 FACT receipt (the per-stream decoded-truth kinds)

```
<KIND>_RECEIPT|v=2|type=FACT|node=<node_id>|station=<sta>|band=<band>|geo=<geo>|pulse_id=<pid>|pulse_hex=<vh16>|batch_start=<unix>|batch_end=<unix>|n=<count>|<honesty_bit>|product_sha256=<digest>|prev=<prev_chain_hash>|signer=<pk_hex>
```

Field-by-field (in load-bearing order):

| Field | Meaning |
|---|---|
| `<KIND>_RECEIPT` | leading token, no `k=v` — see KIND set below |
| `v=2` | contract version |
| `type=FACT` | machine-readable wall (FACT vs INFERENCE) |
| `node=<node_id>` | node identity; supports future multi-node co-sign |
| `station=<sta>` | sanitized station name from `data/station_name.txt` (`regional_MI`) |
| `band=<band>` | RF band string, per-kind constant (see KIND table) |
| `geo=<geo>` | `PENDING_needs_GPS_PPS` until GPS-PPS lands — never fabricated |
| `pulse_id=<pid>` | beacon pulse_id (7-digit int when online) or `PENDING_beacon_unreachable` |
| `pulse_hex=<vh16>` | first 16 hex chars of the beacon `value_hex`, carried as a HEX STRING |
| `batch_start=<unix>` | earliest source-row scrubbed unix ts in this batch (provenance BOUND) |
| `batch_end=<unix>` | latest source-row scrubbed unix ts in this batch (provenance BOUND) |
| `n=<count>` | number of frames/rows committed by `product_sha256` |
| `<honesty_bit>` | **per-kind** honesty field (see A.3); omitted only for AIS |
| `product_sha256=<digest>` | sha256_hex of the deterministic batch digest (see A.4) |
| `prev=<prev_chain_hash>` | `chain_hash` of the previous ledger line, or `GENESIS` for line 1 |
| `signer=<pk_hex>` | 64-hex Ed25519 public key of the (DEV) signing seed |

> **Note on `prev`.** The existing signers literally write the token `prev=` (not
> `prev_sha=`). v=2 keeps that exact `prev=` token so the chain link is the substring
> `prev=`+`<prev_chain_hash>`. (Some design prose calls this field `prev_sha`; the
> wire token is `prev=` — match the existing signers.)

### A.2 INFERENCE receipt (derived products — a SEPARATE ledger family)

```
<KIND>|v=2|type=INFERENCE|node=<node_id>|station=<sta>|band=<band>|geo=<geo>|pulse_id=<pid>|pulse_hex=<vh16>|batch_start=<unix>|batch_end=<unix>|n=<count>|derived_from=<fact_chain_hash>|product_sha256=<digest>|prev=<prev_chain_hash>|signer=<pk_hex>
```

Identical skeleton, but `type=INFERENCE` and a **mandatory** `derived_from=<fact_chain_hash>`
field naming the `chain_hash` of the FACT receipt this product was computed from. A
verifier seeing `type=INFERENCE` with an **unresolvable** `derived_from` (a hash that does
not appear as any `chain_hash` in the corresponding FACT ledger) **MUST reject**.

### A.3 Per-kind honesty bits

The `<honesty_bit>` slot in A.1 is filled per KIND. Failed-check frames are recorded with
the bit=`0`, **never dropped** — "received, check failed" is itself a fact.

| KIND | honesty_bit field | meaning |
|---|---|---|
| `AIS_RECEIPT` | *(none)* | AIS rows carry no per-batch check bit; slot omitted |
| `ACARS_RECEIPT` | `bcs_ok=<0\|1>` | block-check-sequence verdict (cross-pol low-SNR caveat) |
| `ORBCOMM_RECEIPT` | `crc_ok=<0\|1>` | CRC block-decode verdict (orbcomm_frame.rail) |
| `RFML_RECEIPT` | `health=<ok\|degraded>` **and** `model=<tag>` | classifier health + EXACT model tag from the source row |
| `RS41_DECODE_RECEIPT` | `rs_ok=<0\|1>` | Reed-Solomon block-decode verdict |
| `LRPT_DECODE_RECEIPT` | `cadu_ok=<0\|1>` | satdump RS-deframe verdict: `1` iff CADU count>0 (a CADU is an RS-corrected CCSDS VCDU) **and** satdump exit=0; a 0-CADU / no-Viterbi-lock pass is still signed with `cadu_ok=0` |
| `ORBCOMM_POR_RECEIPT` | `crc_ok=<0\|1>` | proof-of-reception; `payload=NONE_proprietary` (no proprietary payload bytes emitted) |

> `RFML_RECEIPT` carries **two** honesty fields in the honesty slot region:
> `health=<ok|degraded>` (degraded ⇔ `signal_windows==0 AND unknown_windows==windows`,
> the exact V3-gain regression signature) and `model=<tag>` copied verbatim from the
> source summary row. A degraded label record is still signed truth: "this node, with
> this model build, produced these (suspect) labels."

### A.4 `product_sha256` — what the digest commits to

`product_sha256` is `sha256_hex` over a **deterministic** digest of the batch, NOT the raw
frames embedded in the line. The committed product is the newline-join of the **canonical
per-frame strings**, in a deterministic order (sorted by `(ts, key)` where key is mmsi /
reg / frame-no as appropriate), staged to a `/tmp` product file the Rail signer reads:

```
product      = read_file "/tmp/<stream>_batch_frames.txt"   (or the kind's product path)
product_hash = sha256_hex product
```

The frames themselves are **not embedded** in the receipt (keeps the JSONL line small);
the **source jsonl is the evidence** the digest commits to. A verifier holding the same
source rows recomputes the identical digest. The per-kind contract product paths:

| KIND | `/tmp` product path |
|---|---|
| AIS | `/tmp/ais_batch_frames.txt` |
| ACARS | `/tmp/acars_deframe_out.txt` |
| ORBCOMM | `/tmp/orbcomm_decode_out.txt` |
| RFML | `/tmp/modclass_result.txt` (label digest + model tag + health staged alongside) |
| RS41 | `/tmp/rs41_decode_out.txt` |
| LRPT_DECODE | `sha256` of `<bin>.satdump/*.cadu` (the RS-deframed CADU stream), staged **as hex** by the driver — the `.cadu` is binary/NUL-bearing, so it follows the IQ-capture hex-staging discipline (never pulled through a Rail string). `product_sha256` commits to the **CADU frames**, NOT the rendered MSU-MR PNGs — the imagery is a deterministic downstream render of the attested CADUs (anyone holding the CADUs + satdump reproduces it) |
| VESSEL_INFERENCE | canonical sorted `transits.jsonl` rows |

### A.5 KIND sets — FACT vs INFERENCE

**FACT kinds** (`type=FACT`, go in the per-stream fact ledgers):
`AIS_RECEIPT`, `ACARS_RECEIPT`, `ORBCOMM_RECEIPT`, `RFML_RECEIPT`,
`RS41_DECODE_RECEIPT`, `LRPT_DECODE_RECEIPT`, `ORBCOMM_POR_RECEIPT` (`payload=NONE_proprietary`).

> `LRPT_DECODE_RECEIPT` is a FACT (an LRPT CADU is a Reed-Solomon-corrected CCSDS VCDU by
> the time satdump emits it — a verified decode, exactly like `RS41_DECODE_RECEIPT`). It
> carries custody `input_sha256` = the raw IQ `.bin` digest. **When the same `.bin` is also
> attested by `iq_capture`**, this digest equals that capture FACT's `product_sha256` — the
> shared value is a **checkable capture↔decode correspondence** (no `derived_from`; FACTs
> carry custody directly). **Honest scope:** the correspondence is populated by the sweep
> (`attest_lrpt_decode_all.sh` mints the `iq_capture` FACT before the decode FACT), and is
> cross-checkable by hand, but `verify.rail` does **not** yet auto-resolve `input_sha256`
> against another ledger's `product_sha256` — so it is a correspondence, not yet a
> verifier-enforced guarantee. (Auto-resolution is a tracked follow-up.)

**INFERENCE kinds** (`type=INFERENCE`, go in the separate inference ledger family, each
carries `derived_from`): `VESSEL_INFERENCE_RECEIPT`, `RS41_INFERENCE_RECEIPT`, the
`LABEL=INFERENCE` sub-block of `ORBCOMM_POR_RECEIPT`, `PHYSICS_BINDING_RECEIPT` (A.7),
`MESH_WITNESS_RECEIPT` (A.8), and `CORPUS_RECEIPT` (A.10).

> A CRC/BCS/RS/UW-verified decode = **FACT** with its honesty bit. Derived kinematics /
> geodetic transform / ETA / track / sat-id = **INFERENCE** in a separate ledger, bound
> to the FACT it rests on by `derived_from`.

### A.6 Custody fields (Wave A, rung 3)

Two new signed pipe fields appear in **EVERY FACT receipt**, inserted **between** `n=<count>`
(and **after** the per-kind honesty bit when one is present) and `product_sha256=`:

```
...|n=<c>|[<honesty_bit>|]code_sha256=<64hex|PENDING_no_code_hash>|input_sha256=<64hex|PENDING_no_input_hash>|product_sha256=<64hex>|prev=<prev_chain_hash>|signer=<pk_hex>
```

- `code_sha256` = `shasum -a 256` of the signer's `.rail` **SOURCE** — it binds the source,
  **NOT** the compiled bytes. (Reproducible-build follow-up: a future rung must bind the
  compiled binary's hash to the source hash so "attested code" means attested *bytes*, not
  just attested text. Named here as the explicit follow-up.)
- `input_sha256` = `shasum -a 256` of the raw source bytes (the input the product is the
  deterministic function of).
- **Honest defaults** are `PENDING_no_code_hash` / `PENDING_no_input_hash` — **never `0`,
  never fabricated.** Both are 64-hex strings when present.
- **INFERENCE receipts inherit custody transitively** via `derived_from` (the parent FACT
  carries the custody fields; the INFERENCE binds to that parent's `chain_hash`).
- **No version bump** — `v=2` stays. `v=2` now has **two FACT shapes** (with and without
  custody fields), both backward-walkable because `verify.rail`'s `pipe_field` is
  position-agnostic (it scans for the `<key>=` token, not a fixed offset).

### A.7 `PHYSICS_BINDING_RECEIPT` (Wave B emits / Wave C re-runs)

One merged, load-bearing field list (`type=INFERENCE`):

```
PHYSICS_BINDING_RECEIPT|v=2|type=INFERENCE|node=<id>|station=<sta>|band=Doppler-binding-137.1MHz|geo=<geo>|pulse_id=<pid>|pulse_hex=<vh16>|batch_start=<unix>|batch_end=<unix>|n=<windows>|derived_from=<fact_chain_hash>|physics_ok=<0|1>|estimator=<centroid|peak>|residual_hz=<int>|tol_hz=<int>|repro_tol_hz=<int>|sat=<NOAA-19>|fc_hz=<int>|orbit=<norad@epoch>|tle_sha256=<64hex>|meas_sha256=<64hex>|t_shift_s=<float>|const_off_hz=<float>|claimed_geo=<lat_lon|..._SYNTH>|note=binding_mechanism_validated_location_unattested|product_sha256=<64hex>|prev=<prev_chain_hash>|signer=<pk_hex>
```

- `derived_from` is the **first post-`n` field** (matches the A.2 INFERENCE walk).
- The tolerance key is **`tol_hz`** (NOT `tolerance_hz`). `repro_tol_hz` is a **distinct** key:
  the verifier's reproduction band (the slack allowed when Wave C recomputes the residual).
- `residual_hz` / `tol_hz` / `repro_tol_hz` are **INTEGER Hz**.
- `t_shift_s` + `const_off_hz` are **MANDATORY**: the emitter **COMMITS** the chosen Doppler
  alignment so the verifier does a **SINGLE-point residual recompute**, not a 240-step
  re-search. (Without the committed alignment the verifier would have to re-search the whole
  curve; committing it makes Wave C cheap and deterministic.)
- `geo` stays the literal `PENDING_needs_GPS_PPS`. `claimed_geo` carries the `_SYNTH` suffix
  when the observer is the placeholder (synthetic) location. **Numeric `geo_lat` / `geo_lon`
  are staged in `/tmp` ONLY, never committed.**
- `physics_ok=1` means "**consistent within `tol_hz`**", **NEVER "verified true."**
- Register `PHYSICS_BINDING_RECEIPT` in the A.5 INFERENCE kind set.

### A.8 `MESH_WITNESS_RECEIPT` (Wave D)

```
MESH_WITNESS_RECEIPT|v=2|type=INFERENCE|node=<aggregator>|station=<sta>|band=<emission_band>|geo=<aggregator_geo>|pulse_id=|pulse_hex=|batch_start=|batch_end=|n=2|mesh_peer=<REAL|SIMULATED>|clock_disc=<PPS|SAMPLE_SYNC_ASSUMED>|nodeA=<id>|geoA=<PENDING_needs_GPS_PPS>|nodeB=<id>|geoB=<SIMULATED_PENDING_needs_GPS_PPS>|emission_product_sha256=<64hex>|tdoa_s=<float|PENDING>|tdoa_pred_s=<float|PENDING>|tdoa_resid_s=<float|PENDING>|tdoa_tol_s=<float>|baseline_km=<float|PENDING>|mesh_ok=<0|1>|sigA=<128hex>|sigB=<128hex>|signerA=<64hex>|signerB=<64hex>|derived_from=<factA_chain_hash>;<factB_chain_hash>|product_sha256=<64hex>|prev=<prev_chain_hash>|signer=<aggregator_pk_hex>
```

- `derived_from` is a **`;`-joined TWO-parent value**. `verify.rail` is extended to
  `str_split ";"` the `derived_from` value and resolve **EACH** parent against the
  corresponding FACT ledger, **failing loud if either is unresolvable** (same "unresolved
  inference parent" rejection as A.2, applied per parent).
- `mesh_peer` + `clock_disc` are **NEVER-droppable honesty bits**. A `SIMULATED` /
  `SAMPLE_SYNC_ASSUMED` `mesh_ok=1` is a **MECHANISM verdict** — it asserts the TDOA is
  geometrically self-consistent, **NEVER "correspondence" / "witnessed."** Real
  correspondence needs `mesh_peer=REAL` + `clock_disc=PPS` (Wave F).
- Register `MESH_WITNESS_RECEIPT` in the A.5 INFERENCE kind set.

### A.9 `SPECTRUM_RECEIPT` (extract-more #2 — attested RF-environment spectrum)

```
SPECTRUM_RECEIPT|v=2|type=FACT|node=<id>|station=<sta>|band=137-VHF-cu8-wideband|geo=<geo>|pulse_id=|pulse_hex=|batch_start=<unix>|batch_end=<unix>|n=<n_fft_cols>|occ_pct=<float>|peak_excess_db=<int>|dyn_range_db=<int>|fs_hz=<int>|nfft=<int>|code_sha256=<64hex>|input_sha256=<64hex>|spectrum_sha256=<64hex>|product_sha256=<64hex>|prev=<prev_chain_hash>|signer=<pk_hex>
```

- A self-calibrating spectral SUMMARY of a **wideband cu8 IQ capture** (`raw_iq/*.bin` — the real
  137-band RF environment, NOT the demod AIS audio). Every metric is relative to the capture's OWN
  noise floor (cu8 has no absolute cal), so SDR gain + band-edge rolloff cancel — honest measured
  values, signed as a FACT.
- The **"meet in the middle"** hybrid: the receipt is a compact, queryable summary (occupancy, peak
  excess, dynamic range) AND commits by hash to full fidelity — `input_sha256` (raw .bin),
  `spectrum_sha256` (the fixed-grid binned spectrum, recomputable from the IQ), `product_sha256`
  (the full summary JSON), `code_sha256` (generator + signer, A.6 custody). The light record stays
  un-fakeable: recompute the bins from the retained IQ and check `spectrum_sha256`.
- All DSP is in `gen_spectrum_summary.py` (mirrors `wb_proto.py`); the Rail signer does only string
  assembly + crypto (no float math). **EPISODIC** (per retained wideband capture), NOT a continuous
  series — continuous wideband would need dedicated periodic IQ grabs (SDR contention; future).
  Complementary to the LRPT-decode attestation (that signs the CADUs a capture decodes to; this
  signs what the band looked like). Register `SPECTRUM_RECEIPT` in the A.5 FACT kind set.

### A.10 `CORPUS_RECEIPT` (PAOS-for-ourselves Rung 1 — attested training corpus)

`type=INFERENCE`. The moat = artifact-attestation: every `CORPUS_RECEIPT` carries
`derived_from=<single AIS FACT chain_hash>`, so a verifier walks each corpus batch back to a
signed off-air AIS reception. It rides `verify.rail`'s **EXISTING** INFERENCE gate
(`verify.rail` derived_from resolution, single-parent path) **UNCHANGED** — no crypto and no
verifier change.

```
CORPUS_RECEIPT|v=2|type=INFERENCE|node=<sta>|station=<sta>|band=AIS-161.975MHz-PAOS-labels|geo=PENDING_needs_GPS_PPS|pulse_id=<pid>|pulse_hex=<vh16>|batch_start=<unix>|batch_end=<unix>|n=<label_count>|derived_from=<single_ais_fact_chain_hash_64hex>|stream=ais|label_count=<n>|agree_count=<n|PENDING>|rejected_count=<r>|bound_rate_x1000=1000|label_oracle=crc_ok|feat_lib=<tag>|code_sha256=<64hex|PENDING_no_code_hash>|input_sha256=<64hex>|corpus_sha256=<64hex>|product_sha256=<64hex>|prev=<prev_chain_hash>|signer=<pk_hex>
```

- `derived_from` is the **first post-`n` field** (matches the A.2 INFERENCE walk).
- **SINGLE-PARENT ONLY.** `derived_from` is a single 64-hex AIS FACT `chain_hash`, **never** a
  `;`-joined list. `verify.rail`'s `resolve_derived_multi` resolves exactly **2** parents; 3+ are
  silently ignored — so the corpus engine emits **ONE `CORPUS_RECEIPT` per source AIS FACT batch
  (1:1)** and stays under the 2-parent ceiling. (See the SINGLE-PARENT MANDATE note in E.2.)
- `corpus_sha256` **==** `product_sha256` = `sha256_hex` over the **`LC_ALL=C` byte-sorted
  newline-join of THIS batch's corpus-row lines** (digest-canonical-byte-order; mirrors
  `attest_rfml_rollup.sh:201`). Both keys carry the same value so the existing `product_sha256`
  verifier path keeps working AND the corpus-specific name is explicit.
- All counts/rates are **ints** (Rail `to_int` is float-only — integer parsing happens in the
  bash wrapper, never in Rail). `bound_rate_x1000=1000` is a **constant** (the engine rejects any
  unbindable row to R2, so every committed row is bound — rate is identically 1.000).
- `label_oracle=crc_ok` and `feat_lib=<tag>` are constants/tags. `agree_count` is `PENDING` for the
  honest reason in the A.10 ROW schema (no per-burst rfml join key), never `0`.
- `code_sha256` / `input_sha256` follow the A.6 custody discipline (`PENDING_no_code_hash` /
  64-hex; never `0`). `code_sha256` = `shasum` of `src/corpus_attest.rail`; `input_sha256` =
  `shasum` of the AIS source.

#### A.10 ROW schema — `data/corpus/ais_corpus.jsonl` (one physical line per row, fixed key order)

The committed training rows. `corpus_sha256`/`product_sha256` above is the digest of THIS batch's
row lines. Fixed key order keeps diffs stable:

```json
{"v":2,"kind":"corpus_row","stream":"ais","example_id":"ais-<first12 of derived_from>-<ts_scrubbed>-<window_idx>","label_class":"msk","label_class_id":4,"oracle":{"src":"ais","verdict_field":"crc_pass","verdict":1,"mmsi":<int>,"msg_type":<int>},"label_fields":{"mmsi":<int>,"msg_type":<int>,"lat":"<text>","lon":"<text>"},"rfml_pred":"PENDING_no_overlay","agree":"PENDING","feat":[],"feat_lib":"featlib_v3-18f","feat_provenance":"PENDING_iq_not_co_captured","snr_db":null,"window_idx":0,"iq_ref":"PENDING_no_iq_ref","derived_from":"<single ais_fact_chain_hash 64hex>","node":"ledaticground-roofv2","pulse":"<pulse_id>","ts_scrubbed":<unix int>}
```

- `label_class_id` is the `modclass.rail` class space `{0 noise, 1 carrier, 2 afsk, 3 fsk, 4 msk}`.
  AIS GMSK = **`msk` (4)**.
- `oracle.verdict=1` is **SYNTHESIZED FROM PRESENCE** — `pi_ais_decode` emits a frame ONLY after
  CRC-16/X-25 (`0xF0B8`) passes, so a present row inherits a passing oracle. **NEVER read
  `o['crc_pass']`** (the live `ais.jsonl` keys are EXACTLY `[ch,kind,msg,node,ts]` — there is no
  `crc_pass` key), **NEVER re-run** CRC.
- `feat=[]` is an **empty array** — NEVER 18 zeros, NEVER fabricated features (the live source
  carries no IQ / no features). `feat_provenance=PENDING_iq_not_co_captured`, `snr_db=null`,
  `iq_ref=PENDING_no_iq_ref` for the same honest reason.
- `rfml_pred=PENDING_no_overlay` / `agree=PENDING`: rfml is a real classifier (msk=63432 / fsk=10189
  classes) but has **no per-burst join key** to this AIS stream — so the overlay is honestly
  PENDING for a **correct reason** (no key), NOT a dead classifier and NOT a disagreement.
- `lat`/`lon` are copied **VERBATIM as source text** (no float reformat) and are the
  **transmitter's self-report** — NOT receiver geo; receiver `geo` stays `PENDING_needs_GPS_PPS`.
- `node="ledaticground-roofv2"` on ROWS (from the live source), while the **RECEIPT** carries
  `node=regional_MI` (from `data/station_name.txt`) — the node split is surfaced honestly; the
  provenance bind resolves by `derived_from` hash, not by node string.
- `ts_scrubbed` = `fromisoformat(ISO) -> int`; `clean_ts` drops `ts < 1e9` / unparseable rows.

#### A.10 REJECT schema — `data/corpus/ais_rejects.jsonl` (failed-oracle, NEVER silently dropped)

```json
{"v":2,"kind":"corpus_reject","reason":"oracle_crc_fail|unresolvable_derived_from|ts_pre_ntp","oracle":{"src":"ais","verdict_field":"crc_pass","verdict":0},"ts_scrubbed":<unix>,"derived_from_attempt":"<hash|NONE>","node":"ledaticground-roofv2","pulse":"<pid>"}
```

- Rejects are **NOT signed per-row**; their COUNT rolls into `CORPUS_RECEIPT.rejected_count`.
- A row whose `ts` lands in **NO** AIS receipt window → `REJECT(unresolvable_derived_from)` with
  `derived_from_attempt=NONE` — **NEVER** a fabricated `derived_from` hash. This is the honest
  failed-oracle policy: failed observations go to the rejects ledger with the bit, never invented
  and never dropped.

Register `CORPUS_RECEIPT` in the A.5 INFERENCE kind set.

---

## (B) THE JSON LEDGER LINE — shape, space-after-colon rule, chain_hash, filenames

### B.1 Line shape (one physical line per append)

```json
{"v":2,"type":"FACT|INFERENCE","receipt":"<the pipe string>","sig":"<128hex>","signer":"<64hex>","chain_hash":"<64hex>"}
```

### B.2 Space-after-colon rule (LOAD-BEARING for the cold verifier)

`src/verify.rail`'s `field_val` extractor scans for the pattern `"<key>": "` — **WITH a
space after the colon**. Emitters MUST emit `"key": "value"` (space after the colon) for
every string field the verifier reads (`receipt`, `sig`, `signer`, `chain_hash`).
Emitting `"key":"value"` (no space) breaks the existing cold verifier silently.

> The compact `"v":2` / `"type":"..."` prefixes may be written without the space because
> the verifier does not field-match on them; but **`receipt`/`sig`/`signer`/`chain_hash`
> MUST use space-after-colon.** When in doubt, use space-after-colon everywhere.

### B.3 `chain_hash` formula (verbatim from the existing signers)

```
chain_hash = sha256_hex(cat[receipt, "|sig=", sig_hex])
```

This is exactly what every existing signer computes and what `verify.rail` expects.
It is the value the **next** line's `prev=` copies. (Live-run confirmed in the existing
signers: VERIFY=1 / TAMPER=0.)

### B.4 The hash-chain rule

- `line[k].receipt` contains the substring `prev=` + `line[k-1].chain_hash`.
- `line[0].receipt` contains `prev=GENESIS`.
- The roll-up driver reads `prev` from `tail -1 <ledger>` (the `chain_hash` JSON field of
  the last line) and stages it to a `/tmp` file the Rail signer reads via `field_or`,
  falling back to `GENESIS` when the ledger is empty/absent.

### B.5 Per-stream FACT ledger filenames

```
data/ais_receipts.jsonl
data/acars_receipts.jsonl
data/orbcomm_receipts.jsonl
data/rfml_receipts.jsonl
data/rs41_receipts.jsonl
data/lrpt_decode_receipts.jsonl
```

### B.6 The separate INFERENCE ledger family (NEVER mixed with FACT ledgers)

```
data/vessel_inference_receipts.jsonl
data/rs41_inference_receipts.jsonl
data/orbcomm_por_receipt.json
```

The fact ledgers stay pure decoded truth; inference ledgers carry derived products. The
two families never mix in the same file.

**Wave B/C/D ledger families** (each follows the same chained-jsonl + legacy-single-object
pattern as B.5/B.7):

- **Physics-binding** (Wave B/C, INFERENCE):
  - `data/physics_binding_receipts.jsonl` — chained v=2 ledger
  - `data/binding_receipt.json` — legacy single-object
  - `data/chain/binding_prev.txt` — `prev` staging (last `chain_hash`)
- **Mesh-witness** (Wave D, INFERENCE):
  - `data/mesh_witness_receipts.jsonl` — chained v=2 ledger
  - `data/mesh_witness_receipt.json` — legacy single-object
  - `data/chain/mesh_witness_prev.txt` — `prev` staging
- **IQ-capture** (Wave B0, NEW v=2 FACT capture stream):
  - `data/iq_capture_receipts.jsonl` — chained v=2 ledger
  - `data/iq_capture_receipt.json` — legacy single-object
  - `data/iq_capture_fact_chain.txt` — fact-chain `prev` staging
- **LRPT-decode** (Wave B0, NEW v=2 FACT decode-product stream — sibling to IQ-capture):
  - `data/lrpt_decode_receipts.jsonl` — chained v=2 ledger (FACT, `cadu_ok` honesty bit)
  - `data/lrpt_decode_receipt.json` — legacy single-object
  - `data/lrpt_decode_fact_chain.txt` — latest fact `chain_hash` (a future MSU-MR
    image-projection INFERENCE can name it as `derived_from`)
  - `data/lrpt_decode_rollup_cursor.txt` — last-signed `input:product` (idempotency note;
    the authoritative idempotency gate is a ledger scan for the `input_sha256`+`product_sha256` pair)
- **CORPUS** (PAOS-for-ourselves Rung 1, INFERENCE — the attested training corpus, A.10):
  - `data/corpus/ais_corpus_receipts.jsonl` — chained v=2 INFERENCE ledger (the `CORPUS_RECEIPT`
    lines; this is the `verify.rail` target)
  - `data/corpus/ais_corpus.jsonl` — the committed corpus ROWS (A.10 ROW schema), one physical
    line per row; the batch's `corpus_sha256`/`product_sha256` is the digest over these row lines
  - `data/corpus/ais_rejects.jsonl` — the REJECT ledger (A.10 REJECT schema); failed-oracle /
    unresolvable / pre-NTP rows, NEVER silently dropped; COUNT rolls into `rejected_count`
  - `data/corpus/corpus_rollup_cursor.txt` — last-signed `batch_end` (missing → derive from the
    CORPUS ledger tail; empty → 0)
  - `data/corpus/corpus_fact_chain.txt` — latest CORPUS `chain_hash` (the `prev=` staging source;
    GENESIS when empty)

> **PAOS_LABEL_RECEIPT supersession.** The existing `src/paos_label_attest.rail` emitted a
> `PAOS_LABEL_RECEIPT` as **`type=FACT` with NO `derived_from`** — it claimed the labels as
> first-class truth, unbound to any decoded reception. That is the exact defect this corpus engine
> fixes: a derived label product is an **INFERENCE**, and it MUST name the AIS FACT batch it was
> computed from. `CORPUS_RECEIPT` (A.10, `type=INFERENCE`, mandatory single-parent `derived_from`)
> **SUPERSEDES** `PAOS_LABEL_RECEIPT`; the new CORPUS chain starts at `prev=GENESIS` and lives in
> its own `data/corpus/` family — it does NOT continue the `data/paos_label_receipts.jsonl` chain.
>
> **Node split (surfaced honestly).** The `CORPUS_RECEIPT` carries `node=regional_MI` /
> `station=regional_MI` (read from `data/station_name.txt`, the receiving-node identity), while the
> corpus ROWS carry `node=ledaticground-roofv2` (copied from the live source). These are different
> strings for a real reason (receipt = the signing/receiving station; row = the source node tag);
> the provenance bind is by `derived_from` **hash**, never by node string.
>
> **SINGLE-PARENT MANDATE (the 2-parent ceiling).** `verify.rail`'s `resolve_derived_multi`
> resolves exactly **2** `;`-joined parents; a 3+-parent value silently ignores the extras. So the
> corpus engine emits **ONE `CORPUS_RECEIPT` per source AIS FACT batch (1:1)**, with `derived_from`
> a **single** 64-hex hash. **Never** a `;`-joined `derived_from` on a `CORPUS_RECEIPT`.
>
> **Per-row walk is a FUTURE rung.** Today the verifier walks RECEIPTS at **batch granularity** —
> it resolves the receipt's single `derived_from` to a covering AIS FACT batch. A per-ROW provenance
> walk (each `corpus_row` independently bound and verifier-resolved) is a **future rung**, stated
> here in the future tense only — it is NOT a present-tense capability.

### B.7 Legacy single-object backward-compat

Each signer **ALSO keeps writing** its legacy single-object
`data/<x>_receipt.json` (the latest receipt as one JSON object) so the existing
`src/verify.rail` single-object mode keeps validating it unchanged:

```
data/ais_receipt.json
data/acars_receipt.json
data/orbcomm_receipt.json
data/modclass_receipt.json     (RFML)
data/rs41_receipt.json
data/beacon_por_receipt.json
```

So every v=2 signer does **two** writes: append one line to the chained `*_receipts.jsonl`
ledger, **and** overwrite the legacy single-object `*_receipt.json`.

---

## (C) BEACON PULSE FETCH — by the SHELL wrapper, NOT Rail

Rail's `shell()` does **not** inherit env vars or PATH, and the framed entropy endpoint is
heavy for Rail's TLS path. So the **shell roll-up driver** fetches the live beacon pulse
and stages it to files the Rail signer reads via the existing `field_or` idiom.

### C.1 Canonical fetch command (live-verified, e.g. pulse_id=2122506)

```bash
curl -s --max-time 6 https://ledatic.org/entropy/pulse | \
  /opt/homebrew/bin/python3.11 -c "import sys,json;d=json.load(sys.stdin);print(d['pulse_id']);print(d['value_hex'][:16])"
```

The endpoint returns keys: `pulse_id` (7-digit int), `value_hex` (64 hex), plus
`unix_timestamp` and `prev_value_hex`.

### C.2 Canonical staging paths

| What | Path | Rail reads via |
|---|---|---|
| pulse_id (line 1 of fetch output) | `/tmp/lg_pulse_id.txt` | `field_or "/tmp/lg_pulse_id.txt" "PENDING_beacon_unreachable"` |
| pulse_hex (line 2, first 16 hex chars) | `/tmp/lg_pulse_hex.txt` | `field_or "/tmp/lg_pulse_hex.txt" "PENDING_beacon_unreachable"` |

> These supersede the older per-stream `/tmp/ais_pulse.txt` / `/tmp/acars_pulse.txt`
> paths; all v=2 signers read the unified `/tmp/lg_pulse_id.txt` + `/tmp/lg_pulse_hex.txt`.

### C.3 HONEST fallback (NEVER pulse_id=0, NEVER wall-clock)

If `curl` fails (network down, or a Pi-side run with no route), the staging files are
absent/empty and the receipt records `pulse_id=PENDING_beacon_unreachable` (and
`pulse_hex=PENDING_beacon_unreachable`). The receipt is **STILL signed and chained** — the
hash-chain is the local clock; the beacon is the external anchor only when reachable.

- **NEVER** write `pulse_id=0`.
- **NEVER** substitute wall-clock time as the pulse (attestation-chain-as-time rule).
- `pulse_hex` always travels as a **hex string** (avoids the `char_from_int(0)==""`
  NUL-drop trap — same discipline `beacon_por.rail` uses for payloads).
- `batch_start` / `batch_end` **are** allowed to carry unix seconds because they are batch
  **BOUNDS** (provenance of which source rows), explicitly NOT the attestation clock — and
  they come from the already-NTP-scrubbed source rows (`transit_log.py` clean_ts pattern),
  not the Pi's pre-NTP clock.

---

## (D) STATION IDENTITY + GEO POLICY

### D.1 Station / node identity

- `station` / `node` is read from `data/station_name.txt`. **Current value: `regional_MI`**
  (sanitized — region only, no street address, per the public-repo sanitation rule).
- The fallback default in the existing signers is `ledaticground-roof`; v=2 keeps the
  same `field_or` idiom: `field_or ".../data/station_name.txt" "ledaticground-roof"`.

### D.2 Geo policy — `PENDING_needs_GPS_PPS`, never fabricated

- `geo` is read from `data/station_geo.txt`, which is **currently MISSING** (correct), so
  every signer falls back to the literal `geo=PENDING_needs_GPS_PPS`.
- The contract **KEEPS geo PENDING** until a surveyed GPS-PPS fix lands (SparkFun NEO-F10N
  ordered per `NODE_BUILD.md`). **Never fabricate coords.**
- **Public-repo committed copies** (anything checked into a public surface) carry
  **rounded-or-PENDING** location only — never precise rooftop coordinates
  (location-disclosure rule).
- A radiosonde's **own GPS position** (RS41 telemetry payload) is the **transmitter's**
  position and is explicitly distinct from the receiver `geo` (which stays
  `PENDING_needs_GPS_PPS`). Receipt comments must keep these from being conflated.

---

## (E) THE FACT/INFERENCE WALL + key policy + multi-node note

### E.1 The wall

- **FACT** = a CRC/BCS/RS/UW-verified decode batch. `type=FACT`, carries the per-kind
  honesty bit (`crc_ok`/`bcs_ok`/`rs_ok`/`health`). Failed-check frames are recorded with
  the bit=`0`, **never dropped**. Lives in the per-stream fact ledgers (B.5).
- **INFERENCE** = derived kinematics / geodetic transform / ETA / track / sat-id /
  anomaly. `type=INFERENCE`, lives in the separate inference ledger family (B.6).

### E.2 `derived_from` rule

Every INFERENCE receipt **MUST** carry `derived_from=<fact_chain_hash>` naming the
`chain_hash` of the FACT receipt(s) it was computed from. The roll-up driver passes the
latest fact-ledger `chain_hash` to the Rail signer via a `/tmp` file (e.g.
`/tmp/vessel_derived_from.txt`). A verifier that sees `type=INFERENCE` with a
`derived_from` that does **not** resolve to some `chain_hash` in the corresponding FACT
ledger **MUST reject** with `unresolved inference parent`.

### E.3 The dev-seed key table (per-stream, clearly labeled DEV seeds)

Each stream keeps its own stable, clearly-labeled **DEV** signing seed. These are NOT
production attestation authorities — production authority keys are a separate future rung
(`V100_BLUEPRINT.md`).

| Stream / signer | DEV seed (64-hex) |
|---|---|
| AIS (`ais_attest.rail`) + RFML (`modclass_attest.rail`) | `a15a15a15a15a15a15a15a15a15a15a15a15a15a15a15a15a15a15a15a15a1500` |
| ACARS (`acars_attest.rail`) | `ac3a5ac3a5ac3a5ac3a5ac3a5ac3a5ac3a5ac3a5ac3a5ac3a5ac3a5ac3a5ac00` |
| Beacon (`beacon_por.rail`) | `9012345678901234567890123456789012345678901234567890123456789000` |
| ORBCOMM (`orbcomm_attest.rail`, NEW) | distinct ORBCOMM-labeled DEV seed (clearly labeled) |
| RS41 (`rs41_attest.rail`, NEW) | distinct RS41-labeled DEV seed (clearly labeled) |
| VESSEL_INFERENCE (`vessel_inference_attest.rail`, NEW) | distinct INFERENCE-labeled DEV seed (clearly labeled) |

> New signers (ORBCOMM, RS41, VESSEL_INFERENCE) MUST pick their own distinct, clearly
> labeled DEV seed — do not reuse another stream's seed. AIS and RFML currently share a
> seed in the existing source; that is preserved for backward compat (the `model=` /
> `band=` / KIND fields disambiguate the receipts).

**Wave A/B/C/D DEV seeds (register both — all clearly-labeled DEV seeds, NOT production
authorities):**

| Stream / signer | DEV seed (64-hex) |
|---|---|
| PHYSICS_BINDING (`binding_attest.rail`) | `b14d14b14d14b14d14b14d14b14d14b14d14b14d14b14d14b14d14b14d140000` |
| MESH aggregator (`mesh_witness_attest.rail`) | `e54e54e54e54e54e54e54e54e54e54e54e54e54e54e54e54e54e54e54e540000` |
| IQ_CAPTURE (`iq_capture_attest.rail`) | `c0dec0dec0dec0dec0dec0dec0dec0dec0dec0dec0dec0dec0dec0dec0de0000` |
| SPECTRUM (`spectrum_attest.rail`) | `5fec5fec5fec5fec5fec5fec5fec5fec5fec5fec5fec5fec5fec5fec5fec0000` |
| LRPT_DECODE (`lrpt_decode_attest.rail`) | `1cad1cad1cad1cad1cad1cad1cad1cad1cad1cad1cad1cad1cad1cad1cad0000` |
| CORPUS (`corpus_attest.rail`, A.10) | `c025c025c025c025c025c025c025c025c025c025c025c025c025c025c0250000` |

> The CORPUS seed (`c025…0250000`) is a **distinct, clearly-labeled DEV seed** — not shared with
> AIS/RFML or any other stream. A `CORPUS_RECEIPT` is a NEW signer (`corpus_attest.rail`); per the
> rule above, new signers pick their own seed and never reuse another stream's.

> The IQ_CAPTURE seed is a distinct, clearly-labeled DEV seed (`c0de…0000`) — not shared
> with any other stream. **`nodeA` / `nodeB` simulation** in the Wave D mesh validator
> **reuses `coattest`'s seeds for validation only** (the two-node cross-witness needs two
> distinct signers; coattest already owns a stable pair), never for any committed
> production receipt.

### E.4 Future multi-node co-sign

The `v=2` receipt carries `node=` precisely so a future multi-node fleet can co-sign a
single product. The co-sign / bundle shape (`coattest.rail` / `bundle.rail`) and the
production authority-key ceremony are a future **V100** rung — out of scope for the
current moves, but the `node=` field reserves the wire space for it.

---

## (F) IMPLEMENTATION DISCIPLINE (every signer / driver obeys these)

### F.1 Compile / run ONLY via `railrun.sh`

Every Rail signer is compiled+run via the flock-serialized wrapper — never a bare
`/tmp/rail_out` (concurrent swarm compiles clobber the shared output):

```bash
bash scripts/railrun.sh /Users/ledaticempire/projects/ledaticground/src/<x>_attest.rail
```

`railrun.sh` `cd`s into the rail repo root so `import "stdlib/..."` resolves, and holds
`flock 9 < /tmp/railrun.lock` for the duration of the compile+run.

### F.2 Ledger append path — `append_line`, NOT `append_file`

Append to the chained ledgers via `stdlib/file.rail`'s **`append_line`** (`import
"stdlib/file.rail"`). It is the real runtime symbol (temp file + `shell cat >>`):

```rail
import "stdlib/file.rail"
...
let _ = append_line "/Users/ledaticempire/projects/ledaticground/data/<stream>_receipts.jsonl" jsonline
```

> **TRAP:** `append_file` is **NOT** a runtime symbol — it appears only in `compile.rail`'s
> side-effect classifier (≈line 2151) with no stdlib implementation. **Never call
> `append_file`.** All ledger appends go through `append_line`.

### F.3 Roll-ups are SEPARATE, idempotent, off the raw decode path

- Attestation is a **new, separate, idempotent** roll-up step. It reads accumulated source
  jsonl, takes the **unsigned tail** since the last receipt's `batch_end`, and signs it.
- If zero new rows: **exit 0**, write **no** receipt (idempotent — no empty receipts).
- The raw capture/decode path is **NEVER edited**: `scripts/ais_monitor.sh`,
  `scripts/pull_iq.sh`, `scripts/pi_ais_decode.py`, `scripts/pi_iq_capture.sh`,
  and `~/.ledatic/roofv2/refresh.sh`'s existing lines all stay untouched. The only hook is
  **one appended line** in `refresh.sh` AFTER its existing `transit_log.py` call:
  `bash <abs>/scripts/attest_ais_rollup.sh >> "$D/attest.log" 2>&1 || true`.

### F.4 Rail traps that bind every implementer

- `char_from_int(0)` returns `""` (NUL drop) → carry binary as **hex strings**, never raw
  NUL through Rail strings. `pulse_hex` and any payload travel as hex.
- **No `&&` / `||` short-circuit** — both sides always evaluate; guard with nested
  `if/then/else`.
- `to_int` is **float-only** → for string→int use `parse_int` (walks chars via
  `char_to_int`). Do batch-count integer parsing in the **bash wrapper**, not Rail.
- `shell()` does **not** inherit env/PATH → all config is **file-based** (the beacon fetch
  is the wrapper's job, staged to `/tmp` files; Rail reads via `field_or`).
- `split` is **single-char only** → use `str_split` for multi-char delimiters. Newline-split
  (char 10) is the one legal single-char `split` use (e.g. walking a ledger file).
- `filter` with a lambda can **segfault** → use a **named predicate** function.

### F.5 `/tmp` staging namespace — keep the new waves OFF the live doppler pipeline

The Wave B/C/D roll-ups stage their working files under **distinct `/tmp` prefixes** so they
never collide with the live decode pipeline's staging files:

- **Wave B (binding rollup):** `/tmp/binding_*` — explicitly **NOT** `/tmp/dop_real.iq` or
  `/tmp/dop_meas_real.out`, which belong to the **live doppler pipeline** and must not be
  read, written, or clobbered.
- **Wave C (verifier):** `/tmp/lg_verify_*`.
- **Wave D (mesh):** `/tmp/mesh_*`.

> Numeric `geo_lat` / `geo_lon` (A.7) are staged in `/tmp` ONLY and never committed —
> consistent with the `geo=PENDING_needs_GPS_PPS` policy (D.2): a synthetic numeric location
> may exist transiently in `/tmp` for the binding *mechanism*, but no precise coordinate is
> ever written to a committed ledger line.

---

## (G) QUICK-REFERENCE — minimal v=2 FACT line (AIS example)

Receipt string (signed bytes):
```
AIS_RECEIPT|v=2|type=FACT|node=regional_MI|station=regional_MI|band=AIS-161.975MHz|geo=PENDING_needs_GPS_PPS|pulse_id=2122506|pulse_hex=a1b2c3d4e5f60718|batch_start=1718400000|batch_end=1718403600|n=42|product_sha256=<64hex>|prev=GENESIS|signer=<64hex>
```

Ledger line appended to `data/ais_receipts.jsonl`:
```json
{"v":2,"type":"FACT","receipt":"AIS_RECEIPT|v=2|type=FACT|...|signer=<64hex>","sig":"<128hex>","signer":"<64hex>","chain_hash":"<64hex>"}
```

Where `chain_hash = sha256_hex(cat[receipt, "|sig=", sig_hex])` and the **next** AIS line's
`prev=` equals this `chain_hash`.

---

*End of contract. AC-1/AC-2/AC-3/AC-5/AC-6 implement (A)–(G) verbatim; byte-compatible
ledgers are the acceptance bar.*
