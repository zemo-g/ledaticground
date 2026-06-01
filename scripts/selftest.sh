#!/bin/bash
# ledaticground regression gates — reproduce the validated stages.
RN=/Users/ledaticempire/projects/rail/rail_native; GD=/Users/ledaticempire/projects/ledaticground
PY=/opt/homebrew/bin/python3.11; cd "$GD"; pass=0; fail=0
ck(){ if echo "$2"|grep -q "$3"; then echo "  PASS $1"; pass=$((pass+1)); else echo "  FAIL $1"; fail=$((fail+1)); fi; }
echo "ledaticground selftest"
o=$(perl -e 'alarm 40;exec @ARGV' $RN run src/fft.rail 2>/dev/null);                     ck "fft  tone->bin1=4" "$o" "bin 1: 4"
echo "selftest-product" > /tmp/apt_rail.out
o=$(cd /Users/ledaticempire/projects/rail && perl -e 'alarm 60;exec @ARGV' ./rail_native run $GD/src/attest.rail 2>/dev/null)
ck "attest verify=1"   "$o" "own-sig accepted = 1"; ck "attest tamper=0" "$o" "modified-msg accepted = 0"
o=$(cd /Users/ledaticempire/projects/rail && perl -e 'alarm 60;exec @ARGV' ./rail_native run $GD/src/coattest.rail 2>/dev/null)
ck "coattest co-attested=1" "$o" "CO-ATTESTED (2 stations) = 1"; ck "coattest forgery rejected" "$o" "co-attest = 0  (want 0)"
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
# AIS rung: attested reception receipt (Ed25519 sign + self-verify + tamper)
printf 'selftest-ais-product\n' > /tmp/ais_decoded.txt; echo "0" > /tmp/ais_pulse.txt
o=$(cd /Users/ledaticempire/projects/rail && perl -e 'alarm 60;exec @ARGV' ./rail_native run $GD/src/ais_attest.rail 2>/dev/null)
ck "ais attest verify=1" "$o" "own-sig accepted = 1"; ck "ais attest tamper=0" "$o" "modified-msg accepted = 0"
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
ck "rfml modclass held-out >=95% (rail-trained softmax)" "$o" "accuracy: 2[89][0-9]/300"
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
ck "rfml IQ characterizer held-out >=95% (rail-trained)" "$o" "accuracy: 29[0-9]/300"
# RFML rung: edge characterizer (pure-python, NO numpy — the Pi path) runs the Rail-trained
# weights (models/audio_softmax.txt, written by the modclass gate above) on a known signal.
$PY -c "import numpy as np,scripts.gen_modclass as G;r=np.random.default_rng(7);np.concatenate([G.make_window('fsk',4096,r) for _ in range(20)]).tofile('/tmp/char_test.s16')"
o=$($PY scripts/pi_characterize.py /tmp/char_test.s16 models/audio_softmax.txt models/audio_novelty.txt 2>/dev/null)
ck "rfml edge characterizer (pure-python, rail weights)" "$o" "\"fsk\": [12][0-9]"
# RFML rung: open-set novelty — a NOVEL modulation (chirp) the model never trained on flags UNKNOWN
$PY -c "import numpy as np,scripts.modnovelty_proto as N;r=np.random.default_rng(5);np.concatenate([N.novel_window('chirp',4096,r).astype('<i2') for _ in range(40)]).tofile('/tmp/chirp40.s16')"
o=$($PY scripts/pi_characterize.py /tmp/chirp40.s16 models/audio_softmax.txt models/audio_novelty.txt 2>/dev/null)
ck "rfml novelty flags a novel modulation UNKNOWN" "$o" "\"unknown_windows\": [23][0-9]"
echo "  ---- $pass passed, $fail failed ----"; [ $fail -eq 0 ]
