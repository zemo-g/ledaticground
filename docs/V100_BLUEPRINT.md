# ledaticground — the v100 Blueprint

*North star for the Rail satellite ground station. Written 2026-05-31, the night v0.1 (pass prediction) went live and the antenna first heard the sky.*

> This is a blueprint, not a backlog. "v100" is the asymptote — the thing
> ledaticground becomes if we take it all the way. Versions below are
> **capability bands**, not 100 literal releases.

---

## 0. The one sentence

**An attested observatory: a pure-Rail, self-hosted ground station where every
received signal becomes a tamper-evident, beacon-anchored *physical
observation* — not "I got a weather image," but a verifiable claim that *this
image of Earth was received from this satellite, over this orbit, at this place,
at this time.***

Receiving is commodity (any $30 dongle + free software does it). **Proving what
you received is not.** That gap is the whole product, and it is pure Ledatic:
attestation is the golden goose, the radio stack in Rail is the point, and the
sky is the most honest physics surface there is.

---

## 1. Why this is a Ledatic project, not a hobby SDR

| Ledatic verb | How the observatory expresses it |
|---|---|
| **Physicify** | Orbital mechanics + RF propagation are live, attested, self-hosted physics — not a sampled artifact. The waterfall *is* the sky, now. |
| **Attest** | Every pass → Ed25519-signed bundle, hash-chained per station, anchored to the `ledatic.org/entropy` pulse. Time axis = `pulse_id`, not wall-clock. |
| **Build in Rail** | DSP, demod, decoders, SGP4 — all pure Rail, zero deps. The missing radio stack is exactly the part worth building. |
| **Self-host** | Own the chain from antenna to byte to signature. No cloud decoder, no third-party API in the trust path. |
| **No synthetic evidence** | Empty gallery beats a faked image. A pass with a bad Doppler fit is published as INCONCLUSIVE, never dressed up. |

This is the **inverse of AI-detection**, the same way [[physicify-proof-of-human]]
is: instead of asking "was this generated?", we *prove* "this was physically
sensed from reality." Call it **proof-of-reception**.

---

## 2. The intellectual core: the Doppler curve is the fingerprint

The novel piece — the thing that makes an attested reception *hard to fake*:

During a pass, the satellite's carrier frequency Doppler-shifts along a curve
determined by **the orbit (TLE) ⊗ the station's coordinates ⊗ the time**. We
measure that curve from the recorded IQ and fit it against the SGP4 prediction.

- A genuine reception has a measured Doppler curve whose residual against the
  predicted geometry is near-zero. **You cannot produce that curve without
  actually being at that location while that satellite passes overhead.**
- A replayed/synthesized recording will not carry a Doppler signature
  consistent with *the claimed place and time* — the fit residual exposes it.
- The fit residual (Hz RMS) becomes a **physical confidence score** baked into
  the attestation. Honest, measurable, falsifiable.

So the attested bundle binds four independent things into one signature:
**orbit ⟷ geography ⟷ time ⟷ the bytes received.** That four-way binding is the
moat — not the cryptography (which is table stakes), the *physics*.

> Honest threat model ([[feedback_enough_for_now_threat_model]]): a
> sophisticated adversary with their own transmitter at the right geometry could
> forge a curve. Defenses are roadmap, not v1 prereq: multi-station
> cross-attestation (§7), known-ephemeris cross-checks, and signal-fingerprinting
> the spacecraft's transmitter. v1 ships the honestly-labeled single-station
> proof and calls the rest future work.

---

## 3. The stack (all Rail, bottom to top)

```
                          ┌─────────────────────────────────────────┐
  L8  PUBLIC SURFACE       │ ledatic.org/observatory: live waterfall, │
                          │ pass schedule, attested gallery, receipts│
                          └─────────────────────────────────────────┘
  L7  ATTESTATION          sign(IQ‖product‖doppler_fit‖TLE‖coords‖pulse_id)
                          → per-station hash chain → beacon anchor
  L6  PRODUCTS             APT / LRPT / SSTV / AFSK / GMSK / telemetry decoders
  L5  SYNC + DECODE        frame sync, FEC (Viterbi/RS), deframing
  L4  DEMOD                FM, BPSK/QPSK (Costas loop), symbol timing recovery
  L3  DSP                  FIR/decimation, FFT, resampler, AGC, Doppler track
  L2  ORBITAL              full SGP4, look-angles, Doppler predict, rotator cmd
  L1  CAPTURE              IQ stream from SDR (C shim now → Rail USB later)
  L0  RF                   antenna + LNA + (eventually) rotator hardware
```

Today (v0.1) only **L2 (a simplified slice)** and **L8 (a static schedule)**
exist. Everything between L1 and L7 is the build.

---

## 4. The version ladder (capability bands)

### v0.x — Foundation *(README roadmap; v0.1 shipped)*
- **v0.1 ✅** Pass prediction (simplified SGP4 + J2), SGP4-validated to ±1–2 min.
- v0.2 RTL-SDR IQ capture via thin C shim; FFT spectrum + browser waterfall.
- v0.3 FM demod in Rail; live audio.
- v0.4 APT decoder → first NOAA weather image, end to end in Rail.
- v0.5 SigMF record + playback; LRPT (QPSK) for METEOR.

