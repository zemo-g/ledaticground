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

### TDOA — multi-station geometric proof ✅ (validated on synthetic) — the v30 rung
`src/tdoa.rail` — two stations record the SAME pass; the signal reaches each with a
time difference tau(t) = (range_A(t) - range_B(t))/c set purely by the orbit and the
two station coordinates. A windowed cross-correlation of the two recordings recovers
tau(t); matching it to the SGP4-predicted differential range is a STRICTLY STRONGER
proof than co-attestation — forging it means faking a consistent geometry at two
places at once. On synthetic two-station recordings with a known +/-50-sample
time-varying differential delay (a ~300 km baseline shape): lag recovery
**corr 1.0000, 20/20 windows exact, RMS 0.00 samples**.
> **3rd-instance fix of the float-accumulator self-loop miscompile.** The
> cross-correlation sum first segfaulted (exit 139) because it carried a float
> accumulator whose per-step update depends on the loop index (`acc + a[i]*b[i+lag]`)
> — same class as apt.rail's synccorr. Fix: accumulate into a 1-element mutable
> float array so the tail recursion carries only int indices. Clean, reusable.
Honest limitation: sample-synchronous clocks are assumed; the real mesh needs
GPS-PPS timing discipline (roadmap, not solved here). Selftest now 9/9.

### v40 capstone — unified multi-physics proof bundle ✅ — the v100 receipt shape
`src/bundle.rail` — recombines every validated method into one signed object: two
stations co-sign a bundle binding {sha256(product) + each station's Doppler-fit
residual + their mutual TDOA + a beacon-pulse anchor (not wall-clock)}. Valid only
if both Ed25519 sigs verify AND the physics corroborates (same product, both
Doppler residuals within tolerance, TDOA present): **BUNDLE VALID = 1**. The same
forgery as coattest (attacker holds only A's key) is **rejected = 0**. This is what
all the rungs add up to — forging it means a consistent orbit+geometry at two places
at once *and* both station keys. selftest now 11/11.

### LRPT decode chain (METEOR-M, digital QPSK — NOT analog APT) — in progress
The high METEOR passes carry LRPT: QPSK, CCSDS r=1/2 K=7 conv-coded, randomized,
RS(255,223), JPEG-ish. Building it bottom-up, each rung falsified on synthetic.

**Rung 1 — Viterbi ✅** `src/viterbi.rail`: soft-decision, 64-state, G1=0o171 G2=0o133
(the LRPT/CCSDS code). int metrics in arrays + per-step normalization + array-cell
traceback (no float-accumulator miscompile). vs reference encoder+BPSK channel:
**0 bit errors at 4 dB; corrects all 640 channel symbol-errors at 3 dB to zero**;
degrades near threshold (~1 dB) as theory predicts.

**Rung 2 — QPSK carrier recovery ✅** `src/qpsk.rail`: decision-directed 2nd-order
Costas loop, NCO state in a float-array cell. Recovers an unknown carrier
frequency+phase: locked freq matches injected (0.004/0.008/-0.005/0.012 cyc/samp
all tracked), **0.0000 steady-state SER after lock**. Pull-in time scales with
offset (narrow-band Costas) — a coarse FFT freq pre-estimate is the documented
refinement to shorten acquisition. 4-fold phase ambiguity resolved downstream.

**Rung 3 — CCSDS derandomizer ✅** `src/derand.rail`: continuous 8-bit LFSR,
h(x)=x^8+x^7+x^5+x^3+1, init 0xFF (output MSB / shift-left / fb=b0^b2^b4^b7).
Reproduces the **published CCSDS PN sequence FF 48 0E C0 9A 0D 70 BC** exactly
(validated against the standard, not just self-round-trip) and XOR-derandomizes a
payload to the expected bytes. xor via (a|b)-(a&b). LFSR state in an array cell.

**Remaining rungs (roadmap):** RRC matched filter + Gardner symbol-timing recovery
· bit-ordering/phase de-ambiguity · frame sync (0x1ACFFC1D ASM) · Reed-Solomon
(255,223), interleave 4 · JPEG-ish decompress → METEOR image. selftest 14/14.

### First real NOAA 15 pass (2026-06-01 00:09 UTC) — NOISE, antenna-limited (honest)
Clean 25 MB / 18.9-min capture of the 81° NOAA 15 pass, SDR locked to 137.620 MHz.
But the audio is **pure noise floor**: the 2400 Hz APT subcarrier sits at only
**1.0–1.5× the noise floor across the whole pass** (a real signal is 5–20×). No
satellite signal was received. Diagnosis: **front-end, not software** — the SDR's
telescopic whip can pull a megawatt local FM broadcast but not a ~5 W, 137 MHz,
800+ km weather-sat downlink. The decode chain is proven correct on synthetic;
it just needs a real signal. **No receipt kept** — attesting noise would be fake
evidence. Fix = a proper 137 MHz antenna with sky view (see REMOTE_NODE.md).

### Remote node kit (toward a real signal + station #2) — built
`docs/REMOTE_NODE.md` + `scripts/pi_capture.sh` (Pi Zero 2 W records a pass, ships
to the Mini over Tailscale) + `scripts/recv_decode.sh` (Mini decodes + attests under
the remote station's identity). `attest.rail` station coords now file-driven
(station_name/lat/lon, regional defaults) — a remote node attests under its own
coords with no code change, which is also exactly what the live 2-station TDOA /
bundle needs. 137 MHz V-dipole spec: two 53.4 cm legs at 120°, horizontal, N–S.
selftest 14/14.

### AIS decode chain (162 MHz ship tracking — Detroit River / Great Lakes) — in progress
The roof node hears the marine VHF band strongly (162 MHz peak ~18x noise floor, both
AIS channels 161.975/162.025 lit at ~9-11x) — even on the 137-tuned V-dipole. Building
the AIS decoder bottom-up, same falsify-each-rung method as LRPT.

**Rung 1 — GMSK demod ✅** `src/gmsk.rail`: AIS is MSK (9600 baud, +/-2400 Hz dev). A
differential phase discriminator (`disc[n]=Q[n]I[n-1]-I[n]Q[n-1]`, the imag part of
z·conj(z₋₁) — no atan2) + per-symbol integrate-and-slice recovers bits. vs synthetic
GMSK: **BER 0.0000 @ 15 dB, 0.005 @ 10 dB**, degrades near 3 dB (normal for a
discriminator demod; real AIS from ships is strong). Float sums in array cells (self-loop-safe).

**Rung 2 — Type-1 parser ✅** `src/ais_parse.rail`: 168-bit payload -> message type, MMSI,
lon/lat (28/27-bit two's-complement, 1/600000 deg), SOG, COG, per the AIS bit-field spec.
Big-endian bit-fold via int array-cell accumulator. vs a known encoded ship report:
**MMSI + lat/lon + sog + cog all exact** (MMSI 366123456, 0.0/-0.0, 7.2 kn).

**Rung 3 — CRC-16/X-25 ✅** `src/crc16.rail`: reflected (poly 0x8408, init+xorout 0xFFFF), matches the published check value 0x906e for "123456789". The HDLC frame validator.

**Remaining AIS glue:** clock recovery (Gardner) ·
NRZI decode · HDLC deframe (0x7E flags + bit-destuff) · CRC-16-CCITT. Tie-in: a live
Detroit-River vessel feed for the Great Lakes logistics work.

### Foundation status vs V100_BLUEPRINT
The entire single-station v0.x→v1 chain (predict → capture → spectrum → demod →
decode → attest) is built and falsified. v10+ (multi-station mesh,
cross-attestation, TDOA, PAOS corpus) is infrastructure/multi-session scope.
