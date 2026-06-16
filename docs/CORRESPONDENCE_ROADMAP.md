# Attested Correspondence — Implementation Roadmap

> **North star (declared 2026-06-16):** aim *entirely* at closing the gap between
> **provenance** (we proved the record) and **correspondence** (the record is *of something real*).
>
> **The claim we make — and the one we refuse:** we make every link from sensor to conclusion
> attested and auditable, and we make *the cost of an undetected lie scale with the size of the
> conspiracy required to sustain it.* We **never** claim "proof of truth." That overclaim is the
> synthetic-evidence sin dressed in philosophy.
>
> Memory: `ledatic-north-star-attested-correspondence`. Builds on `RECEIPT_CONTRACT.md` (v2) +
> `CHANNEL_INTELLIGENCE.md` (the FACT/INFERENCE wall).

## The ladder — where each rung stands

| Rung | What it proves | Status |
|---|---|---|
| 1. **Integrity** | bytes unchanged since signing | ✅ DONE — the Ed25519 signature |
| 2. **Authority** | a known/staked identity signed (not a dev key) | ⏳ V100 rung — key management |
| 3. **Custody** | output = deterministic result of *attested code* on *attested input* | 🔨 **Wave A — software-now** |
| 4. **Sensor-binding** | the capture is consistent with a *physically-possible world* | 🔨 **Waves B/C — software-now mechanism; real claim gated on GPS-PPS** |
| 5. **Correspondence** | the claim is true of the world — *a network property, not a record's* | 🔨 **Wave D mechanism; real truth gated on a 2nd node** |

**Reframe that governs the whole program:** truth is not a property of a record — it is an
*emergent property of a web of mutually-constraining independent observations.* No single
signature proves the ship is there; N independent nodes whose claims are only jointly consistent
if it is there make a lie cost a globally-consistent conspiracy. **The mesh is the truth layer.**

## We already own the primitives (promote, don't invent)

These exist in `src/` as decoder/selftest rungs. The program promotes them to the binding+witness layer:

- `doppler_predict.rail` — TLE → predicted Doppler curve (the physics *model*).
- `doppler_real.rail` — measured Doppler from a real capture (the *observation*).
- `tdoa.rail` — time-difference-of-arrival (mesh *geometry*).
- `coattest.rail` — cross-attestation signatures (mesh *trust*).
- `bundle.rail` — multi-physics receipt bundle.
- `verify.rail` — the independent verifier ("the verifier is Rail").

## Implementation waves

### SOFTWARE-NOW (build + synthetic-validate + commit; no hardware, never touch the live decode path)

**Wave A — Custody chain (rung 3).** Extend the receipt contract so an attestation commits to
`code_sha256` (the decoder binary's hash) + `input_sha256` (the raw IQ/source hash), chained:
*"this product is the deterministic output of attested-code-X on attested-input-Y."* Moves the
trust question from "believe the output" to "believe the input + the code" (auditable).
*Validates:* recompute the chain offline; tamper either hash → verify fails.

**Wave B — Physics-binding receipt (rung 4, the mechanism).** A new `binding_attest.rail`:
runs `doppler_predict` (from a TLE + a *claimed* location) vs `doppler_real` (the capture) and
emits a `PHYSICS_BINDING_RECEIPT` carrying the consistency residual + verdict
(`physics_ok=<0|1>`, `residual_hz`, tolerance, the orbit + claimed-geo it was checked against).
It is `type=INFERENCE` bound (`derived_from`) to the FACT capture receipt. *Honest gating:* the
real claim needs a *truthful* location (Wave E); until then the receipt carries
`geo=PENDING_needs_GPS_PPS` and is explicitly labeled "binding mechanism validated, location
unattested." *Validates:* synthetic Doppler from a known orbit binds (`physics_ok=1`); a Doppler
inconsistent with any physical orbit-from-here is rejected (`physics_ok=0`).

**Wave C — Physics-running verifier (physicify with teeth).** Extend `verify.rail` so verification
does NOT just check signatures — it *re-runs the physics consistency check* on a
`PHYSICS_BINDING_RECEIPT` and rejects a receipt whose claimed residual can't be reproduced. The
verifier itself runs the physics. *Validates:* a forged binding (good signature, fabricated
residual) fails the re-run even though the signature is valid — the whole point.

**Wave D — Mesh co-attestation protocol (rung 5, the mechanism).** Formalize `coattest` + `tdoa`
into a 2-node cross-witness: two nodes attest the same emission; a `MESH_WITNESS_RECEIPT` binds
both signatures + asserts their TDOA is geometrically consistent with the speed of light and the
two node locations. *Validates:* with a *simulated* second node, consistent TDOA co-attests;
an inconsistent pair (a fabricated second witness) is rejected. Real truth-layer needs Wave F.

### HARDWARE-GATED (software fully built + flagged; the physical part is the sole blocker)

**Wave E — GPS-PPS location truth.** SparkFun NEO-F10N → truthful geolocation + disciplined time.
**Strategically central, not polish:** you cannot bind "a real object seen from *here*" without
knowing *here* truthfully. Unlocks the *real* (non-synthetic) Wave B/C claim — flips
`geo=PENDING_needs_GPS_PPS` to an attested location and a PPS-disciplined clock.

**Wave F — Second physical node.** Deploy node #2 (instrumented + GPS-PPS) → the mesh becomes
real; Wave D's TDOA cross-witness runs over real off-air emissions. **The truth layer only exists
once this lands** — a single node can only ever offer provenance.

## The honest ceiling (state it always)

A resourced adversary controlling the physical RF environment — transmitting a *real,
physics-consistent* over-the-air spoof — still defeats sensor-binding: the sensor honestly records
a genuine emission that is a lie (the GPS-spoofing problem). Past that point we are in plain
empirical epistemics, where all of science lives: truth is provisional, corroborated, falsifiable,
**never proven.** This is the reason the claim is always *"cost of an undetected lie,"* never
*"proof of truth."* The mesh raises that cost; it does not abolish it.

## Sequence + checkpoints

A → (B ∥ D) → C, each software-now wave synthetic-validated and committed before the next. E and F
are procurement/deployment, tracked but not coded against until the parts land. Every wave keeps
the live AIS pipeline untouched, commits nothing without review, and pushes only on operator word.
