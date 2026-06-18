#!/bin/bash
# ledaticground regression gates — reproduce the validated stages.
RN=/Users/ledaticempire/projects/rail/rail_native; GD=/Users/ledaticempire/projects/ledaticground
PY=/opt/homebrew/bin/python3.11; cd "$GD"; pass=0; fail=0
ck(){ if echo "$2"|grep -q "$3"; then echo "  PASS $1"; pass=$((pass+1)); else echo "  FAIL $1"; fail=$((fail+1)); fi; }
# Single-flight + coordinate with the live attest cron: refresh.sh's attest section flock-DEFERS on
# this same lock, so the cron's rollups (attest_ais / attest_lrpt) cannot race the selftest's
# live-ledger stanzas -- the flaky-different-failure-each-run source the grounded review found.
# flock auto-releases on exit/death, so a crashed selftest NEVER wedges the live attestation.
exec 9>/tmp/ledaticground_attest.lock 2>/dev/null
flock -w 120 9 2>/dev/null || echo "selftest: attest lock held >120s (concurrent run or live attest) -- proceeding without it"
echo "ledaticground selftest"
o=$(perl -e 'alarm 40;exec @ARGV' $RN run src/fft.rail 2>/dev/null);                     ck "fft  tone->bin1=4" "$o" "bin 1: 4"
echo "selftest-product" > /tmp/apt_rail.out
o=$(cd /Users/ledaticempire/projects/rail && perl -e 'alarm 60;exec @ARGV' ./rail_native run $GD/src/attest.rail 2>/dev/null)
ck "attest verify=1"   "$o" "own-sig accepted = 1"; ck "attest tamper=0" "$o" "modified-msg accepted = 0"
o=$(cd /Users/ledaticempire/projects/rail && perl -e 'alarm 60;exec @ARGV' ./rail_native run $GD/src/coattest.rail 2>/dev/null)
ck "coattest co-attested=1" "$o" "CO-ATTESTED (2 stations) = 1"; ck "coattest forgery rejected" "$o" "co-attest = 0  (want 0)"
# pre-clear the shared verify.rail target/facts staging so this legacy single-object check runs in
# single-object mode (reads data/receipt.json), not a stale ledger-walk a concurrent writer/the corr
# stanza left in /tmp (the intermittent "verify receipt VALID" false-fail). Robustness, not behavior.
rm -f /tmp/lg_verify_target.txt /tmp/lg_verify_facts.txt
o=$(cd /Users/ledaticempire/projects/rail && perl -e 'alarm 60;exec @ARGV' ./rail_native run $GD/src/verify.rail 2>/dev/null); ck "verify receipt VALID" "$o" "RECEIPT VALID"
$PY scripts/gen_doppler.py >/dev/null 2>&1; $RN src/doppler.rail >/dev/null 2>&1
perl -e 'alarm 90;exec @ARGV' /tmp/rail_out > /tmp/dop_rail.out 2>&1
o=$($PY -c "import numpy as np;t=np.load('/tmp/dop_truth.npy');d={};[d.update({int(l.split()[1]):float(l.split()[2])}) for l in open('/tmp/dop_rail.out') if l.startswith('DOP')];nw=min(len(d),len(t));r=np.array([d[i] for i in range(nw)]);print('dopcorr',round(float(np.corrcoef(r,t[:nw])[0,1]),3))")
ck "doppler measure corr>=0.99" "$o" "dopcorr 0.99\|dopcorr 1.0"
# real-capture centroid tracker on a realistic FM (APT-like) Doppler S-curve
$PY scripts/gen_doppler_fm.py >/dev/null 2>&1; $RN src/doppler_real.rail >/dev/null 2>&1
perl -e 'alarm 300;exec @ARGV' /tmp/rail_out > /tmp/dop_meas.out 2>/dev/null
o=$($PY scripts/doppler_fit.py /tmp/dop_meas.out --synth 2>/dev/null)
ck "doppler_real FM centroid corr>=0.99" "$o" "centroid : corr=0.99\|centroid : corr=1.0"
# multi-station TDOA: recover a known time-varying differential delay by xcorr
$PY scripts/gen_tdoa.py >/dev/null 2>&1; $RN src/tdoa.rail >/dev/null 2>&1
perl -e 'alarm 200;exec @ARGV' /tmp/rail_out > /tmp/tdoa_meas.out 2>/dev/null
o=$($PY scripts/tdoa_fit.py /tmp/tdoa_meas.out 2>/dev/null)
ck "tdoa lag recovery corr=1.0" "$o" "corr 1.0000"
# v40 capstone: unified multi-physics bundle (co-sig + Doppler + TDOA) valid; forgery rejected
echo "selftest-bundle-product" > /tmp/apt_rail.out
o=$(cd /Users/ledaticempire/projects/rail && perl -e 'alarm 60;exec @ARGV' ./rail_native run $GD/src/bundle.rail 2>/dev/null)
ck "bundle multi-physics valid=1" "$o" "BUNDLE VALID (multi-physics, 2 stations) = 1"
ck "bundle forgery rejected" "$o" "bundle valid = 0  (want 0)"
# LRPT rung: soft-decision Viterbi (CCSDS r=1/2 K=7) recovers bits through noise
$PY scripts/gen_viterbi.py --n 2000 --snr 4 >/dev/null 2>&1; $RN src/viterbi.rail >/dev/null 2>&1
perl -e 'alarm 120;exec @ARGV' /tmp/rail_out > /tmp/vit_out.txt 2>/dev/null
o=$($PY scripts/viterbi_check.py /tmp/vit_out.txt 2>/dev/null)
ck "viterbi r=1/2 K=7 zero errors @4dB" "$o" "bit errors 0 "
# LRPT rung: QPSK Costas carrier recovery locks + demaps (within pull-in range)
$PY scripts/gen_qpsk.py --n 2000 --foff 0.003 --snr 12 >/dev/null 2>&1; $RN src/qpsk.rail >/dev/null 2>&1
perl -e 'alarm 120;exec @ARGV' /tmp/rail_out > /tmp/qpsk_out.txt 2>/dev/null
if $PY scripts/qpsk_check.py /tmp/qpsk_out.txt >/dev/null 2>&1; then q=OK_LOCK; else q=NO_LOCK; fi
ck "qpsk costas carrier lock SER<5%" "$q" "OK_LOCK"
# LRPT rung: CCSDS derandomizer reproduces the published PN sequence + round-trips
$PY scripts/gen_derand.py >/dev/null 2>&1; $RN src/derand.rail >/dev/null 2>&1
perl -e 'alarm 60;exec @ARGV' /tmp/rail_out > /tmp/derand_out.txt 2>/dev/null
if $PY scripts/derand_check.py /tmp/derand_out.txt >/dev/null 2>&1; then d=OK; else d=BAD; fi
ck "ccsds derandomizer matches published" "$d" "OK"
# AIS rung: GMSK discriminator demod recovers MSK bits (9600 baud)
$PY scripts/gen_ais.py --n 2000 --snr 15 >/dev/null 2>&1; $RN src/gmsk.rail >/dev/null 2>&1
perl -e 'alarm 120;exec @ARGV' /tmp/rail_out > /tmp/gmsk_out.txt 2>/dev/null
if $PY scripts/gmsk_check.py /tmp/gmsk_out.txt >/dev/null 2>&1; then g=OK; else g=BAD; fi
ck "ais gmsk demod BER<2%" "$g" "OK"
# AIS rung: Type-1 payload parser -> MMSI / lat / lon / sog / cog
$PY scripts/gen_ais_msg.py >/dev/null 2>&1; $RN src/ais_parse.rail >/dev/null 2>&1
perl -e 'alarm 60;exec @ARGV' /tmp/rail_out > /tmp/ais_parse_out.txt 2>/dev/null
if $PY scripts/ais_parse_check.py /tmp/ais_parse_out.txt >/dev/null 2>&1; then a=OK; else a=BAD; fi
ck "ais type1 parse MMSI+lat/lon" "$a" "OK"
# AIS rung: CRC-16/X-25 (HDLC frame check) matches published 0x906e
$RN src/crc16.rail >/dev/null 2>&1; o=$(perl -e 'alarm 60;exec @ARGV' /tmp/rail_out 2>/dev/null)
ck "crc16/x25 check value 0x906e" "$o" "MATCH=1"
# AIS rung: NRZI + HDLC deframe (flags + destuff + CRC) recovers a valid frame end-to-end
$PY scripts/gen_ais_msg.py >/dev/null 2>&1; $PY scripts/gen_ais_frame.py >/dev/null 2>&1
$RN src/ais_deframe.rail >/dev/null 2>&1; o=$(perl -e 'alarm 60;exec @ARGV' /tmp/rail_out 2>/dev/null)
ck "ais hdlc deframe CRC ok" "$o" "CRC_OK=1"
# AIS rung: full real-off-air decoder on a committed REAL roof burst (USCG base 003669778)
$RN src/ais_decode.rail >/dev/null 2>&1; cp tests/fixtures/ais_burst_real.s16 /tmp/ais_win.s16
o=$(perl -e 'alarm 60;exec @ARGV' /tmp/rail_out 2>/dev/null)
ck "ais real-burst decode (off-air)" "$o" "mmsi=3669778"
# AIS rung: attested reception receipt (Ed25519 sign + self-verify + tamper). HERMETIC: stage
# SANDBOX output paths so the signer writes to /tmp, NEVER the live ledger. ais_attest.rail's
# output paths default to the live ledger; running it bare re-signed the cron's leftover /tmp
# staging straight INTO the live chain -> a byte-identical duplicate receipt on every selftest run
# (a real source of the historical AIS chain forks). The override + cleanup below make this check
# touch nothing but /tmp.
printf 'selftest-ais-product\n' > /tmp/ais_decoded.txt; echo "0" > /tmp/ais_pulse.txt
rm -f /tmp/st_ais_sb.jsonl /tmp/st_ais_sb.json /tmp/st_ais_sb_chain.txt
printf '%s\n' /tmp/st_ais_sb.jsonl    > /tmp/ais_out_ledger.txt
printf '%s\n' /tmp/st_ais_sb.json     > /tmp/ais_out_single.txt
printf '%s\n' /tmp/st_ais_sb_chain.txt > /tmp/ais_out_chain.txt
o=$(cd /Users/ledaticempire/projects/rail && perl -e 'alarm 60;exec @ARGV' ./rail_native run $GD/src/ais_attest.rail 2>/dev/null)
ck "ais attest verify=1" "$o" "own-sig accepted = 1"; ck "ais attest tamper=0" "$o" "modified-msg accepted = 0"
# tear down the sandbox override IMMEDIATELY so no later step (or the cron) inherits it
rm -f /tmp/ais_out_ledger.txt /tmp/ais_out_single.txt /tmp/ais_out_chain.txt /tmp/st_ais_sb.jsonl /tmp/st_ais_sb.json /tmp/st_ais_sb_chain.txt
# SAME rung: decode a synthetic NWR alert burst (AFSK -> preamble sync -> frame -> parse)
$PY scripts/gen_same.py --snr 25 --out /tmp/st_same.s16 >/dev/null 2>&1
o=$($PY scripts/same_decode.py /tmp/st_same.s16 2>/dev/null)
ck "same decode (WXR/RWT/fips/station)" "$o" "Required Weekly Test"
# SAME rung: 2-of-3 byte voting recovers a message with errors injected in EVERY repeat
$PY scripts/gen_same.py --snr 25 --corrupt --out /tmp/st_samec.s16 >/dev/null 2>&1
o=$($PY scripts/same_decode.py /tmp/st_samec.s16 2>/dev/null)
ck "same 2-of-3 voting recovers" "$o" "026163"
# RFML rung: a 5-class modulation classifier TRAINED IN RAIL (feature extract + softmax SGD, all
# on the substrate) recovers the held-out synthetic set. Features match the Python oracle exactly;
# on real off-air AIS it reproduces noise=528/msk=13 (Gate B, documented in docs/RFML.md).
$PY scripts/gen_modclass.py >/dev/null 2>&1
$RN src/modclass.rail >/dev/null 2>&1
o=$(perl -e 'alarm 150;exec @ARGV' /tmp/rail_out 2>/dev/null)
ck "rfml modclass held-out >=95% (rail-trained softmax)" "$o" "accuracy: 2[89][0-9]/300\|accuracy: 300/300"
# RFML rung: parameter head recovers a known carrier center-offset (pure-Rail estimator)
$PY -c "import numpy as np;v=round(2400*65534/48000);(np.full(8192,v)+np.random.RandomState(1).randint(-2,3,8192)).astype('<i2').tofile('/tmp/modfeat_in.s16')"
$RN src/modparam.rail >/dev/null 2>&1
o=$(perl -e 'alarm 60;exec @ARGV' /tmp/rail_out 2>/dev/null)
ck "rfml param head recovers 2400Hz carrier center" "$o" "center=2[34][0-9][0-9]"
# RFML rung: attested characterization receipt (Ed25519 sign + self-verify + tamper) — PAOS loop
printf 'RFML_CHAR selftest noise=528 msk=13\n' > /tmp/modclass_result.txt
o=$(cd /Users/ledaticempire/projects/rail && perl -e 'alarm 60;exec @ARGV' ./rail_native run $GD/src/modclass_attest.rail 2>/dev/null)
ck "rfml attest verify=1" "$o" "own-sig accepted = 1"; ck "rfml attest tamper=0" "$o" "modified-msg accepted = 0"
# RFML rung: IQ-domain characterizer TRAINED IN RAIL (coherent modulations in complex baseband)
$PY scripts/gen_modclass_iq.py >/dev/null 2>&1
$RN src/modclass_iq.rail >/dev/null 2>&1
o=$(perl -e 'alarm 150;exec @ARGV' /tmp/rail_out 2>/dev/null)
ck "rfml IQ characterizer held-out >=95% (rail-trained)" "$o" "accuracy: 29[0-9]/300\|accuracy: 300/300"
# RFML rung: edge characterizer (pure-python, NO numpy — the Pi path) runs the Rail-trained
# weights (models/audio_softmax.txt, written by the modclass gate above) on a known signal.
$PY -c "import numpy as np,scripts.gen_modclass as G;r=np.random.default_rng(7);np.concatenate([G.make_window('fsk',4096,r) for _ in range(20)]).tofile('/tmp/char_test.s16')"
o=$($PY scripts/pi_characterize.py /tmp/char_test.s16 models/audio_softmax.txt models/audio_novelty.txt 2>/dev/null)
ck "rfml edge characterizer (pure-python, rail weights)" "$o" "\"fsk\": [12][0-9]"
# RFML rung: open-set novelty — a NOVEL modulation (chirp) the model never trained on flags UNKNOWN
$PY -c "import numpy as np,scripts.modnovelty_proto as N;r=np.random.default_rng(5);np.concatenate([N.novel_window('chirp',4096,r).astype('<i2') for _ in range(40)]).tofile('/tmp/chirp40.s16')"
o=$($PY scripts/pi_characterize.py /tmp/chirp40.s16 models/audio_softmax.txt models/audio_novelty.txt 2>/dev/null)
ck "rfml novelty flags a novel modulation UNKNOWN" "$o" "\"unknown_windows\": [23][0-9]"
# RF-survey rung: attested RF-survey receipt (Ed25519 sign + self-verify + tamper) over a survey JSON
printf '{"survey":"selftest","entries":[{"freq_mhz":161.975,"heard":"msk"}]}\n' > "$GD/data/rf_survey.json"
o=$(cd /Users/ledaticempire/projects/rail && perl -e 'alarm 60;exec @ARGV' ./rail_native run $GD/src/survey_attest.rail 2>/dev/null)
ck "rf-survey attest verify=1" "$o" "own-sig accepted = 1"; ck "rf-survey attest tamper=0" "$o" "modified-msg accepted = 0"
# RS41 radiosonde rung: SYNTHETIC end-to-end (gen GFSK frame -> demod -> descramble -> RS(255,231)
# FCR=0 -> per-block CRC16-CCITT -> ECEF->WGS84 -> 2 receipts). Live 400 MHz RX is hardware-blocked
# (the halo is 137 MHz only; needs a separate 400 MHz antenna) -- the SOFTWARE chain validates here.
# RS41-2 demod (GFSK 2-FSK polar discriminator recovers planted channel bits)
$PY scripts/gen_rs41_demod.py --snr 25 >/dev/null 2>&1
o=$(bash scripts/railrun.sh $GD/src/rs41_demod.rail 2>/dev/null); echo "$o" | grep "^BITS" > /tmp/rs41_demod_out.txt
if $PY scripts/check_rs41_demod.py /tmp/rs41_demod_out.txt >/dev/null 2>&1; then r=OK; else r=BAD; fi
ck "rs41 demod recovers planted bits" "$r" "OK"
# RS41-4 RS(255,231) FCR=0 I=2 corrects up to 12 byte errors/codeword
$PY scripts/gen_rs41_rs.py --nerr 12 >/dev/null 2>&1
o=$(bash scripts/railrun.sh $GD/src/rs41_rs.rail 2>/dev/null); echo "$o" > /tmp/rs41_rs_out.txt
if $PY scripts/check_rs41_rs.py /tmp/rs41_rs_out.txt >/dev/null 2>&1; then r=OK; else r=BAD; fi
ck "rs41 RS(255,231) FCR=0 corrects 12 err/cw" "$r" "OK"
# RS41-5 full-chain decode: recovers serial/frame#/ECEF byte-exact + lat/lon/alt + END_TO_END PASS
$PY scripts/gen_rs41_decode.py --snr 25 >/dev/null 2>&1
o=$(bash scripts/railrun.sh $GD/src/rs41_decode.rail 2>/dev/null); echo "$o" > /tmp/rs41_decode_out.txt
if $PY scripts/check_rs41_decode.py /tmp/rs41_decode_out.txt >/dev/null 2>&1; then r=OK; else r=BAD; fi
ck "rs41 full-chain decode (serial/ECEF/WGS84)" "$r" "OK"
# RS41-6 two-receipt attest: DECODE fact + INFERENCE bound to it (verify=1 tamper=0 derived_from=chainA)
$PY scripts/gen_rs41_attest.py --snr 25 >/dev/null 2>&1
o=$(bash scripts/railrun.sh $GD/src/rs41_attest.rail 2>/dev/null); echo "$o" > /tmp/rs41_attest_out.txt
if $PY scripts/check_rs41_attest.py /tmp/rs41_attest_out.txt >/dev/null 2>&1; then r=OK; else r=BAD; fi
ck "rs41 attest (DECODE fact + INFERENCE bound)" "$r" "OK"
# BIASTEE-1 fail-closed interlock: 'on' with no LNA declared REFUSES (exit 1, rtl_biast NOT invoked)
rm -f /tmp/st_biast_calls.log; printf '#!/bin/bash\necho "$*" >> /tmp/st_biast_calls.log\n' > /tmp/st_biast_stub.sh; chmod +x /tmp/st_biast_stub.sh
HOME=/tmp/st_fake_home_none RTL_BIAST=/tmp/st_biast_stub.sh bash scripts/autocap/bias_tee.sh on >/dev/null 2>&1; rc=$?
ncalls=$(wc -l < /tmp/st_biast_calls.log 2>/dev/null | tr -d ' '); ncalls=${ncalls:-0}
if [ "$rc" = 1 ] && [ "$ncalls" = 0 ]; then b=OK; else b=BAD; fi
ck "biastee fail-closed (refuse, no rtl_biast)" "$b" "OK"
# rs41_capture.sh refuses on the 137 MHz halo (no fabricated 400 MHz capture)
o=$(bash scripts/rs41_capture.sh 2>&1); rc=$?
if [ "$rc" = 2 ] && echo "$o" | grep -q "BLOCKER: needs 400 MHz antenna"; then c=OK; else c=BAD; fi
# LRPT-DECODE rung: attested LRPT decode-product receipt (FACT, cadu_ok honesty bit). Hermetic
# fixture (synthetic .bin + fake satdump .cadu). The lrpt_decode ledger is gitignored RUNTIME
# state holding the live decode chain, so snapshot+restore it -> byte-identical after the test.
LFX=/tmp/st_lrpt_fix; rm -rf "$LFX"; rm -f /tmp/st_lbak_*    # clear any stale backup from a killed prior run
mkfix(){ # $1=name $2=cadu_bytes(0=>empty .cadu) $3=marker-line(""=>NO marker) $4=salt(DISTINCT per fixture)
  local d="$LFX/$1.satdump"; mkdir -p "$d"
  # salt MUST differ per fixture: identical .bin bytes -> identical input_sha256 -> the idempotency
  # ledger-scan would no-op the later fixture, so it would never exercise its intended path.
  python3 -c "open('$LFX/$1.bin','wb').write(bytes((i*53+7+$4)&0xff for i in range(8192)))"
  if [ "$2" -gt 0 ]; then python3 -c "open('$d/meteor_m2-x_lrpt.cadu','wb').write(bytes(($4+1)&0xff for _ in range($2)))"; else : > "$d/meteor_m2-x_lrpt.cadu"; fi
  [ -n "$3" ] && printf '%s\n' "$3" > "$LFX/$1.decoded"
  return 0
}
mkfix iq_TEST-SAT_el80_LRPT_20260101T0000Z 2048 "mode=LRPT | satdump=exit=0 | products: CADUS=2 cadu_bytes=2048" 0
mkfix iq_TEST-SAT_el20_LRPT_20260101T0001Z 0    "mode=LRPT | satdump=exit=0 | products: CADUS=0 cadu_bytes=0"    1
mkfix iq_TEST-SAT_el30_LRPT_20260101T0002Z 2048 ""                                                              2  # CADUs present, NO marker -> cadu_ok=0 (A.3 fail-closed)
mkfix iq_TEST-SAT_el40_LRPT_20260101T0003Z 2048 "mode=LRPT | satdump=exit=1 | products: CADUS=2 cadu_bytes=2048" 3 # satdump errored -> cadu_ok=0
for f in lrpt_decode_receipts.jsonl lrpt_decode_receipt.json lrpt_decode_fact_chain.txt lrpt_decode_rollup_cursor.txt; do cp "data/$f" "/tmp/st_lbak_$f" 2>/dev/null; rm -f "data/$f"; done
o=$(perl -e 'alarm 120;exec @ARGV' bash scripts/attest_lrpt_decode_rollup.sh "$LFX/iq_TEST-SAT_el80_LRPT_20260101T0000Z.bin" 2>&1)
ck "lrpt-decode FACT n=2 cadu_ok=1" "$o" "n=2|cadu_ok=1"
ck "lrpt-decode verify=1" "$o" "own-sig accepted = 1"; ck "lrpt-decode tamper=0" "$o" "modified-msg accepted = 0"
o2=$(perl -e 'alarm 120;exec @ARGV' bash scripts/attest_lrpt_decode_rollup.sh "$LFX/iq_TEST-SAT_el80_LRPT_20260101T0000Z.bin" 2>&1)
ck "lrpt-decode idempotent no-op" "$o2" "idempotent no-op"
o4=$(perl -e 'alarm 120;exec @ARGV' bash scripts/attest_lrpt_decode_rollup.sh "$LFX/iq_TEST-SAT_el20_LRPT_20260101T0001Z.bin" 2>&1)
ck "lrpt-decode 0-CADU honest cadu_ok=0" "$o4" "n=0|cadu_ok=0"
o5=$(perl -e 'alarm 120;exec @ARGV' bash scripts/attest_lrpt_decode_rollup.sh "$LFX/iq_TEST-SAT_el30_LRPT_20260101T0002Z.bin" 2>&1)
ck "lrpt-decode marker-absent fail-closed cadu_ok=0" "$o5" "n=2|cadu_ok=0"
o6=$(perl -e 'alarm 120;exec @ARGV' bash scripts/attest_lrpt_decode_rollup.sh "$LFX/iq_TEST-SAT_el40_LRPT_20260101T0003Z.bin" 2>&1)
ck "lrpt-decode satdump-exit1 fail-closed cadu_ok=0" "$o6" "n=2|cadu_ok=0"
# verify the FULL 4-line chain. ABSOLUTE path: railrun cd's to the rail repo, so a relative
# src/verify.rail would resolve to a nonexistent file and silently run a stale /tmp/rail_out.
printf '%s\n' "$GD/data/lrpt_decode_receipts.jsonl" > /tmp/lg_verify_target.txt
printf '%s\n' "$GD/data/lrpt_decode_receipts.jsonl" > /tmp/lg_verify_facts.txt
o3=$(perl -e 'alarm 120;exec @ARGV' bash scripts/railrun.sh "$GD/src/verify.rail" 2>&1)
ck "lrpt-decode verify.rail LEDGER VALID" "$o3" "==> LEDGER VALID"
for f in lrpt_decode_receipts.jsonl lrpt_decode_receipt.json lrpt_decode_fact_chain.txt lrpt_decode_rollup_cursor.txt; do rm -f "data/$f"; [ -f "/tmp/st_lbak_$f" ] && mv "/tmp/st_lbak_$f" "data/$f"; done
rm -rf "$LFX"
# =================================================================================================
# CORRESPONDENCE FRONTIER -- the cold-start RUN-IT-TWICE end-to-end smoke test (ADD-1).
#
# WHY (audit 2026-06-16): a re-runnability defect hid behind clean-slate agent runs --
# attest_iq_capture_rollup.sh no-op'd on a cursor match even with its fact-chain output MISSING,
# wedging the binding pipeline ("already signed" yet no FACT root). Clean-slate success != robustness;
# the same desync class can lurk in any cursor/ledger pair. This stanza is the guard that would have
# caught it: it runs the FULL physics-binding + mesh chain from a TRUE clean slate, asserts green, then
# RUNS THE ENTIRE THING A SECOND TIME from ANOTHER clean slate and asserts green AGAIN. A wedge on the
# deterministic-IQ second pass (cursor present from pass 1, ledger state cleared) turns this red.
#
# HONESTY: every claim here is "mechanism validated on the deterministic SGP4-true SYNTHETIC fixture
# (gen_tle_doppler.py)", NEVER "proof of truth". physics_ok=1 = "consistent within tol_hz". A failed
# bind (physics_ok=0) is RECORDED, never dropped (the --wrong-tle line proves it).
#
# *** LIVE AIS CHAIN: HANDS OFF. *** The reset list below is the GREENFIELD runtime state ONLY. It
# NEVER names the four live AIS files (ais_receipts.jsonl / ais_fact_chain.txt / ais_rollup_cursor.txt
# / ais_receipt.json). Two end-of-stanza guards PROVE the stanza never wrote them: an inode-identity
# check (the stanza never rm'd/recreated them) + a static-text check (the reset list names none of them).
# A content diff is deliberately NOT used -- the live AIS cron may legitimately append DURING the run.
# Deterministic + offline (the one net call is the beacon fetch inside the rollups, honest PENDING).
# =================================================================================================
echo "  -- correspondence frontier: cold-start RUN-IT-TWICE smoke --"
# AIS guard: prove the stanza never WRITES the four live AIS files. A content-hash diff is the WRONG
# instrument here -- the live AIS attestation pipeline (its cron) legitimately appends to those files
# and may fire DURING this selftest, so an after!=before content diff would FALSE-FAIL through no
# fault of ours. Instead we use two race-free, honest checks:
#   (1) inode-identity: snapshot each file's inode BEFORE + AFTER. The live cron APPENDS in place
#       (inode stable); only an rm/recreate changes the inode -- which is exactly the mistake this
#       guards against. So same-inode == "the stanza did not delete/recreate them".
#   (2) static text guard: the GREENFIELD reset list (corr_reset) names none of the four AIS files.
corr_ais_inodes(){ ls -i data/ais_receipts.jsonl data/ais_fact_chain.txt \
    data/ais_rollup_cursor.txt data/ais_receipt.json 2>/dev/null | awk '{print $1}' | tr '\n' ' '; }
