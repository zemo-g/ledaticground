#!/bin/bash
# ledaticground attested RF survey — the node maps what's on the air, here, now, provably.
# Sweeps a VHF marine/weather freq list on the roof Pi; for each, a short rtl_fm capture is
# characterized BY THE RAIL-TRAINED MODEL on the Pi (pure-python, decode-on-Pi -> only JSON crosses
# the weak roof WiFi). The assembled survey is Ed25519-signed (src/survey_attest.rail) into a
# hash-chained receipt: "this is what this node heard at this location, at this time."
# Voice channels (Marine Ch16, NWR voice) are NOT a trained class -> the novelty head reports
# 'unknown', a live demonstration of the node admitting it hears something it can't name.
# One-shot, on-demand (single SDR -> don't run concurrently with the AIS monitor's capture).
set -u
GD=/Users/ledaticempire/projects/ledaticground
PI=${PI_USER:-ledatic}@${PI_HOST:-ledaticground-node}
PY=/opt/homebrew/bin/python3.11
SECS=${SURVEY_SECS:-3}
OUT="$GD/data/rf_survey.json"
TMP=$(mktemp)

# freq(Hz) : label : expectation
PLAN="161975000:AIS-A:msk
162025000:AIS-B:msk
162550000:NWR-WX3:carrier
162400000:NWR-WX2:carrier
156800000:Marine-Ch16:voice(unknown)
160000000:control-empty:noise
137500000:APT-antenna-gap:noise"

echo "ledaticground RF survey — $(date -u +%FT%TZ)" >&2
printf '%s\n' "$PLAN" | while IFS=: read -r FREQ LABEL EXP; do
  [ -z "$FREQ" ] && continue
  j=$(perl -e 'alarm 150; exec @ARGV' ssh -n -o ConnectTimeout=12 -o ServerAliveInterval=15 -o ServerAliveCountMax=2 "$PI" \
      "timeout $((SECS+3)) rtl_fm -f $FREQ -M fm -s 48000 -g 40 -l 0 /tmp/survey.s16 2>/dev/null; \
       timeout 120 python3 /home/ledatic/pi_characterize.py /tmp/survey.s16 /home/ledatic/audio_softmax.txt /home/ledatic/audio_novelty.txt 2>/dev/null" 2>/dev/null | grep '^{' | head -1)
  echo "${FREQ}|${LABEL}|${EXP}|${j}" >> "$TMP"
  echo "  ${LABEL} ($(echo "scale=3;$FREQ/1000000"|bc) MHz): ${j:-<no-capture>}" >&2
done

# assemble the survey JSON (no signing yet — that's survey_attest.rail)
ts=$(date +%s)
"$PY" - "$TMP" "$ts" "$OUT" <<'PY'
import sys, json
tmp, ts, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]
entries = []
for line in open(tmp):
    parts = line.rstrip("\n").split("|", 3)
    if len(parts) < 4: continue
    freq, label, exp, j = parts
    e = {"freq_mhz": round(int(freq)/1e6, 4), "label": label, "expected": exp}
    if j.strip():
        d = json.loads(j)
        e["windows"] = d.get("windows", 0)
        uw, w = d.get("unknown_windows", 0), d.get("windows", 1)
        # headline 'unknown' when the novelty head dominates (a signal we can't name), else the
        # dominant recognized class, else noise
        e["heard"] = "unknown" if uw > w * 0.5 else (d.get("dominant_signal") or "noise")
        e["unknown_windows"] = uw
        e["classes"] = d.get("classes", {})
        if "params" in d: e["params"] = d["params"]
    else:
        e["heard"] = "<no-capture>"
    entries.append(e)
survey = {"survey": "ledaticground-rf-survey", "station": "ledaticground-roof",
          "captured_unix": ts, "band": "VHF marine/weather 137-163 MHz",
          "model": "rail-trained-audio-softmax+novelty", "entries": entries}
open(out, "w").write(json.dumps(survey, indent=2))
print(f"wrote {out}: {len(entries)} freqs surveyed", file=sys.stderr)
PY
rm -f "$TMP"
# sign it
cd /Users/ledaticempire/projects/rail && ./rail_native run "$GD/src/survey_attest.rail" 2>/dev/null | grep -E 'VERIFY|TAMPER|CHAIN|WROTE' >&2
echo "RF survey complete -> $OUT + data/rf_survey_receipt.json" >&2
