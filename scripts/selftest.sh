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
echo "  ---- $pass passed, $fail failed ----"; [ $fail -eq 0 ]
