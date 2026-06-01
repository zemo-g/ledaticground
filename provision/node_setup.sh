#!/bin/bash
# Provision a fresh headless Pi into a ledaticground capture node.
# Run from the Mini:  ssh ledatic@<pi-ip> 'bash -s' -- <TS_AUTHKEY> < provision/node_setup.sh
set -e
AUTHKEY="${1:?need Tailscale auth key as arg 1}"
echo "== hostname =="; sudo hostnamectl set-hostname ledaticground-roof || true
echo "== authorize Mini SSH key =="
mkdir -p ~/.ssh && chmod 700 ~/.ssh
[ -f /boot/firmware/mini_authorized_key.pub ] && cat /boot/firmware/mini_authorized_key.pub >> ~/.ssh/authorized_keys
sort -u ~/.ssh/authorized_keys -o ~/.ssh/authorized_keys 2>/dev/null || true; chmod 600 ~/.ssh/authorized_keys
echo "== SDR tools =="; sudo apt-get update -y && sudo apt-get install -y rtl-sdr
echo 'blacklist dvb_usb_rtl28xxu' | sudo tee /etc/modprobe.d/rtlsdr-blacklist.conf >/dev/null
echo "== Tailscale =="; curl -fsSL https://tailscale.com/install.sh | sudo sh
sudo tailscale up --authkey="$AUTHKEY" --hostname=ledaticground-roof --ssh
echo "== console-only boot (save RAM on the Zero 2 W) =="; sudo raspi-config nonint do_boot_behaviour B1 || true
echo "== install capture script =="
sudo tee /usr/local/bin/pi_record.sh >/dev/null <<'REC'
#!/bin/bash
# record one APT pass to a local file (the Mini pulls it). usage: pi_record.sh <freq> <dur_s> <label>
FREQ=${1:-137620000}; DUR=${2:-900}; LABEL=${3:-roof}
OUT=/tmp/cap_${LABEL}_$(date -u +%Y%m%dT%H%M%SZ).s16
timeout "$DUR" rtl_fm -f "$FREQ" -M fm -s 48000 -r 11025 -A fast -g 49 -E deemp - 2>/tmp/rec.log > "$OUT"
echo "$OUT"
REC
sudo chmod 755 /usr/local/bin/pi_record.sh
echo "== SDR check =="; timeout 3 rtl_test -t 2>&1 | head -4 || true
echo "NODE READY  tailscale=$(tailscale ip -4 2>/dev/null | head -1)  host=ledaticground-roof"
