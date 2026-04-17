# ground-station

Personal satellite ground station — receive-only.

Pulls free public data: TLEs from CelesTrak + SatNOGS DB, transmitter metadata
from the SatNOGS API, and raw RF directly from the sky via an SDR.

## Status

Empty scaffold. Direction pending.

## Hardware (planned)

- SDR: RTL-SDR v4 (baseline), upgrade path to Airspy/HackRF
- Antenna: TBD (V-dipole for NOAA/METEOR 137 MHz, turnstile for ISS/amateur VHF/UHF)
- Host: Pi 5 or Mac Mini

## Directory layout

- `backend/` — server + signal pipeline
- `frontend/` — UI
- `hardware/` — antenna build notes, BOM, wiring
- `docs/` — design, reference material, learning notes
