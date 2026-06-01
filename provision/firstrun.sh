#!/bin/bash
# ledaticground one-shot first-boot WiFi+SSH provisioner for Raspberry Pi OS Bookworm.
# WHY THIS EXISTS: on Bookworm (pi-gen 2024-11+), the passive boot-partition
# wpa_supplicant.conf is NOT imported — the Pi never gets WiFi. This script runs via
# the kernel `systemd.run` cmdline hook (fires regardless of prior first-boot state),
# converts each network={} block in wpa_supplicant.conf into a native NetworkManager
# .nmconnection (autoconnect, descending priority), enables SSH, installs the Mini's
# key, then removes itself from cmdline and the success_action reboots into WiFi.
# Install: place on the boot partition + append to cmdline.txt (ONE line):
#   systemd.run=/boot/firmware/firstrun.sh systemd.run_success_action=reboot systemd.unit=kernel-command-line.target
set +e
exec >/boot/firmware/firstrun.log 2>&1
echo "ledaticground firstrun $(date -u)"
/usr/bin/python3 - /boot/firmware/wpa_supplicant.conf <<'PYEOF'
import re,sys,os
txt=open(sys.argv[1]).read()
for i,b in enumerate(re.findall(r'network=\{(.*?)\}',txt,re.S)):
    s=re.search(r'ssid="([^"]*)"',b); p=re.search(r'psk="([^"]*)"',b)
    if not(s and p): continue
    s,p=s.group(1),p.group(1)
    conf=("[connection]\nid=%s\ntype=wifi\nautoconnect=true\nautoconnect-priority=%d\n\n"
          "[wifi]\nmode=infrastructure\nssid=%s\n\n"
          "[wifi-security]\nkey-mgmt=wpa-psk\npsk=%s\n\n"
          "[ipv4]\nmethod=auto\n\n[ipv6]\nmethod=auto\n") % (s,10-i,s,p)
    fn="/etc/NetworkManager/system-connections/"+s.replace(' ','_').replace("'","")+".nmconnection"
    open(fn,'w').write(conf); os.chmod(fn,0o600)
    print("wrote",fn,"ssid=",s)
PYEOF
rfkill unblock wifi 2>/dev/null
raspi-config nonint do_wifi_country US 2>/dev/null
systemctl enable ssh 2>/dev/null
H=$(getent passwd 1000|cut -d: -f6); U=$(getent passwd 1000|cut -d: -f1)
mkdir -p "$H/.ssh"; cat /boot/firmware/mini_authorized_key.pub >> "$H/.ssh/authorized_keys" 2>/dev/null
chown -R "$U:$U" "$H/.ssh" 2>/dev/null; chmod 700 "$H/.ssh"; chmod 600 "$H/.ssh/authorized_keys" 2>/dev/null
sed -i 's# systemd.run=/boot/firmware/firstrun.sh systemd.run_success_action=reboot systemd.unit=kernel-command-line.target##' /boot/firmware/cmdline.txt
sync
echo "firstrun complete $(date -u)"
