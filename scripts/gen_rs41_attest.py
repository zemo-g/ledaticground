#!/usr/bin/env python3
# RS41-6 generator: stage the synthetic decode facts the Rail attest module reads, by
# running the full decoder chain (gen_rs41_decode.py -> rs41_decode.rail) and parsing its
# FACT (/tmp/rs41_facts.txt) + DERIVED (/tmp/rs41_derived.txt) outputs into the individual
# /tmp/rs41_*.txt the attest reads via field_or. Honest placeholders stay where data is
# absent (pulse -> PENDING_no_pulse, geo -> PENDING_needs_GPS_PPS). SYNTHETIC-ONLY.
import sys, subprocess, os

HERE = os.path.dirname(os.path.abspath(__file__))
RN = '/Users/ledaticempire/projects/rail/rail_native'
GD = '/Users/ledaticempire/projects/ledaticground'
snr = sys.argv[sys.argv.index('--snr')+1] if '--snr' in sys.argv else '25'

# 0. stage the SHARED beacon pulse (canonical /tmp/lg_pulse_id.txt + /tmp/lg_pulse_hex.txt).
#    The fetcher is honest-fallback: beacon unreachable -> PENDING (never fabricated, never 0).
subprocess.run(['bash', os.path.join(HERE, 'fetch_beacon_pulse.sh')],
               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# 1. build the integration vector + run the full decoder (writes /tmp/rs41_facts.txt etc.)
subprocess.run(['/opt/homebrew/bin/python3.11', os.path.join(HERE, 'gen_rs41_decode.py'),
                '--snr', snr], check=True, stdout=subprocess.DEVNULL)
out = subprocess.run(['bash', os.path.join(HERE, 'railrun.sh'),
                      os.path.join(GD, 'src/rs41_decode.rail')],
                     capture_output=True, text=True).stdout

# 2. parse the FACTS file
facts = {}
for l in open('/tmp/rs41_facts.txt'):
    if '=' in l:
        k, v = l.strip().split('=', 1); facts[k] = v
derived = {}
for l in open('/tmp/rs41_derived.txt'):
    if '=' in l:
        k, v = l.strip().split('=', 1); derived[k] = v

# 3. count RS corrections from the RUNG4 line
rs_corrected = 0
for l in out.splitlines():
    if l.startswith('RUNG4_RS'):
        for tok in l.split():
            if tok.startswith('cw0_nerr=') or tok.startswith('cw1_nerr='):
                try: rs_corrected += int(tok.split('=')[1])
                except ValueError: pass

def w(path, val): open(path, 'w').write(str(val))

w('/tmp/rs41_serial.txt', facts.get('serial', 'PENDING_no_serial'))
w('/tmp/rs41_frame_num.txt', facts.get('frame_num', 'PENDING_no_frame'))
w('/tmp/rs41_rs_ok.txt', facts.get('rs_ok', '0'))
w('/tmp/rs41_rs_corrected.txt', rs_corrected)
w('/tmp/rs41_payload_hex.txt', facts.get('payload_hex', '00'))
# INFERENCE product: lat,lon,alt as a single field; temp/RH honest PENDING
lla = f"{derived.get('lat','PENDING')},{derived.get('lon','PENDING')},{derived.get('alt_m','PENDING')}"
w('/tmp/rs41_latlonalt.txt', lla)
w('/tmp/rs41_temp_c.txt', derived.get('temp_C', 'PENDING_no_cal'))
w('/tmp/rs41_rh_pct.txt', derived.get('rh_pct', 'PENDING_no_cal'))
# synthetic detection band metadata (freq is the planted RS41 downlink; snr a placeholder)
w('/tmp/rs41_freq.txt', '405.000')
w('/tmp/rs41_snr.txt', f'{snr}')
# pulse: HONEST placeholder unless a beacon fetcher staged /tmp/rs41_pulse.txt.
# Never fabricate -- if absent the attest's field_or default (PENDING_no_pulse) stands.

print(f"rs41 attest facts staged: serial={facts.get('serial')} frame#={facts.get('frame_num')} "
      f"rs_ok={facts.get('rs_ok')} rs_corrected={rs_corrected} latlonalt={lla} "
      f"payload_hex_len={len(facts.get('payload_hex',''))}")
