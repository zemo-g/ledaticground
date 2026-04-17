# ledaticground

Personal satellite ground station, written in Rail.

Receive-only. Pulls free public data: TLEs from CelesTrak and SatNOGS DB,
transmitter metadata from the SatNOGS API, and raw RF from the sky via an SDR.

## Why Rail

Rail already has the ingredients: pure-Rail HTTP client (`stdlib/http_client.rail`),
TCP server (`stdlib/socket.rail`), JSON, chunked-transfer decoder, Metal tensor GPU.
What's missing is the radio stack — SDR driver bridge, DSP, demodulators, decoders.
Building those in Rail is the point.

## Scope

- **v0.1** — pull TLEs, compute passes for a given station, serve a web page.
- **v0.2** — RTL-SDR IQ capture via a thin C shim, spectrum + waterfall in browser.
- **v0.3** — FM demod, live audio to the browser.
- **v0.4** — SSTV / AFSK / GMSK decoders.
- **v0.5** — SigMF record + playback.

## Hardware

- SDR: RTL-SDR v4 (baseline)
- Antenna: V-dipole for NOAA/METEOR (137 MHz), turnstile for amateur VHF/UHF
- Host: Pi 5 or Mac Mini

## Directory layout

- `src/` — Rail source
- `shim/` — minimal C bridge to `librtlsdr` (until Rail has USB)
- `web/` — static frontend (plain HTML/JS, served by Rail)
- `hardware/` — antenna build notes, BOM
- `docs/` — design notes, reference material

## License

TBD (likely BSL 1.1 to match Rail, or MIT).
