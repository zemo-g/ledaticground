# SAME Decoder — attested weather/marine alerts from NOAA Weather Radio

**Status:** SPEC (2026-06-01). **Reception CONFIRMED:** the roof node hears NWR on **162.550 MHz**
— roughness 1768 (smooth carrier) vs 11009 on an off-channel (noise), a ~6× margin = a strong,
continuous broadcast. This is a clean fit for the *current* antenna (it's 0.5 MHz from the AIS band
we already decode at ~18× the floor).

> Why it matters: SAME carries **Special Marine Warnings (SMW)**, **Marine Weather Statements (MWS)**,
> and Great-Lakes marine-zone gale/storm warnings. Decoded + attested, these become the **weather-alert
> layer of the port-call product** — warnings on the same corridor the vessel feed covers, provably
> received here, at this time.

## What SAME is
**Specific Area Message Encoding** — the digital burst NWR sends *before* an alert's voice message
(and an End-Of-Message burst after). The voice carrier is continuous; **SAME bursts are event-driven**
(they fire only when an alert is issued). Each header is sent **3×** for redundancy (SAME has no FEC —
the 3 copies *are* the error correction, via 2-of-3 byte voting).

## The signal (what we decode)
- **Modulation:** AFSK, NRZ, **520.83 bits/s**, mark (1) = **2083.3 Hz**, space (0) = **1562.5 Hz**.
- **Burst structure:**
  - Preamble: 16 bytes of `0xAB` (`10101011`) — clock/byte sync.
  - Header: `ZCZC-ORG-EEE-PSSCCC-PSSCCC…+TTTT-JJJHHMM-LLLLLLLL-`
    - `ORG` originator (WXR = NWS, EAS, CIV, PEP)
    - `EEE` event (TOR tornado, SVR severe t-storm, FFW flash flood, **SMW special marine warning,
      MWS marine weather statement**, SVS, …)
    - `PSSCCC` location(s): part-of-county + state + county FIPS (up to 31, dash-separated)
    - `+TTTT` purge/valid duration · `JJJHHMM` issue time (Julian day + UTC) · `LLLLLLLL` station ID
  - (warnings) 1050 Hz attention tone 8–25 s, then the voice message.
  - EOM: preamble + `NNNN`, 3×.

## Decode pipeline — reuses the stack we've already built
Same shape as AIS/LRPT (demod → bit-sync → frame → validate → parse → attest):
1. **FM-demod audio** — already have it (`rtl_fm` ch @ 162.550, s16 @ 48 kHz). Continuous capture
   (unlike AIS bursts) — or squelch/energy-gate to only process when a SAME burst is present.
2. **AFSK tone detection** — Goertzel (or matched correlation) at 1562.5 / 2083.5 Hz per bit window;
   mark>space → 1. (Tones are well inside 48 kHz audio.) Pure-Python on the Pi like `pi_ais_decode`.
3. **Bit/byte sync** — lock on the `0xAB` preamble run (520.83 baud → ~92 samples/bit @48k; easy,
   no Gardner needed at this rate). NRZ, LSB-first bytes.
4. **Frame extract** — from `ZCZC` to the trailing `-`; collect the 3 repeats.
5. **2-of-3 voting** — per byte position across the 3 copies → the corrected header (the SAME FEC).
6. **Parse** — split the dash fields → {originator, event, FIPS area list, valid-until, issued, station}.
7. **Filter to relevant** — surface **marine codes (SMW/MWS) + Great-Lakes marine-zone FIPS** for the
   port product; log all.

## Validation (falsify-each-rung, like every other decoder here)
SAME bursts are event-driven, so **live validation waits for a real alert.** Meanwhile:
- **Synthetic burst:** generate the AFSK from a known `ZCZC…` header (a `gen_same.py`), decode it,
  assert exact field recovery + the 2-of-3 voting corrects injected bit errors. This is the gate.
- **Live confirm:** when a real alert fires (or NWR's weekly Required Weekly Test `RWT`), decode it
  off-air and check the fields against the broadcast voice. NWR sends an `RWT` weekly — a free live test.

## Attestation + product tie-in
- **Attested alert receipt** (`ais_attest.rail` pattern): *this node received this SAME alert
  (event, area, time) at this time* — Ed25519-signed, hash-chained. Proof-of-reception for warnings.
- **Port-call layer:** marine warnings (SMW/MWS/gale/storm) on the corridor, overlaid on the vessel
  feed → the weather-alert strip the dispatcher brief wants. *Attested* weather warnings, not a scrape.

## Honest gates
- **Event-driven:** no real decode until an alert (or the weekly RWT) is broadcast — validate synthetic
  first; the live decode proves out on the next RWT/alert.
- **Current-antenna OK** (reception confirmed) — no hardware needed, unlike ADS-B/137.
- **Receive-only, open broadcast** — NWR/SAME is a public emergency broadcast; clean to receive + attest
  (unlike pagers/encrypted public-safety, which are off-limits).

**First build step:** `gen_same.py` (synthetic ZCZC burst) → a pure-Python `same_decode.py` on the Pi
(Goertzel + preamble sync + 2-of-3 vote + field parse), validated on the synthetic, then armed to catch
the next RWT off 162.550. Mirrors how AIS went from synthetic-validated to real off-air.
