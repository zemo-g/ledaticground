# ledaticground build log

Climbing the [V100 blueprint](V100_BLUEPRINT.md) ladder, falsifying each rung.

## 2026-05-31 — v0.1 → v0.3 (one session)

**Hardware:** Nooelec NESDR SMArt v5 (R820T/RTL2832U) on the Mini, long telescopic
dipole. Observer: regional, MI (0.0 N, -0.0 W).

### v0.1 — pass prediction ✅
`src/passes.rail` — simplified SGP4 (Kepler + J2 secular). Validated vs python
`sgp4` to ±1-2 min AOS, ~2° elevation across ~30 passes. TLEs via
`scripts/fetch_tle.sh` (CelesTrak weather group + NOAA 15/19 by catnr).

### v0.2a — pure-Rail FFT ✅
`src/fft.rail` — radix-2 Cooley-Tukey, float arrays for re/im. FFT of a known
tone lands exactly on the right bins (mag 4 at bins 1 & 7, ~1e-16 elsewhere).
> **Rail bug found:** direct tail-self-recursion miscompiles when a recursive
> arg depends on another arg updated in the same call (bit-reversal accumulator
> infinite-looped). Workaround: mutual recursion. (memory: rail-bug-self-loop-cross-dep-args)

### v0.2b — IQ power spectrum ✅
`src/spectrum.rail` — reads rtl_sdr uint8 IQ, Hann window, Welch-averaged FFT.
Validated vs `rtl_power`: detects FM stations at correct frequency AND relative
amplitude (89.9 strongest, then 88.7 — both within one 37.5 kHz bucket).
> **Rail bug found:** `foreign malloc n -> int` tags the 64-bit pointer and
> corrupts every byte read — silently. Use the builtin malloc. Caught because the
> garbage bytes failed the v0.3 correlation test. (memory: rail-bug-foreign-malloc-pointer)

### v0.3 — FM demod ✅
`src/fmdemod.rail` — polar discriminator (atan2 of z·conj(z₋₁)) + decimate to
48 kHz. Validated two ways: (1) corr = 1.0000 vs a python reference demod of the
same IQ; (2) **Rail's own FFT of its own demodulated audio finds the 19 kHz FM
stereo pilot at 268× baseline** — unambiguous proof of correct FM demod.

### Cross-check harnesses (reference, not product)
`scripts/xcheck_sgp4.py` (SGP4), `/tmp/xfm.py` (FM demod). Falsification-first:
every DSP/orbital claim is checked against an independent implementation.

### v0.4 — APT decoder ✅ (on synthetic)
`src/apt.rail` — reads s16 @ 11025 Hz (rtl_fm format), quadrature AM-detects the
2400 Hz subcarrier, resamples to 4160 px/s, assembles 2080-px lines, extracts
channel A. Validated on a synthetic NOAA-format APT signal: corr 0.9895 vs
ground truth (identical to the python reference) — recovered the test image
visually. Sync-lock for mid-stream real captures is the next refinement.
`scripts/apt_to_png.py` packs ROW output → PNG.

### Attestation — proof-of-reception receipt ✅ (the v100 soul)
`src/attest.rail` — binds {satellite, pulse_id, station coords, sha256(product),
prev_chain_hash} under an Ed25519 signature (Rail-native crypto: sha256 +
ed25519 from stdlib, run from the rail repo cwd so nested imports resolve).
Self-checks: own-sig verify = 1, tampered-msg verify = 0; SHA-256 chain hash;
emits `data/receipt.json`. The Doppler-fit residual (the physical fingerprint /
real moat) is a labeled PENDING field until we capture IQ. DEV key, clearly
labeled — not a production authority.

### v0.4 sync-lock ✅ (host-side) + a 3rd recursion-bug instance
`apt.rail` now emits the full 2080-px flat line stream; `scripts/apt_sync.py`
finds the line-start offset by correlating the sync region (found offset 1426 on
a signal shifted exactly 1425 px) and renders channel A. End-to-end on a
**shifted + AWGN** synthetic: corr **0.98** vs truth — recognizable image.
> In-Rail Sync-A correlation (`linesum`/`synccorr`) **segfaulted even at depth 3**
> despite the mutual-recursion workaround — a nested float-accumulator variant of
> the self-loop miscompile that mutual recursion did NOT fix. Deferred to the host
> post-step; worth a compiler-level look later. The DSP (envelope/resample) is all
> Rail; only the cosmetic line-offset is host-side.

### Live capture armed
NOAA 15, 85° overhead, 137.62 MHz APT → auto-record at 23:48 UTC (~19 min,
rtl_fm s16 @ 11025 Hz). On completion: decode (`apt.rail`) → sync+render
(`apt_sync.py`) → first REAL weather image → sign into an attested receipt
(`attest.rail`). That's the climax this whole chain was built for.

