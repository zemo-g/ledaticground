# LNA Procurement + Integration Spec — closing the ~8 dB sensitivity deficit

> **Status:** software interlock (`BIASTEE-1`) is **READY NOW**. The LNA itself is the
> **procurement blocker** to live gain. This doc is the buy list + integration steps.
> Ticket **BIASTEE-2** (doc-only). Cross-linked from `docs/ANTENNA.md`.

---

## 0. The problem this LNA solves

The empirical diagnosis in `docs/ANTENNA.md` (2026-06-01) is sensitivity-limited reception:
the 137 MHz weather downlink is a weak-far signal (~5 W, 800+ km up) and the roof node sits
about **~8 dB short** of a clean decode margin. Per memory `ledaticground-remote-node-pi`
the deficit is **"~8 dB = LNA-sized"** — i.e. it is the kind of gap a properly placed
low-noise preamplifier closes, not something a different antenna alone fixes.

A low-noise amplifier (LNA) placed **at the antenna feedpoint** sets the **system noise
figure (NF)** before the coax loss and the SDR's own (mediocre) front-end NF dominate the
budget. That is the entire mechanism: cascade NF is set by the *first* stage's NF plus the
loss *ahead* of it. Put a ~1 dB-NF, ~20 dB-gain stage first, and everything downstream
(coax loss, SDR NF) is divided down by the LNA gain in the Friis cascade.

---

## 1. TWO BANDS — TWO LNAs. Do not conflate them.

There are now **two separate RF bands in play**, each needing its **own** LNA, and the
400 MHz band needs its **own antenna** as well. **These are two distinct procurements.**

| | Band (a): **137 band** | Band (b): **400 band** |
|---|---|---|
| Frequency | **137–138 MHz** | **400–406 MHz** |
| What it serves | NOAA APT / METEOR LRPT, Orbcomm, 137 beacon work. **NOT AIS:** AIS / mobile vessels are **162 MHz**, *outside* this SAW passband — a 137-band LNA **rejects** them (see "Why a SAW" below). The CHANNEL_INTELLIGENCE.md Section 0 mobile-vessel gate needs a **separate 162 MHz front-end**, not this LNA. | **RS41 / DFM radiosonde** decode (`src/rs41_*.rail`) |
| Antenna | the **existing** 137MLCHD halo (already on the roof, `docs/ANTENNA.md`) | a **SEPARATE 400 MHz antenna — not yet owned, not in this buy unless explicitly approved** |
| LNA | **Sawbird+ 137 / "NOAA" variant** (SAW passband 137 MHz) | a **different** 400 MHz LNA (Sawbird GOES is 1.6 GHz — wrong; needs a 70 cm / 400 MHz SAW or wideband LNA) |
| Priority | **PRIMARY BUY** — closes the measured 137 deficit, broad payoff | **deferred** — RS41 stays synthetic-only until the 400 MHz antenna + LNA land |

**Why the split is load-bearing:** a SAW-filtered LNA only low-noise-amplifies *inside its
SAW passband*. A 137 MHz Sawbird+ does **nothing useful** at 400 MHz, and a 400 MHz LNA does
nothing at 137. The RS41 chain (`src/rs41_decode.rail` etc.) is fully built and
synthetic-validated, but its **live reception is gated on a 400 MHz antenna + 400 MHz LNA**
that the node does not have. Buying one LNA does not unlock both bands.

> **Recommendation:** buy the **137-band LNA first** (broad payoff: weather imaging margin,
> Orbcomm, and 137 beacon sensitivity — **not** AIS, which is 162 MHz and out of this passband).
> Treat the 400 MHz LNA + antenna as a later,
> separate purchase tied to whether live RS41 reception is in scope.

---

## 2. Recommended part — 137-band LNA

**Class:** Nooelec **Sawbird+ 137** (the "NOAA" / 137 MHz variant) — or an equivalent
**SAW-filtered, bias-tee-powered LNA** centered on the 137–138 MHz weather band.

