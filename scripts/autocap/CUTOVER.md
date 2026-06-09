# CUTOVER — Mini-driven capture  →  decoupled (Pi-driven) capture

**ledaticground roof node.** This is the deliberate switch from the *old*
Mini-driven capture (the Mini sshes the Pi at AOS — dies if WiFi is down) to
the *new* decoupled capture (the Pi captures on its own clock from a pushed
schedule; the Mini only pushes the schedule and pulls the results later).

> **THE ONE HARD RULE — never run both systems at once.**
> There is a single SDR (RTL dongle) on the Pi. The old agent
> (`com.ledatic.ledaticground-iq`, on the Mini) and the new Pi capture
> (`ledaticground-iqcap.service`) both grab that radio at pass time. If both are active they
> will fight for the dongle: one gets `usb_claim_interface error -6`, captures
> corrupt, and `roofmon`/AIS may be left stopped. **Exactly one capture owner
> at a time.** The cutover below stops the old owner *before* starting the new
> one; the rollback does the reverse. Never overlap them.

`roofmon.service` (AIS / vessels) is the always-on MAIN system and is **not**
part of this cutover. Both capture systems preempt it only for a pass window
and always restart it. The Pi-side deadman (`/etc/cron.d/roofmon-deadman`) is
the independent backstop and already tolerates a running `rtl_sdr`.

---

## 0. What each piece is

| Side | Old (Mini-driven) | New (decoupled) |
|------|-------------------|-----------------|
| Capture owner | **Mini** ssh-at-AOS → `rtl_sdr` on Pi | **Pi** `ledaticground-iqcap.service` runs `pi_iq_capture.sh` |
| Orbit calc | Mini `next_pass.py` (per pass, live) | Mini `next_pass.py` → **`schedule.tsv` pushed to Pi** |
| If WiFi down at AOS | **pass LOST** | **pass still captured** (Pi has the schedule) |
| Mini agents | `com.ledatic.ledaticground-iq` | `…-iqsched` (push schedule) + `…-iqpull` (pull+decode) |
| Pull/decode | inline in the same agent | `…-iqpull` on the Mini, idempotent, off-radio |
| Capture store | pulled immediately, deleted on Pi | **kept on Pi** `~/.iq/captures/` until pulled; retention prunes |

**Interface contract (do not change without changing both ends):**
- Schedule file on the Pi: `~/.iq/schedule.tsv`, TAB-separated, one pass/line,
  sorted by AOS ascending, fields **exactly**:
  `AOS_EPOCH <TAB> DUR_MIN <TAB> ELEV <TAB> FREQ_HZ <TAB> MODE <TAB> SAT`
  (AOS_EPOCH = unix seconds UTC; SAT may contain spaces). Written atomically.
- Capture filename (so `iq_apt_decode.py` + `validate_external.sh` keep working):
  `iq_<SATNOSPACES>_el<EL>_<MODE>_<YYYYMMDDTHHMMZ>.bin` (uppercase T and Z)
  e.g. `iq_NOAA19_el81_APT_20260609T0303Z.bin` (SATNOSPACES = name w/ spaces removed).
- Capture store on Pi: `~/.iq/captures/<name>.bin` + manifest `~/.iq/manifest.tsv`
  (`AOS_EPOCH <TAB> name <TAB> bytes <TAB> captured_epoch` — AOS-first, so a
  power-cycle skips already-captured passes by AOS; the retention janitor drops by `$2`=name).
- Pull target on Mini: `~/.ledatic/roofv2/raw_iq/` (same dir the old path used).

---

## 1. Install (idempotent, NON-destructive — does not cut over)

From the Mini, with the Pi online:

```bash
cd /Users/ledaticempire/projects/ledaticground/scripts/autocap
./install_autocap.sh            # installs files; enables (no-start) iqcap; prints cutover
# or preview everything first:
./install_autocap.sh --dry-run
```

This copies the Pi capture script + unit + retention cron to the Pi (and
`systemctl enable iqcap` so it survives the nightly power-cycle), and drops the
two Mini plists into `~/Library/LaunchAgents`. It does **not** start `iqcap`,
does **not** bootstrap the Mini agents, and **never** stops the old agent. The
old Mini-driven system keeps running, untouched, the whole time.