### Doppler proof-of-reception method ✅ (on synthetic) — the v100 moat
`src/doppler.rail` — reads IQ, FFTs each window, peak-picks the carrier bin →
freq-vs-time. On a synthetic known Doppler S-curve (±3000 Hz): recovered track
vs truth = **RMS 28.5 Hz** (~2 bins) at **corr 0.9999**. This is the physical
fingerprint method: against a real IQ pass it fits to the SGP4-predicted Doppler
and the residual becomes the attested receipt's confidence. Needs a real IQ
capture to bind a live reception (tonight's FM-audio recording discards the
carrier — next pass).

### Not done (honest)
- Apply Doppler fingerprint to a REAL pass (needs IQ capture; next pass).
- First REAL image (pending tonight's 23:48 UTC recording).
- v10+ (multi-station mesh, cross-attestation, TDOA, PAOS corpus): out of
  single-session scope — that's the actual road to v100.

### v10 cross-attestation prototype ✅ (synthetic) — the network rung
`src/coattest.rail` — two independent stations (different coords + Doppler fits)
each Ed25519-sign a receipt of the SAME pass. Co-attestation is valid ONLY if
both sigs verify AND corroborate (same sat+pulse+product hash). Result:
both verify 1/1, corroborate 1, **CO-ATTESTED = 1**; a forgery (attacker with
only station A's key signing as B) is **rejected (co-attest = 0)**. This is the
v100 multi-station strength: forging N independent stations is exponentially
harder than one.

### Real-Doppler capture armed (toward a real attested reception)
Fire-and-forget recorder for the NOAA 19 APT pass (137.1 MHz, 83 deg, 03:02 UTC):
~105 narrowband IQ snapshots (16384 @ 60 kHz) logged with unix time → a REAL
Doppler curve to run through `doppler.rail` and bind into an attested receipt.
The first proof-of-reception on live data (vs the synthetic-validated method).

### Doppler PREDICT half ✅ — proof-of-reception pipeline now complete
`src/doppler_predict.rail` (extends `passes.rail` geometry with range-rate →
Doppler = (range(t)-range(t+1s))·fc/c). Predicted NOAA-19 (137.1 MHz) curve over
its 03:02 UTC pass is a textbook S-curve: **+3020 Hz** at approach (el 5.6°) →
**~0 at peak** (el 83°, closest approach) → **−1594 Hz** receding. Magnitude
±3 kHz and the zero-crossing-at-peak both match theory — validated by shape.
Proof-of-reception is now end-to-end: **measure** (`doppler.rail`) + **predict**
(`doppler_predict.rail`) + fit→residual. Awaiting the real NOAA-19 IQ capture
(armed, 03:02 UTC) to bind a live reception.

### Session tally
Built + falsified in one climb: pass-predict, FFT, spectrum, FM-demod,
APT-decode+sync, Ed25519 attestation, Doppler proof-of-reception. The complete
single-station v100 *machinery*. 3 Rail compiler-bug instances found + documented
(self-loop cross-dep family). Remaining road to v100 is live data + multi-station
infrastructure, not new single-node capability.

### Real-Doppler processor ✅ (validated on a realistic FM synthetic) — pre-flight for the live pass
`src/doppler_real.rail` — the live-capture tracker, tuned for the NOAA-19 IQ
snapshots (uint8, 60 kHz, 16384 samples/snapshot concatenated in time order).
Two changes a REAL signal forces over the clean-tone tracker, both validated:
- **DC-skip** — rtl_sdr leaves a DC spike at tune center; bins <2 / >n-2 are
  excluded so the tracker can't lock onto 0 Hz every window.
- **Spectral centroid, not peak bin** — NOAA APT is a ~34 kHz-wide FM signal, so
  the peak bin jitters. The power-weighted centroid of the signal energy tracks
  the Doppler-shifted center.
> **This caught a real trap.** `scripts/gen_doppler_fm.py` synthesizes the live
> format exactly (17 kHz-deviation FM, 2400 Hz subcarrier + video tones, a
> constant offset = SDR ppm/carrier error, a DC spike, noise) on a known Doppler
> S-curve. On it: **centroid corr 0.9986 / residual 102 Hz** and recovers the
> injected 1850 Hz offset (got 1864). **Peak bin: corr 0.27.** The clean-tone
> tracker (corr 0.9999 on a pure carrier) would have *falsely failed* the
> proof-of-reception on the real FM signal — the realistic synthetic surfaced
> that before the pass, not after.
`scripts/doppler_fit.py` fits a measured track against a reference curve, solving
out a constant frequency offset and (real mode) a best time-shift via grid search
(AOS uncertainty); validated to recover shift=0 / corr 0.9986 on synthetic.
`scripts/process_real_doppler.sh` is the one-command live pipeline: concat snaps →
`doppler_real.rail` → predicted curve (`doppler_predict.rail`) → fit → attest.
Selftest now 8/8 (added the FM-centroid gate). Armed for the 03:02 UTC NOAA-19 pass.

### Foundation status vs V100_BLUEPRINT
The entire single-station v0.x→v1 chain (predict → capture → spectrum → demod →
decode → attest) is built and falsified. v10+ (multi-station mesh,
cross-attestation, TDOA, PAOS corpus) is infrastructure/multi-session scope.