| Requirement | Target | Why |
|---|---|---|
| **Noise figure (NF)** | **≤ 1 dB** | The deficit is sensitivity-limited. A ≤1 dB-NF first stage *sets* the system NF; this is what actually recovers the ~8 dB. |
| **Gain** | **~20 dB** | Enough to swamp coax loss + SDR NF in the Friis cascade, not so much it overloads on strong local signals. |
| **SAW filter** | **passband 137–138 MHz** | Rejects out-of-band (broadcast FM, pagers, the strong local 162 MHz marine/AIS) so the LNA doesn't amplify interferers into compression. |
| **Bias-tee powered** | **DC-pass on the RF port, accepts ~4.5 V up the coax** | So the existing coax carries DC power up to the feedpoint — **no separate power run on the roof.** Fed by the `BIASTEE-1` path (`rtl_biast -b 1`). |
| **Connectors** | SMA (match to the SDR / adapter chain in `docs/ANTENNA.md` §Install step 1) | Antenna is SO-239; plan the adapter chain so the LNA sits inline at the feedpoint. |
| **Cost** | **~$30–50** | Matches the procurement budget. |

**Do NOT buy:**
- A Sawbird **GOES** (1.6 GHz) — wrong band.
- A non-DC-pass / non-bias-tee LNA — it would need a separate roof power run, defeating the
  whole point of the bias-tee path.
- A high-gain (>30 dB) wideband (no-SAW) "TV" preamp — it amplifies the strong 162 MHz AIS
  and broadcast FM into the front end and can desensitize the whole chain.

---

## 3. Gain budget — how ~1 dB NF + ~20 dB gain closes ~8 dB

Cascade noise figure follows **Friis**. With the LNA *first* (at the feedpoint, before the
coax loss), the system NF is dominated by the LNA's own NF; the coax loss and the SDR's NF
that follow are divided down by the LNA's ~100× (20 dB) gain.

Order-of-magnitude budget (illustrative — confirm against the real coax run + SDR datasheet):

```
WITHOUT LNA (today):
  antenna -> coax loss (~3-4 dB) -> SDR front-end (NF ~5-7 dB)
  system NF ≈ coax_loss + SDR_NF ≈ ~8-11 dB        <- this is the ~8 dB-class deficit

WITH LNA at feedpoint:
  antenna -> LNA (NF ~1 dB, gain ~20 dB) -> coax loss (~3-4 dB) -> SDR (NF ~5-7 dB)
  system NF ≈ LNA_NF + (coax_loss + SDR_NF - LNA_gain)/... (Friis: later-stage NF
             contribution shrinks by ~20 dB of gain)
  system NF ≈ ~1 dB  (the LNA's own NF now dominates)
```

Net: system NF drops from **~8–11 dB → ~1 dB**, i.e. an **~7–10 dB** improvement in
sensitivity — squarely the **~8 dB** the node is short. This is the engineering path to:
- making the **137 weather decode** clean where the halo-only chain is marginal,
- improving **Orbcomm / beacon** link margin,
- making **mobile VESSELS audible** (the CHANNEL_INTELLIGENCE.md Section 0 mobile-vessel
  sensitivity gate).

**Placement is the whole game:** the LNA's NF only helps if **nothing lossy precedes it.**
Put the LNA **AT the antenna feedpoint**, before the coax. An LNA bolted at the SDR end
(after the coax loss) recovers almost none of the deficit — the coax loss has already raised
the noise floor. (See §5.)

---

## 4. The existing inline FM band-stop filter — do not double-filter destructively

There is already an **inline FM band-stop filter** on the feed (per the cluster design /
node build). It stays. **Confirm the LNA's SAW passband and the existing FM band-stop do not
fight:** the FM band-stop notches the broadcast FM band (88–108 MHz), and the Sawbird+ 137
SAW passes only 137–138 MHz — they should be complementary, not destructively overlapping.
Verify the cascade insertion loss in-band (137 MHz) is dominated by the LNA gain, not eaten
by stacked filter loss. If the SAW LNA already rejects FM adequately, the separate FM
band-stop may become redundant; keep it unless measurement shows it costs in-band margin.

