# The practical Ledatic node — $300 build

The ground station's bottleneck is now hardware, not software. This is the orderable spec for the
"ultimate practical" node at a **~$300** budget, **reusing the Pi + RTL-SDR you already have**.

Every part is chosen by what it unlocks *for the thesis* (attestation / proof-of-reception / the
RFML corpus) — not for generic SDR performance. The two thesis-critical wins fit inside the budget:
**GPS-PPS (provability)** and **mast LNAs (the decode floor = the real RFML moat)**, plus **solar
(autonomy)** to kill the power-bank endurance limit.

## Bill of materials (~$287)

| # | Item | ~$ | What it unlocks for us |
|---|---|---|---|
| 1 | **u-blox NEO-M9N GPS module (PPS out)** → Pi GPIO + `gpsd`/`chrony` | 25 | **#1 thesis win.** Real `geo=` (lat/lon) + GPS-locked time in every attestation receipt — the field that currently reads `PENDING_needs_GPS_PPS` becomes real. PPS-marks capture boundaries for TDOA. |
| 2 | **NooElec SAWbird+ NOAA** (137 MHz LNA + SAW, ~0.8 dB NF) | 35 | Mast LNA → lowers the 137 system noise figure → finally hear weak weather sats. |
| 3 | **NooElec SAWbird+ AIS** (162 MHz LNA + SAW) | 35 | Mast LNA → pushes the **AIS decode floor down** = recover weaker ships. The coverage-gap experiment showed this floor *is* the moat. |
| 4 | **Inline bias-tee + 5 V feed** (power the mast LNAs over coax) | 12 | LNAs must live at the antenna, before coax loss; bias-tee carries their power up the feedline. |
| 5 | **162 MHz marine collinear** (AIS-tuned, high-gain omni) | 40 | Dedicated AIS gain. *Optional* if the in-flight 137 halo covers 162 adequately (0.5 MHz away) — if so, redirect this $40 toward item 8 or the GPSDO tier. |
| 6 | **LMR-240 coax run + SMA/SO-239 connectors/adapters** | 45 | Low-loss feedline; the SO-239→SMA adapter the halo needs (see ANTENNA.md). |
| 7 | **Weatherproof enclosure + self-amalgamating tape** | 25 | Roof-survivable; seal the LNA + connectors. |
| 8 | **20 W solar panel + LiFePO4 cell + charge controller** | 70 | **Autonomy.** Kills the power-bank limit (currently ~1.6 %/h → ~2.5 days). Pi Zero 2 W + SDR draws ~2–3 W; 20 W of sun sustains it indefinitely. |
| — | *(reuse)* Pi Zero 2 W / Pi + RTL-SDR v5 | 0 | Already deployed. Decode-on-Pi: ship JSON, not samples. |
| — | *(in flight)* 137MLCHD halo + custom pole | — | Already ordered — the 137 antenna. This BOM assumes it. |
| | **Total** | **~287** | under $300 |

## Architecture / wiring

```
  137 halo (in-flight) ─┐                         ┌─ SAWbird+ NOAA (mast LNA+filter) ─┐
                        ├─ [enclosure @ mast] ─────┤                                    ├─ LMR-240 ─┐
  162 collinear ────────┘                         └─ SAWbird+ AIS  (mast LNA+filter) ─┘            │
                                                          ▲ 5V via bias-tee up the coax            │
                                                                                                   ▼
   solar 20W ─ charge ctrl ─ LiFePO4 ─ 5V ─► Pi ◄── RTL-SDR v5 ◄── bias-tee injector ◄─────────────┘
                                            │
                                u-blox GPS (PPS) ─► Pi GPIO  (gpsd + chrony: real time + lat/lon)
                                            │
                                  rail_native decode + pi_characterize  ──► attested JSON over WiFi
```

The Pi gets GPS time + position from the u-blox (item 1); every receipt `ais_attest` /
`survey_attest` signs now carries a **real** `geo=` and a GPS-disciplined timestamp instead of
`PENDING`. The mast LNAs (2,3) set the noise figure; the bias-tee (4) powers them up the coax.

## Build / install order

1. **Bench-first:** wire the u-blox to the Pi (UART + PPS→GPIO), `gpsd` + `chrony`, confirm a 3-D
   fix + PPS lock. Write the real lat/lon to `data/station_geo.txt` (the attest scripts read it).
2. Mount antennas on the pole, LNAs in the enclosure *at the antenna*, seal everything.
3. Bias-tee at the SDR end; verify the LNAs power up (DC continuity, current draw).
4. Run `scripts/antenna_score.sh 162` + `ais_groundtruth.py` to **measure** the sensitivity gain
   vs the current baseline (don't claim it — measure it).
5. Solar last; confirm the battery holds through a night, then it's autonomous.

## Honest caveats

- **Fine TDOA is NOT in the $300 tier.** The RTL-SDR can't take an external 10 MHz reference, so the
  u-blox gives **time discipline** (real timestamps + position + coarse PPS sample-marking) but the
  *RF sample clock* still free-runs (~1 ppm → ~25 µs drift over a 25 s capture). That's enough for
  **real geo+time receipts and coarse TDOA**, not sub-µs geometric TDOA. Fine TDOA = the +$135 tier.
- **Measure, don't claim.** Every sensitivity number goes through `antenna_score` / `ais_groundtruth`
  against the recorded baseline before it's asserted.

## Upgrade tiers (beyond $300)

| Add | $ | Unlocks |
|---|---|---|
| **Leo Bodnar GPSDO + clock-capable SDR** (Airspy/Lime) | +135–350 | Disciplined RF sample clock → **fine sub-µs TDOA** = the real v100 geometric proof. |
| **SDRplay RSPdx (14-bit, wideband)** | +200 | Dynamic range (dig weak ships next to strong AtoN) + a **real-time wideband RF survey** (characterize the whole band at once, not freq-by-freq). |
| **QFH for 137** (vs the halo) | +60 | Circular-pol match to LEO weather sats; less fading. The 137 antenna *ceiling*. |
| **×3 of this node** | ×$287 | The actual ceiling: a GPS-synced **mesh** — proof-of-reception is geometric, it needs ≥3 spatially separated, time-synced stations (the v100). |

## The one-line priority

If the budget shrinks: **the u-blox GPS-PPS (item 1) is the single highest-leverage part** — it makes
the attestation real, and provability is the whole company. The antenna fixes *sensitivity*; PPS
fixes *provability*.