### v1–v9 — The real station
- Full SGP4 in Rail (deep-space terms, drag) — retire the J2 approximation.
- Pure-Rail DSP core: FFT, polyphase resampler, Costas/PLL, timing recovery.
- **Doppler measurement** (L3) — extract the carrier curve from IQ.
- Rotator/antenna control (az-el) for high-gain tracking.
- First **attested reception**: sign one pass bundle with the existing Rail
  Ed25519 stack ([[rail-ed25519-sign-session-1]]).

### v10–v30 — The attested observatory
- **Proof-of-reception** formalized: the §2 four-way binding, Doppler-fit score,
  per-station hash chain, beacon anchoring (`pulse_id` as time,
  [[feedback_attestation_chain_as_time]]).
- Public **attested gallery** on ledatic.org — every image carries a verifiable
  receipt; INCONCLUSIVE passes shown honestly ([[feedback_no_synthetic_evidence]]).
- Rail-native **verifier**: re-checks a receipt's chain + Doppler fit from
  scratch (the verifier is itself Rail — [[ledatic-physicify]]).
- Pi Zero "observatory" node runs an autonomous capture+attest loop.

### v30–v60 — The network
- Multi-station mesh over the fleet (Mini/Studio/Air/Pi + remote nodes).
- **Cross-attestation**: ≥2 stations independently attest the same pass from
  different geometries → a far stronger claim than any single receiver.
- **TDOA/FDOA geolocation**: triangulate a transmitter from the network's
  timing/Doppler — attested "this signal came from *here*."
- Observatory becomes a fleet HTTP surface (`:9101` family,
  [[rail-fleet-control-plane]]).

### v60–v100 — The substrate
- **Attested-RF corpus** → a [[paos-specialist-models]] reference impl: a
  specialist model trained on operated, provenance-stamped sky data. The bound
  (every sample is attested) is the moat, not scale.
- **Proof-of-reception SDK + receipts** — mirror the
  [[verifiability-sdk-frame-2026-05-23]]: free bare receipt / metered anchored
  witness counter-sign.
- The observatory is a permanent **physicify surface** on ledatic.org:
  "observation that proves itself," sitting beside "computation that proves
  itself" ([[rail-innovation-thesis-verifiable-language]]).

---

## 5. The attested bundle (the artifact)

What a single pass emits and signs:

```
pass-<norad>-<pulse_id>/
  iq.sigmf-data         raw IQ (or a downsampled witness slice)
  iq.sigmf-meta         SigMF metadata: center freq, sample rate, station coords
  product.png           decoded image (APT/LRPT) or telemetry.json
  doppler.csv           measured carrier freq vs t
  prediction.json       TLE used + predicted Doppler + look-angles
  fit.json              residual Hz-RMS, max elevation, AOS/LOS (measured)
  receipt.json          { sat, pulse_id, station_pubkey,
                          sha256(iq), sha256(product),
                          doppler_residual_hz, prev_chain_hash,
                          ed25519_sig }   ← the signature binds it all
```

`receipt.json` is the public, verifiable atom. Anyone can: recompute the hashes,
re-fit the Doppler against the public TLE, walk the chain back to a beacon pulse,
and verify the Ed25519 signature — all with the Rail-native verifier.

---

## 6. Long poles (where the years go)

Ranked by difficulty — these are the honest hard problems:

1. **Pure-Rail DSP performance.** FFT + filtering on a live IQ stream is the
   throughput wall. Mitigations already in the toolbox: Metal JIT auto-emission
   (35× fused kernels, [[rail-jit-fused-kernels-plan]]), arena discipline for
   the bump allocator, chunked streaming (never build giant buffers).
2. **QPSK/LRPT demod** (Costas loop + timing recovery + Viterbi + Reed-Solomon)
   — this is the "JPEG-decoder long pole" of the project ([[aigp-vqr1-plan-pure-rail]]
   had the same shape). FM/APT is the warm-up; coherent demod is the real climb.
3. **Full SGP4 in Rail** — hundreds of empirical terms, but deterministic and
   testable against the reference `sgp4` harness we already wrote
   (`scripts/xcheck_sgp4.py`). Falsification-first.
4. **Doppler-fit rigor** — turning "residual looks small" into a defensible,
   adversary-aware confidence score. This is research, not engineering.
5. **USB in Rail** — to delete the C shim and make L1 pure Rail too. Far future;
   the shim is an honest "enough for now."

---

## 7. First principles to hold (so v100 stays Ledatic)

- **Falsify before claiming.** Every propagation/DSP/decode claim gets a
  reference cross-check (we already do — SGP4 harness). No vibes.
- **Honest empty states.** A failed pass is a published INCONCLUSIVE, never a
  hidden failure or a faked frame.
- **The verifier is Rail.** If we can't re-prove a receipt in Rail from scratch,
  it isn't attested.
- **Time is the chain, not the clock.** Anchor to `pulse_id`.
- **Logistics-first surface for any commercial framing, attestation in the
  footer** ([[feedback_logistics_over_attestation]]) — but on substrate
  surfaces, attestation leads.

---

## 8. The pitch, compressed

> Anyone can download a weather image off a satellite.
> ledaticground can **prove it caught one** — that this picture of Earth came
> from that spacecraft, over that orbit, above that backyard in regional, at
> that minute — and hand you a receipt you can verify yourself, in Rail, against
> the physics of the sky.

That's the v100.