> If the Pi is offline when you install, the Pi half is skipped with a warning;
> re-run the installer when the Pi is back to finish it. The Mini half still
> completes.

---

## 2. Cut over (the deliberate switch)

Pick **one** of the two paths. Both enforce *stop-old-before-start-new*.

### Path A — assisted (`--go`)
```bash
cd /Users/ledaticempire/projects/ledaticground/scripts/autocap
./install_autocap.sh --go
```
`--go` performs the **Mini** cutover for you:
1. `launchctl bootout` the old `com.ledatic.ledaticground-iq` (stops Mini-driven capture).
2. `bootstrap` the two new agents (`…-iqsched`, `…-iqpull`).
3. `kickstart` `…-iqsched` once so a fresh `schedule.tsv` reaches the Pi immediately.

It then **prints — but does not run —** the final Pi step, on purpose (starting
the radio is the contention moment; you want eyes on it):
```bash
ssh ledatic@100.115.30.12 'sudo systemctl start ledaticground-iqcap.service && systemctl status ledaticground-iqcap.service --no-pager'
ssh ledatic@100.115.30.12 'journalctl -u ledaticground-iqcap.service -f'     # watch a window land
```

### Path B — fully manual
```bash
# 1. STOP the old Mini-driven capture FIRST (single-SDR safety).
launchctl bootout gui/$(id -u)/com.ledatic.ledaticground-iq

# 2-3. Bring up the new Mini agents.
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.ledatic.ledaticground-iqsched.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.ledatic.ledaticground-iqpull.plist

# 4. Push the first schedule to the Pi now (don't wait for the first tick).
launchctl kickstart -k gui/$(id -u)/com.ledatic.ledaticground-iqsched

# 5. LAST: start the Pi capture loop (now that the old owner is stopped).
ssh ledatic@100.115.30.12 'sudo systemctl start ledaticground-iqcap.service'
```

