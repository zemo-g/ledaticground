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

A roof beats the the operator's HF antenna — unobstructed sky view is what 137 MHz wants.
1 mile is negligible for pass prediction (reuse the regional schedule from
`passes.rail`). Rooftop practicalities:
- **Power:** Pi Zero 2 W + SDR need ~5 V / 1.5 A. Roof outlet, or a long USB run from
  inside, or a battery/solar pack for passes only.
- **Weatherproofing:** it's outdoors — Pi + SDR in a sealed IP65 box, antenna outside
  it, a drip loop on the coax. Vent to avoid condensation.
- **WiFi + Tailscale:** confirm the building WiFi reaches the roof; Tailscale gives the
  Mini a stable path regardless of the local network.
- **Baseline note:** a 1-mile site is for IMAGES, not TDOA. For the live two-station
  TDOA/bundle, the *second* site (e.g. the the operator's) must be ~10+ km away.

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

Pass times: compute on the Mini with `src/passes.rail` (the the operator's lat/lon), tell the
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

## Status (2026-06-01, updated ~15:00 UTC)
- [x] Mini-side decode pipeline (validated)
- [x] Pi provisioned headless + on Tailscale (ledaticground-node) + SDR confirmed
- [x] **deployed on the building roof**, on `SITE_WIFI` (SSID is SalsO not SalsA),
      internet OK, no captive portal, 2.4 GHz, signal ~40% (link throughput **only ~13 KB/s**)
- [x] **FLIPPED to continuous AIS monitoring** — LaunchAgent `com.ledatic.ledaticground` runs
      `scripts/ais_monitor.sh`: every 5 min the Pi captures 25 s, **decodes on-board**
      (`pi_ais_decode.py`, pure-Python, exhaustive), ships only JSON. Alternates ch-A/ch-B.
      Decode-on-Pi is the weak-WiFi fix (a 2.4 MB pull at 13 KB/s never kept up).
- [x] battery + thermal endurance logged per cycle (`vcgencmd get_throttled` + `measure_temp`)
- [x] **REAL off-air AIS decoded + attested** — USCG base station + ~21 aids-to-navigation
      across the Detroit River / L.St.Clair / W.L.Erie corridor. Catalog in `data/vessel_log.jsonl`.
- [ ] **moving vessels** — the gap; see the sensitivity finding below. Needs the tuned antenna.
- [ ] **137 weather imaging** — still antenna-blocked (whip/kit can't hear the satellites).
      Incoming: the **137MLCHD horizontal halo** (see `ANTENNA.md`).
- [ ] two-node simultaneous capture -> live TDOA / cross-attestation

## Finding (2026-06-01): the vessel gap is RF SENSITIVITY, not decode
Compared our feed to live **aisstream.io** ground truth (`scripts/ais_groundtruth.py`): we match
the fixed AtoN + base station and even uniquely catch a few, but decode **zero of the ~16 moving
vessels** aisstream shows in the river (freighters `H LEE WHITE`, `AMERICAN INTEGRITY`, `ALPENA`,
saltie `FEDERAL WELLAND`, riverboat `DETROIT PRINCESS`, USCG, pleasure craft).
- Tested **both channels**, exhaustive decode → AtoN + base only on each. Not a decoder bug.
- We're not 92-km-capable; we hear the **strong fixed infrastructure** (the base station + the
  AtoN it broadcasts district-wide). Mobile vessels (low antennas, weaker) sit below our front end.
- The decode side is now **exhaustive** (`ais_decode.rail` + `pi_ais_decode.py` collect every
  distinct frame, deduped) — it'll extract vessels the instant they're audible. The unlock is the antenna.

## Antenna-day playbook (when the 137MLCHD halo is mounted)
1. **Connector:** SO-239 (antenna) ↔ SMA (SDR) — have a PL-259→SMA coax/adapter ready (see `ANTENNA.md`).
2. **Mount** horizontal, ≥12 ft, clear of metal; **6-turn coax choke** at the feed.
3. **Score 162 (AIS):** `bash scripts/antenna_score.sh 162` → compare SNR/decode to the dipole baseline.
4. **Ground-truth:** `python3 scripts/ais_groundtruth.py --secs 150` → **measure how many vessels we now
   recover** vs aisstream. This is THE metric that says the feed became a vessel-traffic feed.
5. **137 weather:** peak the gamma match ~137.4 by rendering a pass + the 2400 Hz ratio
   (`antenna_score.py 137`); swap the plist back to `orchestrate.sh` for NOAA pass capture if desired.
