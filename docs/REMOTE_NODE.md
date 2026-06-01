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

## Node identity (deployed 2026-06-01)
- Pi Zero 2 W, hostname **ledaticground-roof**, Tailscale **ledaticground-node** (reach from
  the Mini anywhere over the tailnet; survives the WiFi change at the roof).
- User `ledatic`, passwordless sudo, Mini SSH key authorized, SDR = Nooelec NESDR
  SMArt v5 SN REDACTED (R820T). Home WiFi `HOME_WIFI`, roof WiFi `SITE_WIFI`
  (both autoconnect, home priority 10 > roof 9).

## Gotchas earned during bring-up (read before doing another Pi)
1. **Bookworm ignores boot-partition `wpa_supplicant.conf`** — the Pi never gets WiFi.
   Fix = `provision/firstrun.sh` (kernel `systemd.run` hook converts it to native
   NetworkManager `.nmconnection` files). `ssh`+`userconf.txt` ARE honored (consumed on
   first boot); only the WiFi import is dead.
2. **SSIDs are case-sensitive** — `HOME_WIFI` != `HOME_WIFI`. Verify against
   a device that's actually joined the network.
3. **SDR drops off USB after a warm reboot** — `lsusb` empty though it worked pre-reboot.
   It's power/contact, not driver. Full power-cycle + re-seat (powered hub ideal). Pi
   Zero 2 W + RTL-SDR is power-marginal.
4. **DVB driver claims the dongle** — blacklist `dvb_usb_rtl28xxu`, takes effect on reboot.
5. **Guest WiFi captive-portal risk** — a headless Pi can't click "Accept". Test the roof
   network with a phone first; if a portal pops, the node can't use it.

## Status (2026-06-01 ~03:46 UTC)
- [x] Mini-side decode pipeline (validated)
- [x] Pi provisioned headless + on Tailscale (ledaticground-node) + SDR confirmed
- [x] full Pi->Mini capture+pull pipeline proven over Tailscale
- [x] **deployed on the building roof**, on `SITE_WIFI` (SSID is SalsO not SalsA),
      internet OK, no captive portal, 2.4 GHz, signal ~40%
- [x] orchestrator LIVE as LaunchAgent `com.ledatic.ledaticground`, auto-captures next pass
- [x] weak-WiFi fix: Pi windows the clip (~2.6 MB) before shipping (24 MB full-pull stalled)
- [ ] **137 V-dipole built + swapped for the whip** (in progress — see `ANTENNA.md`).
      THE BLOCKER: first two real passes were noise on the whip (137 MHz too weak for it).
- [ ] first real image (next pass armed: **NOAA 19, 09:04 UTC / 05:04 EDT, El 60**)
- [ ] AIS live decode (roof hears 162 MHz at 18x; chain built, needs Gardner clock recovery)
- [ ] two-node simultaneous capture -> live TDOA / cross-attestation