---

## 5. Topology — where each piece goes

```
   ANTENNA (137MLCHD halo, roof)
      |
      |  <-- LNA HERE, at the feedpoint (before any coax loss).  ≤1 dB NF, ~20 dB gain.
      |       DC-powered via the coax (bias-tee). SAW-filtered to 137-138 MHz.
      |
   [existing inline FM band-stop filter — confirm complementary, see §4]
      |
   coax run (carries RF down, DC power up)
      |
   bias-tee  <-- AT THE SDR END.  Injects ~4.5 V onto the coax.
      |          This is the RTL-SDR Blog V3's BUILT-IN bias tee
      |          (enabled by `rtl_biast -b 1`, see §6). No external tee needed.
      |
   RTL-SDR Blog V3  (the SDR)
      |
   Pi  (decode chain)
```

- **LNA** = at the antenna feedpoint, roof side.
- **Bias-tee** = at the SDR end (built into the RTL-SDR Blog V3).
- **DC flows up** the same coax that carries RF down — **no separate roof power run.**

---

## 6. Enabling bias-tee power — and the fail-CLOSED software interlock (`BIASTEE-1`)

The RTL-SDR Blog V3 has a **built-in bias tee** that injects ~4.5 V DC onto the antenna
port. The enable path:

```
rtl_biast -b 1     # bias tee ON  (DC up the coax to power the LNA)
rtl_biast -b 0     # bias tee OFF (safe to call anytime)
```

> **⚠ The danger:** energizing ~4.5 V DC up the coax into an antenna with **NO LNA** (or a
> DC-shorted / direct-to-ground feed) can damage the SDR front-end or the feed. Bias power
> must therefore be **interlocked** so it is **never** turned on without an LNA actually
> installed.

### The interlock that `BIASTEE-1` implements (`scripts/autocap/bias_tee.sh`)

`bias_tee.sh on|off|status` — and **`on` is fail-CLOSED, gated on TWO conditions**:

1. **`data/lna_present.txt` content is exactly `yes`** — someone physically installed an LNA
   and declared it. **This file ships ABSENT** (it does not exist today, confirmed), so the
   system is fail-safe by default: bias-tee stays OFF until an operator both installs the LNA
   *and* writes `lna_present.txt=yes`.
2. **AND an LNA model file is set** (`~/.iq/lna_model`, naming a known DC-pass LNA such as
   the Sawbird+) — a second guard so "yes" alone isn't enough; the declared part must be a
   known bias-tee-powered LNA.

If **either** gate fails, `bias_tee.sh on` prints
**`REFUSED: no LNA declared present — never energize an empty/DC-shorted feed`**, exits
non-zero, and **`rtl_biast` is never invoked**. (The two conditions are ANDed; the discipline
is fail-closed — the same gating shape carried from the Rail decoders, even though bash
itself short-circuits.)

`off` **always** runs `rtl_biast -b 0` (safe to call anytime).
`status` reads back the current state.

### Coupled to active-capture only (never left hot)

`BIASTEE-1` wires bias power into the capture path (`scripts/autocap/pi_iq_capture.sh`) so DC
is energized **only while the SDR is actively capturing**:

- In `do_capture()`, **after** `systemctl stop roofmon` and **before** `rtl_sdr`: call
  `bias_tee.sh on` (a no-op / REFUSED if no LNA is declared).
- In the `TERM`/`INT` trap **and** after `rtl_sdr` exits: call `bias_tee.sh off`.

This guarantees the tee is **de-energized whenever the SDR is released** — never left hot
between captures.

> **`rtl_biast` GPIO caveat:** on some dongles `rtl_biast -b 1` is **sticky** across
> `rtl_sdr` invocations (the GPIO state persists). The **explicit `-b 0` in the release
> trap** is the guarantee that the tee does not stay energized after a capture ends. Do not
> rely on bias power clearing itself.

---

## 7. PROCUREMENT CHECKLIST