**Optional — stop the old agent loading on the next Mini login.** `bootout`
only unloads it for the current session; it RunAtLoad's again after a reboot.
To retire it across reboots (rollback-friendly: rename, don't delete):
```bash
mv ~/Library/LaunchAgents/com.ledatic.ledaticground-iq.plist \
   ~/Library/LaunchAgents/_disabled.com.ledatic.ledaticground-iq.plist
```
(Matches the repo convention — see `_disabled.com.ledatic.ledaticground-pass.plist`.)

---

## 3. Verify the new system

**Mini — schedule was computed and pushed:**
```bash
tail -f /tmp/iqsched_v2.log                       # iqsched agent log
ssh ledatic@100.115.30.12 'cat ~/.iq/schedule.tsv'    # the pushed schedule (AOS_EPOCH..SAT)
ssh ledatic@100.115.30.12 'wc -l < ~/.iq/schedule.tsv; \
  awk -F"\t" "{print strftime(\"%FT%TZ\",\$1), \$3\"deg\", \$5, \$6}" ~/.iq/schedule.tsv | head'
```
Expect ≥1 line, sorted by AOS, covering the next ≥24h (maxel ≥ 40°).

**Pi — capture loop is healthy and time-sharing correctly:**
```bash
ssh ledatic@100.115.30.12 'systemctl is-active ledaticground-iqcap.service'      # -> active
ssh ledatic@100.115.30.12 'journalctl -u ledaticground-iqcap.service -n 40 --no-pager'
# Between passes, roofmon MUST be the radio owner:
ssh ledatic@100.115.30.12 'systemctl is-active roofmon.service'    # -> active
ssh ledatic@100.115.30.12 'pgrep -a rtl_sdr; pgrep -a rtl_fm'      # idle: only rtl_fm (AIS), no rtl_sdr
```

**A real pass landed and was pulled + decoded (the end-to-end proof):**
```bash
# on the Pi, after the first scheduled AOS:
ssh ledatic@100.115.30.12 'ls -l ~/.iq/captures/ && cat ~/.iq/manifest.tsv'
# on the Mini, the puller fetched it and ran iq_apt_decode.py once:
ls -lt ~/.ledatic/roofv2/raw_iq/ | head
tail -f /tmp/iqpull.log
# you should see iq_<sat>_…_<MODE>_<ts>.bin plus its _waterfall.png / _image.png
```

**Time-share sanity (the SACRED invariant):** during a pass window `iqcap`
stops `roofmon`, runs `timeout -k 10 <secs> rtl_sdr …`, then restarts `roofmon`
with ≥5 retries. After every pass, confirm `roofmon` is `active` again. If it
isn't, the Pi-side `roofmon-deadman` cron force-starts it within a minute.

---

## 4. ROLLBACK — back to the Mini-driven system

Use this if the decoupled path misbehaves. Again: **stop the new owner first,
then start the old one** — never both.

```bash
# 1. STOP + DISABLE the Pi capture (give the radio back to AIS-only + Mini-driven).
ssh ledatic@100.115.30.12 'sudo systemctl stop ledaticground-iqcap.service && sudo systemctl disable ledaticground-iqcap.service'
ssh ledatic@100.115.30.12 'sudo systemctl start roofmon.service'   # ensure AIS is back

# 2. Remove the new Mini agents (helper does bootout + plist removal):
cd /Users/ledaticempire/projects/ledaticground/scripts/autocap
./install_autocap.sh --uninstall-mini
#   (equivalently, by hand:)
#   launchctl bootout gui/$(id -u)/com.ledatic.ledaticground-iqsched
#   launchctl bootout gui/$(id -u)/com.ledatic.ledaticground-iqpull
#   rm ~/Library/LaunchAgents/com.ledatic.ledaticground-iq{sched,pull}.plist

# 3. RE-ENABLE the old Mini-driven agent (LAST — now that the Pi owner is stopped).
#    If you renamed it to _disabled.* in step 2.optional above, rename it back first:
[ -f ~/Library/LaunchAgents/_disabled.com.ledatic.ledaticground-iq.plist ] && \
  mv ~/Library/LaunchAgents/_disabled.com.ledatic.ledaticground-iq.plist \
     ~/Library/LaunchAgents/com.ledatic.ledaticground-iq.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.ledatic.ledaticground-iq.plist
```

After rollback:
- `ledaticground-iqcap.service` is `inactive` + `disabled` (won't auto-start on the next Pi
  power-cycle). Its files remain installed for a future re-cutover; re-enable
  with `./install_autocap.sh` (no `--go`) → step 2 again.
- The captures already sitting in `~/.iq/captures/` on the Pi are still there;
  retention prunes them on the normal 7-day / <5G schedule. Leaving the
  retention cron installed during rollback is harmless (it only deletes old
  `.bin` files; it never touches the radio).
- The old `com.ledatic.ledaticground-iq` is the sole capture owner again.

---

## 5. Files this cutover touches

| Path | Side | Owner | Purpose |
|------|------|-------|---------|
| `/usr/local/bin/pi_iq_capture.sh` | Pi | root 0755 | reads `schedule.tsv`, captures at AOS, time-shares AIS |
| `/etc/systemd/system/ledaticground-iqcap.service` | Pi | root 0644 | runs the capture loop; `enable` = survives power-cycle |
| `/etc/cron.d/iqcap-retention` | Pi | root 0644 | prune captures >7d or when /home <5G (this component) |
| `~/.iq/schedule.tsv` | Pi | ledatic | pushed schedule (Mini writes, Pi consumes) |
| `~/.iq/captures/*.bin` + `~/.iq/manifest.tsv` | Pi | ledatic | capture store, kept until pulled |
| `~/Library/LaunchAgents/com.ledatic.ledaticground-iqsched.plist` | Mini | user | compute + push schedule |
| `~/Library/LaunchAgents/com.ledatic.ledaticground-iqpull.plist` | Mini | user | pull + decode new captures (idempotent) |
| `~/Library/LaunchAgents/com.ledatic.ledaticground-iq.plist` | Mini | user | **OLD** agent — disabled at cutover, kept for rollback |

**Auto-resume guarantees** (both must hold or the decouple is pointless):
- *Pi power-cycle (nightly):* `ledaticground-iqcap.service` is `enable`d → starts on boot and
  re-reads the last-pushed `~/.iq/schedule.tsv` (persisted on /home). No Mini
  contact needed to resume capturing.
- *Mini reboot:* `…-iqsched` + `…-iqpull` are `RunAtLoad` → restart on login and
  resume pushing schedules / pulling captures.
