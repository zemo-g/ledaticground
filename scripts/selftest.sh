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
$PY scripts/gen_doppler.py >/dev/null 2>&1; $RN src/doppler.rail >/dev/null 2>&1
perl -e 'alarm 90;exec @ARGV' /tmp/rail_out > /tmp/dop_rail.out 2>&1
o=$($PY -c "import numpy as np;t=np.load('/tmp/dop_truth.npy');d={};[d.update({int(l.split()[1]):float(l.split()[2])}) for l in open('/tmp/dop_rail.out') if l.startswith('DOP')];nw=min(len(d),len(t));r=np.array([d[i] for i in range(nw)]);print('dopcorr',round(float(np.corrcoef(r,t[:nw])[0,1]),3))")
ck "doppler measure corr>=0.99" "$o" "dopcorr 0.99\|dopcorr 1.0"
echo "  ---- $pass passed, $fail failed ----"; [ $fail -eq 0 ]