**137-band LNA (PRIMARY BUY — closes the ~8 dB deficit):**

- [ ] **Nooelec Sawbird+ 137** ("NOAA" / 137 MHz variant) or equivalent SAW-filtered LNA
- [ ] NF **≤ 1 dB** (confirm on the datasheet)
- [ ] Gain **~20 dB**
- [ ] SAW passband **137–138 MHz** (rejects FM / pager / 162 MHz AIS out-of-band)
- [ ] **Bias-tee powered** — DC-pass RF port, accepts **~4.5 V** (RTL-SDR Blog V3 tee output)
- [ ] Connectors / adapters to sit **inline at the feedpoint** (plan the SMA / SO-239 chain
      per `docs/ANTENNA.md` Install step 1)
- [ ] Budget **~$30–50**

**400-band LNA + antenna (DEFERRED — RS41 live reception only):**

- [ ] A **separate** 400–406 MHz LNA (NOT a 137 Sawbird, NOT a 1.6 GHz GOES Sawbird)
- [ ] A **separate** 400 MHz antenna (the 137 halo will NOT receive 400 MHz)
- [ ] Same requirements: ≤1 dB NF, ~20 dB gain, bias-tee-powered if it shares an SDR/coax
- [ ] **Hold until** live RS41 reception is explicitly in scope (chain is synthetic-only today)

---

## 8. INTEGRATION STEPS (137-band, when the LNA arrives)

1. **Mount the LNA at the antenna feedpoint** (roof, before the coax). Confirm SO-239/SMA
   adapter chain so the LNA's input sees the halo with minimal loss ahead of it.
2. **Confirm the inline FM band-stop filter is complementary** to the Sawbird+ SAW passband
   (§4) — measure in-band insertion loss; don't let stacked filters eat the gain.
3. **Verify the bias-tee feeds DC up the coax** to the LNA: at the SDR end, `rtl_biast -b 1`
   should power the LNA. Confirm the LNA's DC draw matches the V3 tee output (~4.5 V).
4. **Declare the LNA to the software interlock** (this is what arms the bias tee):
   - write `data/lna_present.txt` = **`yes`**
   - set `~/.iq/lna_model` to the part name (e.g. `Sawbird+ 137`)
   - sanity-check: `bias_tee.sh status` and `bias_tee.sh on` should now actually invoke
     `rtl_biast -b 1` instead of printing `REFUSED`.
5. **Capture-path is already wired** (`BIASTEE-1`): the next capture energizes the tee before
   `rtl_sdr` and de-energizes it in the release trap. No further capture-script edits needed.
6. **Re-score the antenna/LNA chain** with the existing tooling: run
   `scripts/antenna_score.sh` (162 baseline) and `antenna_score.py 137` on the next pass —
   the rendered image + 2400 Hz subcarrier ratio + sync-lock is the real test (per
   `docs/ANTENNA.md` §Tuning). Baselines accumulate in `data/antenna_scores.jsonl`; the
   LNA's job is to beat the halo-only numbers there by ~7–10 dB.
7. **If the chain is ever torn down / the LNA removed:** set `data/lna_present.txt` back to
   absent (or not `yes`) **before** any further capture, so the interlock fails closed again
   and the tee cannot energize an LNA-less feed.

---

## 9. Bottom line

- **Software is ready NOW** — `BIASTEE-1` ships the fail-closed bias-tee interlock; the
  capture path energizes power only during active capture and de-energizes it on release.
- **The LNA is the procurement blocker** for live gain.
- **Buy the 137-band Sawbird+ first** (≤1 dB NF, ~20 dB gain, bias-tee-powered, ~$30–50):
  placed at the feedpoint it sets system NF to ~1 dB and recovers the **~8 dB** deficit —
  the path to clean weather decodes, better Orbcomm/beacon margin, and audible mobile vessels.
- **The 400 MHz LNA + antenna for RS41 is a SEPARATE, deferred buy.** One purchase does not
  unlock both bands. RS41 stays synthetic-only until that antenna lands.
