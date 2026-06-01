# ledaticground remote node — remote site (Pi Zero 2 W)

Goal: a second receiving station at a high, low-noise site. The Pi Zero 2 W
**captures**; the Mini **decodes** (our validated pipeline lives there). Once this
node + a local node both run, the same pass recorded at two sites gives a **live
TDOA + cross-attested bundle** — the v100 multi-station proof on real data.

## Antenna — build a 137 MHz V-dipole (the standard NOAA antenna)

His existing ~10 m "power-line-looking" antenna is almost certainly **HF** (3–30 MHz):
wrong resonance for 137 MHz and a horizon-facing pattern. **Don't rely on it.** The
mast + clear-sky location is the asset; mount a purpose-built 137 antenna up there.

**V-dipole for 137.5 MHz** (covers NOAA 137.1 / 137.62 + METEOR 137.1/137.9):
- Two legs, **each 53.4 cm** of wire (¼λ minus end-effect; λ/4 = 54.5 cm at 137.5 MHz).
- Legs at a **120° V**, held **horizontal**, legs oriented **N–S** (broadside faces E–W
  to catch the N–S/S–N passes).
- Fed at the apex with **50 Ω coax** (RG58/RG174) straight to the SDR. Add **5–7 turns
  of the coax into a ~5 cm coil** right at the feedpoint = common-mode choke (cuts noise).
- Mount **clear of metal**, with open sky view. Height helps but even 2–3 m in a clear
  rural yard beats an urban whip. Do **not** climb the 10 m mast — reachable + clear is fine.
- Better (optional, later): a **QFH** (quadrifilar helix) matches the satellites' circular
  polarization and fades less, but it's fiddlier to build. V-dipole first.

## Deployment site: rooftop ~1 mile away (preferred)

A roof beats the friend's HF antenna — unobstructed sky view is what 137 MHz wants.
1 mile is negligible for pass prediction (reuse the regional schedule from
`passes.rail`). Rooftop practicalities:
- **Power:** Pi Zero 2 W + SDR need ~5 V / 1.5 A. Roof outlet, or a long USB run from
  inside, or a battery/solar pack for passes only.
- **Weatherproofing:** it's outdoors — Pi + SDR in a sealed IP65 box, antenna outside
  it, a drip loop on the coax. Vent to avoid condensation.
- **WiFi + Tailscale:** confirm the building WiFi reaches the roof; Tailscale gives the
  Mini a stable path regardless of the local network.
- **Baseline note:** a 1-mile site is for IMAGES, not TDOA. For the live two-station
  TDOA/bundle, the *second* site (e.g. the friend's) must be ~10+ km away.

## Pi Zero 2 W setup (capture node)

```bash
# Raspberry Pi OS Lite. Plug RTL-SDR via micro-USB OTG adapter (+ power).
sudo apt update && sudo apt install -y rtl-sdr
echo 'blacklist dvb_usb_rtl28xxu' | sudo tee /etc/modprobe.d/rtlsdr-blacklist.conf
rtl_test -t          # confirm the dongle enumerates (Nooelec/Realtek)
# Join our tailnet so the Mini can pull recordings:
curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up
tailscale ip -4      # note this node's Tailscale IP
```

The Pi **records only** — real-time decode would choke a Zero 2 W. APT recording
(~11 kHz audio) is light; it writes ~25 MB/pass to the SD card and ships it to the Mini.

## Flow

```
backyard:  [137 V-dipole] -> RTL-SDR -> Pi Zero 2 W  (scripts/pi_capture.sh)
                                  | records the pass, scp to Mini over Tailscale
Mini:  scripts/recv_decode.sh -> decode_real_pass.sh -> image + attested receipt
                                  (station label = this node)
```

Pass times: compute on the Mini with `src/passes.rail` (the friend's lat/lon), tell the
Pi when to record (cron or a one-shot `at`). NOAA 15/19 + METEOR schedule is the same
sky; only the observer coords change.

## Status
- [x] Mini-side decode pipeline (validated)
- [x] `scripts/pi_capture.sh` (record + ship)
- [ ] friend's lat/lon for pass prediction
- [ ] Pi on Tailscale + SDR confirmed
- [ ] V-dipole built + mounted
- [ ] first real pass captured at the remote site
- [ ] two-node simultaneous capture -> live TDOA / cross-attestation
