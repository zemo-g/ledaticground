# The practical Ledatic node — $300 build

The ground station's bottleneck is now hardware, not software. This is the orderable spec for the
"ultimate practical" node at a **~$300** budget, **reusing the Pi + RTL-SDR you already have**.

Parts chosen by what they unlock *for the thesis* (attestation / proof-of-reception / the RFML
corpus), not generic SDR specs. Two thesis-critical wins fit inside the budget: **GPS-PPS
(provability)** and a **filtered mast preamp (the decode floor = the real RFML moat)**, plus
**solar (autonomy)** to kill the power-bank endurance limit.

> **Prices verified 2026-06-02 (web), but treat as estimates — confirm at order.** This spec was
> corrected after a "are you certain?" audit: the original invented a "SAWbird+ AIS" SKU (doesn't
> exist), under-priced the GPS, and tried to do two bands on one SDR. See *Corrections* at the end.

## Scope it to ONE mission: the AIS / 162 node

One RTL-SDR tunes one band at a time; doing 137 *and* 162 off two antennas needs a diplexer or an
RF switch (extra cost + complexity). So **this $300 node is the AIS/162 station** — the proven,
commercial (Lakes), decode-floor-is-the-moat chain. **137 weather is its own node** (the in-flight
137MLCHD halo + a SAWbird+ NOAA), which is also the multi-station-mesh direction anyway.

## Bill of materials — AIS/162 node (~$262)

