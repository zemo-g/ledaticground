# Antenna — 137 MHz V-dipole for the ledaticground node

## Why (the empirical diagnosis, 2026-06-01)
Two real overhead passes (NOAA 15 81°, NOAA 19 82°) came down as **pure noise** — the
2400 Hz APT subcarrier sat at only **1.0–1.2× the noise floor** (a real signal is 5–20×).
Cause: the SDR was on the **stock telescopic whip**. Proof it's the antenna, not the
location or pipeline:
- The roof hears the **162 MHz marine/AIS band at ~18× the noise floor** (strong local
  ship transmitters, a few km away on the Detroit River).
- But the **137 MHz weather satellite** (NOAA, ~5 W, 800+ km up) is far weaker and needs a
  *tuned, sky-facing* antenna. A whip catches strong-near signals but not weak-far ones.
The decode pipeline is fully validated; the antenna is the only gap.

## The fix — build a 137.5 MHz V-dipole
λ at 137.5 MHz = 218 cm; ¼λ = 54.5 cm; minus end-effect → **53.4 cm per leg**.

**Best / easiest:** the RTL-SDR.com (or Nooelec) dipole kit (~$20) — telescopic elements
set to 53.4 cm, hinge to 120°, includes coax with SMA + a choke + a mount.

**DIY (basically free):**
1. Two stiff wires, **each exactly 53.4 cm** (copper, brazing rod, or coat-hanger).
2. **Reuse the whip's coax + SMA plug** (the NESDR SMArt v5 is SMA): cut the whip off,
   keep the coax. **Center conductor → one leg, braid/shield → the other.** Don't short them.
3. Splay into a **120° V, held horizontal** (wide V, not straight, not vertical).
4. **Legs point N–S** (V opens E–W) — aims the pattern at the N↔S satellite passes.
5. **Common-mode choke: coil the coax ~6 turns, ~5 cm across, right at the feedpoint.**
   Cheapest single improvement to the noise floor.
6. Mount **horizontal, high, clear sky, away from metal.**

```
      53.4 cm          53.4 cm
  wire \                / wire      ← horizontal, ~120° between legs
        \_____120°_____/            ← legs point N & S
              |
            coax (6-turn choke at feed) → SDR
```

## Upgrade path (optional, later)
**QFH (quadrifilar helix)** — matches the satellites' RHCP, less fading, best performance,
but fiddly to build. Do the V-dipole first.

## After it's built
Swap it for the whip at the SDR's SMA. No software changes — the orchestrator
(`com.ledatic.ledaticground` on the Mini) auto-captures the next pass and decodes it.
First real-image target after the dipole: **NOAA 19, 09:04 UTC / 05:04 EDT, El 60°.**

## Bands of interest (this SDR: 24 MHz–1.7 GHz, 2.4 GHz hardware not relevant)
- **137.100 MHz** NOAA 19 APT · **137.620** NOAA 15 APT · **137.9125** NOAA 18 (decommissioned)
- **137.100 / 137.900** METEOR-M LRPT (digital QPSK — see the LRPT decoder)
- **161.975 / 162.025** AIS (ship tracking — strong at this roof; see the AIS decoder).
  The 137 V-dipole is ~18% off-tune at 162 but the local AIS signal is strong enough; a
  dedicated 162 dipole (legs ~45 cm) would be better for a permanent AIS feed.

## Tuning the current adjustable dipole kit (interim, until the new antenna lands)

The node currently runs the **silver telescopic dipole kit** (adjustable elements). It's
tunable, so set it per band — λ/4 minus ~2% end-effect:

| Band | Use | Element length (each leg) | Geometry / polarization |
|------|-----|---------------------------|--------------------------|
| **137.5 MHz** | NOAA/METEOR weather | **~53.4 cm** | 120° **V, horizontal**, legs N–S (approximates the sat's circular pol; broadside faces the N↔S pass) |
| **162.0 MHz** | AIS ship tracking | **~44 cm** | **Vertical** dipole (marine signals are vertically polarized) |

These two optima **conflict** (horizontal-V vs vertical, different lengths), so "both
equally" means one of:
- **Two presets, swapped by hand** — best per-band result, but each swap is a roof trip.
- **One compromise** — ~48 cm legs in a shallow V. Decodes both, optimal for neither.

**Don't guess — measure.** That's what `scripts/antenna_score.sh 162` is for: set the
elements, run it, read the SNR + decode count, adjust, re-run. Baselines accumulate in
`data/antenna_scores.jsonl`, so the current-kit numbers are the yardstick the new antenna
has to beat. Since AIS already works and 137 weather imaging waits on the **new** antenna
anyway, a sane interim is: tune the kit for **162 vertical** now (lock in the Lakes vessel
feed), and let the incoming antenna own 137.
