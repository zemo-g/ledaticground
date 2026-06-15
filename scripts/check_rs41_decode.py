#!/usr/bin/env python3
# RS41-5 checker: validate every FACT + DERIVED line of the Rail full-chain decoder
# against the planted truth (/tmp/rs41_meta.txt).
#   FACTS (byte-exact): serial, frame_num, battery_mv, ECEF X/Y/Z cm, velocity cm/s,
#                       raw PTU counts; all per-block CRC_OK=1; rs_ok=1.
#   DERIVED (float tol): lat/lon (1e-4 deg) / alt (2 m) from ECEF->WGS84.
#   HONEST: temp_C / rh_pct must be PENDING_no_cal (no cal sub-frames in a single frame).
#   END_TO_END PASS required.
import sys

meta = {}
for l in open('/tmp/rs41_meta.txt'):
    if '=' in l:
        k, v = l.strip().split('=', 1); meta[k] = v

lines = {}
status = {}; gps = {}; ptu = {}; crc = {}; derived = {}; e2e = None
for l in open(sys.argv[1]):
    l = l.rstrip('\n')
    if l.startswith('CRC_OK '):
        for tok in l[7:].split():
            k, v = tok.split('='); crc[k] = int(v)
    elif l.startswith('STATUS '):
        for tok in l[7:].split():
            k, v = tok.split('=', 1); status[k] = v
    elif l.startswith('GPS_POS '):
        for tok in l[8:].split():
            k, v = tok.split('='); gps[k] = int(v)
    elif l.startswith('PTU '):
        for tok in l[4:].split():
            k, v = tok.split('='); ptu[k] = int(v)
    elif l.startswith('DERIVED '):
        for tok in l[8:].split():
            if '=' in tok:
                k, v = tok.split('=', 1); derived[k] = v
    elif l.startswith('END_TO_END '):
        e2e = l[11:].split()[0]
    elif l.startswith('RUNG4_RS'):
        for tok in l.split():
            if tok.startswith('rs_ok='):
                crc['rs_ok'] = int(tok.split('=')[1])

ok = True; msgs = []
def chk(cond, label):
    global ok
    if cond:
        msgs.append(label)
    else:
        ok = False; msgs.append('!' + label)

chk(crc.get('status') == 1 and crc.get('gpspos') == 1 and crc.get('ptu') == 1, 'all_block_crc')
chk(crc.get('rs_ok') == 1, 'rs_ok')
chk(status.get('serial') == meta['serial'], f"serial={status.get('serial')}")
chk(status.get('frame_num') == meta['frame_num'], f"frame#={status.get('frame_num')}")
chk(status.get('battery_mv') == meta['battery_mv'], 'battery')
chk(gps.get('ecef_x_cm') == int(meta['ecef_x_cm']), 'ecef_x')
chk(gps.get('ecef_y_cm') == int(meta['ecef_y_cm']), 'ecef_y')
chk(gps.get('ecef_z_cm') == int(meta['ecef_z_cm']), 'ecef_z')
chk(gps.get('vel_x_cms') == int(meta['vel_x_cms']), 'vel_x')
chk(gps.get('vel_y_cms') == int(meta['vel_y_cms']), 'vel_y')
chk(gps.get('vel_z_cms') == int(meta['vel_z_cms']), 'vel_z')
chk(ptu.get('temp_count') == int(meta['temp_count']), 'temp_count')
chk(ptu.get('rh_count') == int(meta['rh_count']), 'rh_count')
chk(ptu.get('pres_count') == int(meta['pres_count']), 'pres_count')

# DERIVED float tolerance
try:
    dlat = float(derived.get('lat')); dlon = float(derived.get('lon')); dalt = float(derived.get('alt_m'))
    chk(abs(dlat - float(meta['lat'])) < 1e-4, f"lat~{dlat:.5f}")
    chk(abs(dlon - float(meta['lon'])) < 1e-4, f"lon~{dlon:.5f}")
    chk(abs(dalt - float(meta['alt'])) < 2.0, f"alt~{dalt:.1f}")
except (TypeError, ValueError):
    ok = False; msgs.append('!derived_parse')

# honest PENDING cal
chk(derived.get('temp_C') == 'PENDING_no_cal', 'temp_PENDING')
chk(derived.get('rh_pct') == 'PENDING_no_cal', 'rh_PENDING')
chk(e2e == 'PASS', f'end_to_end={e2e}')

print(f'rs41_decode: {"PASS" if ok else "FAIL"}  ' + '  '.join(msgs))
sys.exit(0 if ok else 1)
