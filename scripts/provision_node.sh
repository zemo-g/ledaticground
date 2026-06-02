#!/bin/bash
# ledaticground node provisioner — fresh Raspberry Pi OS Lite (64-bit) -> sensor-ready.
# Brings any Pi to an IDENTICAL ledaticground sensor: rtl-sdr + the pure-python decode/characterize
# stack + the Rail-trained models + Tailscale + GPS-PPS staging + reliability hardening.
# The ONLY per-node difference is the hostname (arg 1).
#
# Run on a fresh Pi (one-liner, no files to copy first):
#   curl -fsSL https://raw.githubusercontent.com/zemo-g/ledaticground/main/scripts/provision_node.sh \
#     | sudo bash -s -- ledaticground-roof
# or:  sudo bash provision_node.sh <hostname>
#
# Idempotent: safe to re-run. Reboot once at the end to apply boot overlays.
set -euo pipefail

NODE="${1:-ledaticground-node}"
RUNUSER="${SUDO_USER:-ledatic}"
HOME_DIR="$(eval echo "~$RUNUSER")"
REPO="https://github.com/zemo-g/ledaticground.git"
log(){ printf '\n=== %s ===\n' "$*"; }
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }

log "1/9  hostname -> $NODE"
hostnamectl set-hostname "$NODE"
sed -i "s/^127.0.1.1.*/127.0.1.1\t$NODE/" /etc/hosts 2>/dev/null || echo "127.0.1.1 $NODE" >> /etc/hosts

log "2/9  system update"
apt-get update -y; DEBIAN_FRONTEND=noninteractive apt-get upgrade -y

log "3/9  packages (rtl-sdr, gps/pps, chrony, git, python3 — decode stack is stdlib-only, no pip)"
DEBIAN_FRONTEND=noninteractive apt-get install -y rtl-sdr librtlsdr-dev git python3 \
  gpsd gpsd-clients pps-tools chrony

log "4/9  rtl-sdr: blacklist the conflicting DVB kernel driver"
cat >/etc/modprobe.d/blacklist-rtlsdr.conf <<'EOF'
blacklist dvb_usb_rtl28xxu
blacklist rtl2832
blacklist rtl2830
EOF

log "5/9  deploy the ledaticground stack from the public repo"
rm -rf /tmp/lg && git clone --depth 1 "$REPO" /tmp/lg
install -o "$RUNUSER" -g "$RUNUSER" -m 644 \
  /tmp/lg/scripts/pi_ais_decode.py /tmp/lg/scripts/pi_characterize.py /tmp/lg/scripts/same_decode.py \
  /tmp/lg/models/audio_softmax.txt /tmp/lg/models/audio_novelty.txt \
  "$HOME_DIR/"
rm -rf /tmp/lg
echo "deployed: $(ls "$HOME_DIR"/pi_*.py "$HOME_DIR"/audio_*.txt 2>/dev/null | wc -l) files to $HOME_DIR"

# boot config path differs Bookworm vs older
CFG=/boot/firmware/config.txt; [ -f "$CFG" ] || CFG=/boot/config.txt

log "6/9  GPS-PPS staging (activates once the F10N is wired: UART + PPS->GPIO18)"
grep -q "^enable_uart=1"            "$CFG" || echo "enable_uart=1"            >> "$CFG"
grep -q "^dtoverlay=pps-gpio"       "$CFG" || echo "dtoverlay=pps-gpio,gpiopin=18" >> "$CFG"
grep -q "^pps-gpio" /etc/modules    || echo "pps-gpio" >> /etc/modules
grep -q "ledaticground GPS-PPS" /etc/chrony/chrony.conf || cat >>/etc/chrony/chrony.conf <<'EOF'
# ledaticground GPS-PPS — uncomment after F10N + gpsd are confirmed (ppstest /dev/pps0):
# refclock SHM 0 refid GPS precision 1e-1 offset 0.0
# refclock SHM 1 refid PPS precision 1e-7 prefer
EOF

log "7/9  low-power + reliability (it died once on a bank — never hang silently again)"
grep -q "^dtoverlay=disable-bt" "$CFG" || echo "dtoverlay=disable-bt" >> "$CFG"   # BT unused -> save power
sed -i 's/^#*RuntimeWatchdogSec=.*/RuntimeWatchdogSec=15/'  /etc/systemd/system.conf
sed -i 's/^#*RebootWatchdogSec=.*/RebootWatchdogSec=2min/'  /etc/systemd/system.conf
grep -q RuntimeWatchdogSec /etc/systemd/system.conf || echo "RuntimeWatchdogSec=15" >> /etc/systemd/system.conf
# persist WiFi power-save OFF (a WiFi blip once hung the Mini's monitor for an hour)
cat >/etc/systemd/system/wifi-powersave-off.service <<'EOF'
[Unit]
Description=Disable WiFi power save
After=network.target
[Service]
Type=oneshot
ExecStart=/sbin/iw dev wlan0 set power_save off
[Install]
WantedBy=multi-user.target
EOF
systemctl enable wifi-powersave-off.service >/dev/null 2>&1 || true

log "8/9  tailscale"
command -v tailscale >/dev/null || curl -fsSL https://tailscale.com/install.sh | sh

log "9/9  DONE — $NODE provisioned"
cat <<EOF

Next (manual, per node):
  1) sudo tailscale up            # authenticate in browser -> joins the fleet
  2) sudo reboot                  # apply UART / pps-gpio / disable-bt / watchdog overlays
  3) verify:  rtl_test -t   (plug SDR)   |   ls ~/pi_*.py ~/audio_*.txt
GPS bring-up (when F10N wired): ppstest /dev/pps0 ; gpsd ; uncomment chrony refclocks.
The Mini's ais_monitor.sh drives capture over SSH — no local service needed here.
EOF
