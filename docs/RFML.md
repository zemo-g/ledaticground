# RFML — learning to characterize signals, on an attested substrate

**Status:** EXPERIMENT — **first result in, both gates PASS (2026-06-01).** First brick of the
"machine that speaks radio" idea, with the hype stripped out and the part physics rewards kept in.

## Result (2026-06-01) — both gates pass, in pure Rail

- **Gate A (held-out synthetic):** a 5-class softmax **trained in Rail** (feature extraction + SGD,
  all on the substrate) scores **299/300 = 99.7 %** held-out — matching the Python oracle (logreg
  100 %, MLP 100 %) and crushing the nearest-centroid floor (74 %). The Rail features match the
  Python `feats()` to 5+ decimals, exactly.
- **Gate B (synthetic→real transfer, the headline):** the synthetic-trained classifier, shown 46 s
  of **real off-air AIS** it has never seen (`data/ais_clean_a.s16`), returns **noise = 528, msk =
  13** — *bit-identical* between the Python and the Rail-trained models. Every one of the 13 `msk`
  windows is an independently-confirmed burst (100 % containment in the deterministic roughness
  detector); **zero** `msk` false-alarms on idle. Transfer works.

**What it means:** a model trained only on synthetic signals correctly recognizes real off-air
modulation. So we can characterize bands we've never hand-coded by training on synthetic models of
them — the self-labeling flywheel has legs. And the whole chain — decode, features, train, classify
— runs on one verifiable substrate. Honest caveat: the classifier is conservative (13 of ~108
candidate-burst windows), high-precision / lower-recall, consistent with our antenna-limited SNR.

Artifacts: `scripts/gen_modclass.py` (corpus) · `scripts/modclass_proto.py` (Python oracle +
nearest-centroid floor) · `src/modfeat.rail` (Rail feature extractor) · `src/modclass.rail` (Rail
feature-extract + softmax SGD + eval) · selftest gate "rfml modclass held-out >=95%".

## What this is (and what it is not)

The romantic version — *"an LLM that learns to cipher/uncipher any band"* — hits three walls:

1. **Decode ≠ decrypt.** Demodulating RF into bits is signal processing (learnable). Turning
   *ciphertext* bits into meaning is cryptography (not learnable from IQ — that's the key, by design).
   We stay on **open broadcast** (AIS, NWR/SAME, NOAA APT). Encrypted bands are out of scope, full stop.
2. **For a *known* protocol, our deterministic Rail decoders are already optimal.** A matched
   receiver on a known code is Shannon-optimal; our Viterbi hits 0 bit-errors @ 4 dB. ML can at best
   tie it. ML only *wins* where the channel/modulation is unknown or analytically intractable.
3. **An LLM is the wrong shape for raw IQ.** Token models aren't the inductive bias for complex
   baseband. The language model only earns its place *above* the decode — reasoning over decoded,
   attested message streams (the PAOS / channel-intelligence layer).

So the honest, buildable thing is a **blind signal characterizer**: given an unknown window, say
*what kind of signal it is* (modulation class, and later baud/center/SNR) so the node can route it to
the right deterministic decoder. Today the node decodes only the four protocols we hand-coded; a
characterizer turns it into "characterize whatever's on the air."

## The edge: the oracle is free, external, and attested

The scarce resource in RF machine learning is **labeled data with provenance**. Everyone trains on
synthetic (RadioML is simulated). We mint real labels for free:

> **deterministic Rail decoder = the oracle.** Every time `ais_decode` succeeds it yields an
> audio-window ↔ verified-message pair, Ed25519-signed at reception, cross-checkable against an
> independent reference (aisstream via `ais_groundtruth.py`).

This is the **compile-as-oracle** pattern from Rail training, applied to radio. The deterministic
decoder self-labels the easy cases; the model's job is to extend coverage where the decoder fails —
and we can *measure* that extension honestly, because we have a floor (the decoder) and a truth
(aisstream). No proxy-metric trap.

## Tonight's experiment — the one falsifiable question

> **Train a modulation classifier on our synthetic generators only. Does it recognize the
> modulation of the REAL off-air AIS we captured (`data/ais_clean_a.s16`)?**

- **Yes** → synthetic→real transfer works → we can characterize bands we've never hand-coded by
  training on synthetic models of them. The flywheel has legs.
- **No** (real AIS gets called "noise"/"FSK") → the synthetic↔real gap is real → we *must* train on
  attested real captures (antenna-gated). Also a real, useful finding.

### Domain (must match the real data)
FM-demod audio @ **48 kHz, s16 mono** — exactly what `rtl_fm -M fm -s 48000` produces, which is what
`data/ais_clean_a.s16` *is*. Synthetic must go through the same RF→discriminator-demod chain, or the
idle-noise statistics won't match (FM-demodulated thermal noise is rough ±π; that's a real feature).

### Classes (physically coherent in the audio domain), chance = 20%
| class | what | real analogue |
|---|---|---|
| `noise` | complex-Gaussian RF, FM-demodulated | idle squelch (rough ±π) |
| `carrier` | unmodulated tone | NWR carrier (roughness 1768 vs 11009) |
| `afsk` | audio FSK tones (SAME-like, 1562/2083 Hz) | NWR SAME burst |
| `fsk` | direct 2-FSK, different shift | generic data carrier |
| `msk` | Gaussian MSK, 2400 Hz dev, 9600 baud | **AIS** (`ais_clean_a.s16`) |

*(QPSK/LRPT is an IQ-domain modulation — belongs to a separate IQ characterizer, future work.)*

## Success / abort metric — written BEFORE training (the 2026-04-06 rule)

- **Gate A — features separate modulations:** 5-class held-out synthetic accuracy **≥ 70 %**
  (chance 20 %). Below that → the features are useless; fix features before any NN.
- **Gate B — the headline, synthetic→real transfer:** windows of the real `ais_clean_a.s16` that the
  deterministic decoder confirms hold an AIS burst are classified **`msk`** by a majority; idle
  windows are classified `noise`/`carrier`, **not** `msk`. If real AIS bursts get called
  `noise`/`fsk` → transfer FAILS → honest finding (need real-data training).
- **Baseline-beat rule:** the deterministic **nearest-centroid** classifier is the floor. A trained
  softmax/MLP must *beat it* on held-out to justify its complexity — else we ship the centroid.

## Why Rail (not a Python notebook)
Feature extraction + classification run **in Rail** — the substrate. It's the ML-in-Rail frontier
(`fft.rail` + `autograd.rail` + `optim.rail` already in tree), and it keeps the whole chain — decode,
features, classify, attest — on one verifiable substrate. Python is used only to synthesize the
fixture signals (we already have the generators) and to glue ground-truth.

## Pipeline (this build)
1. `scripts/gen_modclass.py` — synthetic 5-class corpus, RF→FM-demod chain, varied SNR → s16 windows + labels.
2. `src/modfeat.rail` — per-window feature vector (spectral via FFT + instantaneous-amplitude variance + lag-1 roughness + zero-crossing rate).
3. Rail classifier — nearest-centroid baseline, then trained softmax if it beats the floor.
4. Eval — held-out synthetic accuracy + confusion matrix → **Gate A**.
5. Real-transfer test — classify windows of `data/ais_clean_a.s16` → **Gate B**.
6. selftest gate + commit.

## Honest gates
- This characterizes the **audio domain** only (IQ-domain modulations are separate, future).
- Synthetic training is a *first* answer; the real moat is training on attested captures, which the
  next-antenna sensitivity work unlocks.
- A characterizer **routes** to deterministic decoders; it does not replace them. The deterministic
  decoder stays the source of truth and the label oracle.