| # | Item | ~$ | What it unlocks for us |
|---|---|---|---|
| 1 | **SparkFun GNSS L1/L5 Breakout — u-blox NEO-F10N, SMA** (PPS broken out to header) → Pi UART + GPIO, 3.3 V logic, `gpsd`/`chrony` | 40 | **#1 thesis win.** Real `geo=` (lat/lon) + GPS-locked time in every attestation receipt — the field that reads `PENDING_needs_GPS_PPS` becomes real. PPS-marks capture boundaries for coarse TDOA. *(ORDERED. L1/L5 dual-band → better fix in urban/roof multipath. The F10**N** is the nav variant; its PPS is plenty for us. The F10**T** is the timing-grade sibling — save that for the fine-TDOA/GPSDO tier.)* |
| 1b | **Active GNSS antenna, SMA** — L1 (~$18) or L1/L5 (~$40) for the dual-band benefit | 18 | The F10N breakout needs an active antenna (not included); the SMA board biases it. L1 alone suffices for time+position; L1/L5 unlocks the multipath edge. |
| 2 | **Uputronics 162 MHz Filtered Preamplifier** (PSA4-5043+, 20 dB @161 MHz, 161 MHz SAW 7.6 MHz BW + FM high-pass) | 48 | Mast LNA → pushes the **AIS decode floor down** (the moat). **FILTERED is essential here:** we decode the local USCG base, so we are *not* RF-quiet — a bare wideband LNA would overload the front-end and make reception *worse*. Bias-tee or USB-C powered. |
| 3 | **162 MHz marine collinear** (AIS-tuned high-gain omni) | 45 | Dedicated AIS gain (the 137 halo is 25 MHz away and 137-tuned — it won't serve 162 well). |
| 4 | **Bias-tee injector + 5 V feed** *(or power the preamp via USB-C)* | 12 | Carries the preamp's DC up the coax so it can live at the antenna, before coax loss. |
| 5 | **LMR-240 coax run + SMA/SO-239 connectors** | 40 | Low-loss feedline + adapters. |
| 6 | **Weatherproof enclosure + self-amalgamating tape** | 25 | Roof-survivable; seal preamp + connectors. |
| 7 | **30 W solar panel + LiFePO4 cell + MPPT charge controller** | 70 | **Autonomy.** Pi Zero 2 W + SDR draws ~2–3 W. *(30 W not 20 W: Detroit winter days are short — size the panel + battery generously or the node browns out in December.)* |
| — | *(reuse)* Pi + RTL-SDR v5 | 0 | Decode-on-Pi: ship JSON, not samples. |
| | **Total** | **~298** | right at $300 with an L1 antenna; ~$320 with L1/L5 → trim 30 W→20 W solar (−$15) to stay under. |

## Architecture / wiring (single band, single SDR — clean)

```
  162 marine collinear ──┬─[enclosure @ mast]─ Uputronics 162 filtered preamp ─┐
                         │                         ▲ 5V via bias-tee up the coax │
                         │                                                       ▼
   solar 30W ─ MPPT ─ LiFePO4 ─ 5V ─► Pi ◄── RTL-SDR v5 ◄── bias-tee injector ◄─ LMR-240
                                       │
             NEO-F10N (PPS)+active GNSS ant ─► Pi UART+GPIO (3.3V; gpsd + chrony + pps-gpio: real time + lat/lon)
                                       │
                            rail_native decode + pi_characterize ──► attested JSON over WiFi
```

The Pi gets GPS time + position from the u-blox; every receipt `ais_attest` / `survey_attest` signs
now carries a **real** `geo=` and a GPS-disciplined timestamp instead of `PENDING`. The filtered
preamp sets the noise figure; the bias-tee powers it up the coax. One antenna, one band, no switch.

## Build / install order

1. **Bench-first:** wire the F10N to the Pi — 3.3 V logic so UART TX/RX go straight to the Pi UART,
   PPS → a Pi GPIO (enable the `pps-gpio` device-tree overlay), then `gpsd` + `chrony`. Confirm a 3-D
   fix + PPS lock (`ppstest`). Write the real lat/lon to `data/station_geo.txt` (the attest scripts
   read it → real receipts). The SMA active antenna needs clear sky view; the board biases it.
2. Mount the 162 collinear; preamp in the enclosure *at the antenna*; seal everything.
3. Bias-tee at the SDR end; confirm the preamp powers up (DC continuity / current draw).
4. Run `scripts/antenna_score.sh 162` + `ais_groundtruth.py` to **measure** the gain vs the recorded
   baseline (measure, don't claim) — and watch for *over*load (FPR / desense near the USCG base).
5. Solar last; confirm a full night on battery, then it's autonomous.

## Honest caveats

- **Fine TDOA is NOT in this tier.** The RTL-SDR can't take an external 10 MHz reference, so the
  u-blox gives **time discipline** (real timestamps + position + coarse PPS marking) but the *RF
  sample clock* still free-runs (~1 ppm → ~25 µs drift over a 25 s capture). Enough for **real
  geo+time receipts and coarse TDOA**, not sub-µs geometric TDOA → that's the +$135 GPSDO tier.
- **Filtered preamp, not a bare LNA** — repeated because it's the #1 way to make this *worse* in our
  RF environment. We are near strong AIS + FM; an unfiltered wideband LNA overloads.
- **Measure, don't claim** every dB through `antenna_score` / `ais_groundtruth` vs the baseline.
- **Prices are estimates** (verified 2026-06-02 but they drift + lead times bite — the NEO-M9N had a
  26-wk lead, hence the M8N).

## Upgrade tiers (beyond this node)

| Add | $ | Unlocks |
|---|---|---|
| **137 weather node** = halo (in-flight) + SAWbird+ NOAA + own Pi/SDR | ~$40 + a Pi | 137 APT/LRPT on a *separate* node — clean, and it's a 2nd mesh station. |
| **Dual-band on one SDR** = SAWbird+ NOAA + Pi-GPIO RF switch (137↔162) | +$65 | If you insist on one node for both bands. Adds switching complexity; prefer two nodes. |
| **Leo Bodnar GPSDO + clock-capable SDR** (Airspy/Lime) | +135–350 | Disciplined RF sample clock → **fine sub-µs TDOA** = the real v100 geometric proof. |
| **SDRplay RSPdx (14-bit, wideband)** | +200 | Dynamic range (dig weak ships next to strong AtoN) + a **real-time wideband RF survey**. |
| **QFH for 137** | +60 | Circular-pol match to LEO weather sats; less fading. The 137 antenna ceiling. |
| **×3 GPS-synced nodes** | ×~$262 | The actual ceiling: proof-of-reception is geometric — it needs ≥3 spatially separated, time-synced stations (the v100). |

## The one-line priority

If the budget shrinks: **the u-blox GPS-PPS (item 1) is the single highest-leverage part** — it makes
the attestation real, and provability is the whole company. The antenna fixes *sensitivity*; PPS
fixes *provability*.

## Corrections (post-audit 2026-06-02)

- ❌ "SAWbird+ AIS $35" — **does not exist** (NooElec SAWbird = NOAA/GOES/ADS-B only). → ✅ Uputronics
  162 MHz Filtered Preamp.
- ⚠️ A bare wideband mast LNA can **overload** the RX near strong AIS/FM (our case) → must be filtered.
- ❌ GPS "$25 NEO-M9N" — usable M9N breakout is $40–70 + 26-wk lead → ✅ NEO-M8N (~$22, has PPS).
- ❌ Two bands merged onto one SDR/coax — needs a diplexer/switch → ✅ scope to one band per node.
- 🛒 **2026-06-02: GPS part ordered = SparkFun NEO-F10N L1/L5 breakout ($40, PPS verified on the
  header).** Upgrade from the budget M8N — better urban/roof fix. Needs an active SMA GNSS antenna
  (+$18 L1 / +$40 L1/L5). Note it's the F10**N** (nav); the F10**T** is the timing-grade sibling for
  the future fine-TDOA tier.
