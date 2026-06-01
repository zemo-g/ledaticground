# Channel Intelligence — turning the attested AIS feed into Detroit-River traffic understanding

**Status:** DESIGN (2026-06-01). Gated on the tuned antenna making *vessels* audible — today the
node decodes the fixed infrastructure (AtoN + base station) but not the mobile vessels (a
sensitivity limit, proven by both-channel testing; see `REMOTE_NODE.md`). This document is the
roadmap for the layer that turns vessel data into something genuinely valuable to **port-call
operations** on the Detroit shipping channel, once that data exists.

> The point isn't "another AIS feed." It's an **attested, independent, locally-computed, channel-
> specialist** traffic picture. Coverage is a commodity; provenance + locality + domain expertise
> are not.

---

## 0. The gate (read first)

Everything below has no fuel until the antenna hears vessels. **Step 0 is a number, not a build:**

```
python3 scripts/ais_groundtruth.py --secs 150
```

When that shows freighters (e.g. `H LEE WHITE`, `AMERICAN INTEGRITY`, `ALPENA`) appearing in
*our* decoded feed — not just aisstream's — the channel-intelligence layer becomes real. Until
then it's architecture on paper. Do not build the upper layers against synthetic vessels.

---

## 1. Architecture — four layers, kept strictly separate

The discipline that makes this trustworthy is the separation: **decoded facts are attested truth;
everything inferred is labeled inference.** A port operator must always be able to tell which is which.

### Layer 1 — Sensor (built today)
Attested AIS decode → the timestamped vessel timeline (`data/vessel_log.jsonl`) → ground-truth
comparison (`ais_groundtruth.py`). Output: a live, **signed** stream of *"vessel X at position P,
course C, speed S, observed by this station at time T — provably."* The signature is the moat.

### Layer 2 — Deterministic (Rail, not the LLM)
ETA, meet-points, and sequencing are **kinematics + fixed channel geometry, not language**:
- position + SOG + COG + channel mile-grid → time-to-lock / time-to-dock / time-to-anchorage
- two vessels' projected tracks → **where and when they meet** in the narrows
- arrival ordering at a chokepoint (Livingstone Channel, the locks, a terminal)

This is Kalman / geometry / physics — Rail computes it exactly and **attestably**. An LLM here would
be worse and unverifiable. **The numbers come from this layer.**

### Layer 3 — Reasoning (Studio's local AI, on top of verified facts only)
Where local Qwen (Studio M1 Ultra, 64 GB + Metal) genuinely earns its place — reasoning *over* the
deterministic output and context, never generating the positions:
- **Anomaly** — loitering, AIS-dark gaps ("going dark"), deviation from the normal channel track.
- **Congestion / sequencing** — N vessels converging on a dock or lock → ordering, conflicts, hold time.
- **Destination inference** — AIS Type-5 declared destination (free-text, unreliable) + track + vessel
  type + learned channel patterns → a confident call on where it's actually headed.
- **Port-call brief** — the structured picture rendered as dispatcher-readable language:
  *"next 6 h: 4 up-bound arrivals; AMERICAN INTEGRITY ETA salt dock 14:20 but two ahead — expect ~90-min hold."*
- **The specialist model (PAOS)** — a model fine-tuned on *our own attested Detroit-channel corpus*:
  not a general AI, a model that knows this channel because it learned from provably-real local
  observations. This is the [[paos-specialist-models]] thesis with a sensor behind it.

### Layer 4 — Visualization
- **Live map** — table stakes (we already render the AtoN/vessel map).
- **★ Time–distance (string-line / Marey) diagram** — the key insight. The Detroit River is effectively
  a **single ~28-mile two-way track**, so the railway dispatcher's diagram is the right one:
  - x = time, y = distance along the channel (mile marker)
  - each vessel = a line; **slope = speed**, flat = anchored/docked
  - **up-bound × down-bound line crossings = meets in the narrows**
  A dispatcher reads conflicts and congestion off it at a glance. Deterministic to build (Layer 2 feeds it).
- **Derived plots** — channel occupancy over time, transit-time distributions, anchorage dwell, density.

---

## 2. What's genuinely predictable (and how confident)

| Prediction | Method | Confidence |
|---|---|---|
| ETA to lock/dock/anchorage | kinematic (Layer 2) | high (open channel), degrades near locks/weather |
| Meet-point in the narrows | track projection (Layer 2) | high over short horizons |
| Arrival sequence / ordering | geometry + reasoning (2+3) | medium — human dispatch can override |
| Dwell / wait time | statistical over learned history | medium — improves with corpus |
| Destination | Type-5 hint + inference (3) | medium — labeled as inference, never asserted |
| Anomaly (dark/loiter/deviation) | pattern (3) | good as a *flag*, not a verdict |

**Rule: never hand a port a confident wrong number.** Uncertainty is part of every output. The value
is being *useful and honest*, not *precise and occasionally catastrophically wrong* to someone booking a tug.

---

## 3. The moat — why a port operator would pay

Not coverage (commercial aggregators win raw coverage). The bundle nobody else offers:
- **Attested** — provable observations. When berth/pilot/liability money rides on "when was it
  *actually* here," a signed, tamper-evident record beats a vendor CSV.
- **Independent + local** — our sensor, our compute, data stays local. (We already catch AtoN that
  the crowd-sourced network missed — a genuinely independent receiver.)
- **Channel-specialist** — a model tuned to *this* waterway, not a generic global feed.

Positioning: *"a trustworthy, auditable, channel-expert traffic picture,"* not *"another AIS feed."*

---

## 4. Honest limits / gates

- **Antenna-gated** — no vessel data until the halo hears them (Section 0).
- **One rooftop node on a power bank ≠ production** — this is a proof-of-concept sensor. Path:
  prove the picture is real on this node → *then* discuss hardening / redundancy / a second node.
- **Accuracy is "useful," not "perfect"** — locks, weather, ice, and human decisions bound ETA.
- **Hard wall: decoded fact vs AI inference** — decoded positions are attested truth; everything the
  reasoning layer produces is labeled inference. Never ship a hallucinated vessel to a port.
- **Validation is built in** — `ais_groundtruth.py` measures our coverage AND (later) prediction
  accuracy against aisstream, so claims are measured, not asserted.

---

## 5. The data foundation already exists

When vessels become audible we are not starting from zero — we *turn on* a layer:
- `pi_ais_decode.py` / `src/ais_decode.rail` — exhaustive, deduped decode (extracts every distinct frame)
- `scripts/ais_monitor.sh` — continuous dual-channel capture → `data/vessel_log.jsonl` timeline
- `scripts/ais_census.py --summary` — census + movement flags from the timeline
- `scripts/ais_groundtruth.py` — live reference comparison + accuracy measurement
- `src/ais_attest.rail` — Ed25519-signed reception receipts (the provenance layer)

**First artifact when vessels land:** the live **string-line diagram** + a one-paragraph **port-call
brief** for the next few hours. Simple, high-signal, deterministic + a thin reasoning pass. Prove value
there before layering the specialist model. Everything heavier (PAOS fine-tune, multi-node, SLA) follows
the proof.