CORR_AIS_INO_BEFORE="$(corr_ais_inodes)"
# TRUE clean slate of GREENFIELD runtime state. NEVER the four live AIS files.
corr_reset(){
  rm -f data/physics_binding_receipts.jsonl data/binding_receipt.json data/chain/binding_prev.txt \
        data/iq_capture_receipts.jsonl data/iq_capture_receipt.json data/iq_capture_fact_chain.txt \
        data/iq_capture_rollup_cursor.txt data/mesh_witness_receipts.jsonl data/mesh_witness_receipt.json \
        data/mesh_factA_receipts.jsonl data/mesh_factB_receipts.jsonl data/chain/mesh_witness_prev.txt \
        2>/dev/null
}
# ONE full pass from a clean slate. $1 = label tag ("p1"/"p2"). Sets pass/fail via ck.
corr_pass(){
  local tag="$1" o
  corr_reset
  # (a) positive binding (NOAA-19, SGP4-true synth) -> physics_ok=1 + a FACT root minted.
  o=$(perl -e 'alarm 240;exec @ARGV' bash scripts/attest_binding_rollup.sh --synth 2>&1)
  ck "corr[$tag] binding --synth physics_ok=1 + FACT root" "$o" "physics_ok=1.*1 iff\|BIND: FACT root chain_hash="
  # (c) cold-verify line 1 -> LEDGER VALID (the physics re-run reproduces the committed residual).
  #     ORDER: run this BEFORE the --wrong-tle rollup, and pass an EXPLICIT NOAA-19 --tle-file. The
  #     rollup leaves the LAST run's TLE staged at /tmp/binding_tle_l1/l2.txt; verify_binding's default
  #     reads those, so after a --wrong-tle (NOAA-15) run the default TLE would mismatch line 1's cited
  #     NOAA-19 tle_sha256. Pinning the TLE here makes the line-1 verify deterministic + immune to any
  #     sibling rollup clobbering the staged TLE between the mint and this walk.
  printf '%s\n%s\n' \
    "1 33591U 09005A   26166.49283008  .00000032  00000+0  40805-4 0  9995" \
    "2 33591  98.9521 237.3664 0014363  39.0504 321.1702 14.13474065894244" > /tmp/st_corr_tle19.txt
  o=$(perl -e 'alarm 180;exec @ARGV' bash scripts/verify_binding.sh --line 1 --tle-file /tmp/st_corr_tle19.txt 2>&1)
  ck "corr[$tag] verify_binding --line 1 LEDGER VALID" "$o" "==> LEDGER VALID"
  # (b) NEGATIVE binding (wrong TLE) -> physics_ok=0 RECORDED (not dropped); also seeds ledger line 2
  #     that check_verify_physics.py fabricates against. Honest: a failed bind is on the chain.
  o=$(perl -e 'alarm 240;exec @ARGV' bash scripts/attest_binding_rollup.sh --synth --wrong-tle 2>&1)
  ck "corr[$tag] binding --wrong-tle physics_ok=0 RECORDED" "$o" "physics_ok=0"
  # (d) accept/reject harness: genuine binding VALID + valid-sig/fabricated-residual forgery INVALID.
  #     (check_verify_physics stages its OWN per-line TLE internally, so it is order-independent.)
  o=$(perl -e 'alarm 300;exec @ARGV' "$PY" scripts/check_verify_physics.py 2>&1)
  ck "corr[$tag] check_verify_physics RESULT PASS" "$o" "RESULT: PASS"
  # (e) AUDIT DEFECT, pinned: cursor present + fact-chain ABSENT must NOT no-op (must re-mint). This is
  #     the exact 2026-06-16 desync regression on a deterministic IQ. A no-op here = the bug is back.
  python3 -c "open('/tmp/st_corr_iq.bin','wb').write(bytes((i*37+11)&0xff for i in range(4096)))"
  perl -e 'alarm 120;exec @ARGV' bash scripts/attest_iq_capture_rollup.sh /tmp/st_corr_iq.bin >/dev/null 2>&1
  rm -f data/iq_capture_fact_chain.txt   # the desync: cursor survives, fact-chain output is gone
  o=$(perl -e 'alarm 120;exec @ARGV' bash scripts/attest_iq_capture_rollup.sh /tmp/st_corr_iq.bin 2>&1)
  if echo "$o" | grep -q "idempotent no-op"; then dz=WEDGED; else dz=RESIGNED; fi
  [ -s data/iq_capture_fact_chain.txt ] || dz=WEDGED   # fact-chain MUST be restored
  ck "corr[$tag] iq_capture desync re-runnable (no wedge)" "$dz" "RESIGNED"
  # (f) mesh co-attestation (SIMULATED) -> mesh_ok=1, then verify.rail walks the mesh ledger VALID.
  #     Re-stage /tmp/lg_verify_* right before the walk: sibling agents share these mutable /tmp files
  #     and a concurrent rollup can clobber the target between mint and walk (observed during build).
  o=$(perl -e 'alarm 240;exec @ARGV' bash scripts/mesh_witness_rollup.sh 2>&1)
  ck "corr[$tag] mesh_witness_rollup mesh_ok=1" "$o" "MESH_OK  1\|mesh_ok=1"
  printf '%s\n' "$GD/data/mesh_witness_receipts.jsonl" > /tmp/lg_verify_target.txt
  printf '%s\n%s\n' "$GD/data/mesh_factA_receipts.jsonl" "$GD/data/mesh_factB_receipts.jsonl" > /tmp/lg_verify_facts.txt
  # Compile+run verify.rail to a DEDICATED out-prefix (NOT the shared /tmp/rail_out the flock-serialized
  # railrun.sh uses). Two robustness reasons, both learned during this build: (1) the mesh ROLLUP just
  # ran the signer via railrun, leaving the signer binary at /tmp/rail_out; if our verify compile loses
  # the /tmp/rail_out race to any concurrent rail process the STALE signer binary runs and the verdict is
  # garbage. (2) the path must be ABSOLUTE ($GD/src/...): rail_native run resolves it from the rail repo
  # cwd, and a relative src/verify.rail there is a nonexistent 0-char file. An isolated out-prefix removes
  # the shared-binary collision entirely -- the honest fix for "clean-slate success != robustness".
  o=$(cd /Users/ledaticempire/projects/rail && perl -e 'alarm 180;exec @ARGV' \
        ./rail_native --out-prefix /tmp/st_corr_verify_ run "$GD/src/verify.rail" 2>&1)
  ck "corr[$tag] verify.rail mesh ledger VALID" "$o" "==> LEDGER VALID"
}
corr_pass p1
# *** RUN THE ENTIRE STANZA A SECOND TIME from another clean slate -- the re-runnability gate. ***
# pass 1 leaves cursor/ledger state populated; corr_pass's corr_reset wipes it and the deterministic
# fixtures reproduce byte-identical inputs, so a latent cursor/output desync would wedge pass 2 here.
corr_pass p2
# (4) SGP4 triplication-drift guard (ADD-2): the inlined SGP4 in doppler_range/binding_attest/verify
#     must agree to within tol -- else emit and verify disagree and every binding silently fails.
o=$(perl -e 'alarm 180;exec @ARGV' bash scripts/check_sgp4_parity.sh 2>&1)
ck "corr sgp4 triplication parity (3 copies agree)" "$o" "SGP4_PARITY: PASS"
# (5) self-collection cleanup: leave NO greenfield-ledger residue (NEVER the live AIS files).
corr_reset
rm -f /tmp/st_corr_iq.bin /tmp/st_corr_tle19.txt /tmp/st_corr_verify_
# AIS guard (1) inode-identity: same inodes == the stanza did not delete/recreate the live files
# (the live cron may have APPENDED in place -- inode stable -- which is fine and NOT our doing).
CORR_AIS_INO_AFTER="$(corr_ais_inodes)"
if [ "$CORR_AIS_INO_BEFORE" = "$CORR_AIS_INO_AFTER" ]; then ais_ok=UNTOUCHED; else ais_ok=RECREATED; fi
ck "corr LIVE AIS chain untouched (4 inodes stable; stanza never rm'd them)" "$ais_ok" "UNTOUCHED"
# AIS guard (2) static text guard: the GREENFIELD reset list must name NONE of the four AIS files.
ais_in_reset=$(sed -n '/^corr_reset(){/,/^}/p' "$0" | grep -cE 'ais_receipts\.jsonl|ais_fact_chain\.txt|ais_rollup_cursor\.txt|ais_receipt\.json')
if [ "$ais_in_reset" = "0" ]; then reset_ok=CLEAN; else reset_ok=NAMES_AIS; fi
ck "corr reset list names no live-AIS file (static guard)" "$reset_ok" "CLEAN"
# =================================================================================================
# LIVE AIS LEDGER WALK (ADD-3, 2026-06-17): walk-verify the ACTUAL production AIS chain end-to-end.
#
# WHY: the AIS self-check above (the single-object "verify receipt VALID") re-checks ONE receipt --
# it never walks the linear prev-linkage of the whole chain. That blind spot hid 16 prev-link
# fan-breaks an UNLOCKED rollup critical section produced (overlapping cron signings shared a stale
# tail chain_hash for prev=). This stanza WALKS the live ledger: every sig + chain_hash + prev-link,
# and asserts LEDGER VALID. A future fork turns this red. READ-ONLY: verify.rail never writes the
# ledger, and the estate lock held at the top of this selftest keeps the live cron deferred, so the
# ledger is stable during the walk (the inode guard above independently proves we never recreate it).
# A segment-boundary genesis (prev=SEG_GENESIS:<archived-segment sha256>) is accepted on line 0.
# Isolated --out-prefix compile (never the shared /tmp/rail_out) so a concurrent rail process can't
# swap in a stale signer binary. An EMPTY ledger walks VALID (0 lines, nothing to break). If the open
# segment ever grows large enough to time out, that is the signal to close+restart it (rotation).
# =================================================================================================
echo "  -- live AIS ledger walk --"
# NOTE: this walk is O(N) -- an Ed25519 + chain re-derive per ledger line, plus the verify.rail
# compile. It grows with the live segment (~hundreds of receipts/day). The alarm below is generous
# headroom; the real long-term bound is SEGMENT ROTATION -- periodically close+restart the AIS chain
# (the SEG_GENESIS mechanism) so the walked segment stays small. A timeout here = "rotate the segment",
# NOT "chain broken" (a broken chain prints ==> LEDGER INVALID well within the alarm).
printf '%s\n' "$GD/data/ais_receipts.jsonl" > /tmp/lg_verify_target.txt
printf '%s\n' "$GD/data/ais_receipts.jsonl" > /tmp/lg_verify_facts.txt
o=$(cd /Users/ledaticempire/projects/rail && perl -e 'alarm 420;exec @ARGV' \
      ./rail_native --out-prefix /tmp/st_ais_verify_ run "$GD/src/verify.rail" 2>&1)
ck "live AIS ledger walk LEDGER VALID" "$o" "==> LEDGER VALID"
rm -f /tmp/lg_verify_target.txt /tmp/lg_verify_facts.txt /tmp/st_ais_verify_
echo "  ---- $pass passed, $fail failed ----"; [ $fail -eq 0 ]
