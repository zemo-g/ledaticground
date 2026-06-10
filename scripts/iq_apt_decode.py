#!/usr/bin/env python3.11
# Raw-IQ APT processor — the software-first shot. Works on rtl_sdr raw IQ (uint8 I/Q @ 250k),
# NOT rtl_fm's pre-cooked audio. Two outputs:
#   1) WATERFALL spectrogram (the definitive diagnostic): a real APT pass paints a ~40 kHz
#      trace that drifts in frequency with Doppler over the pass; noise stays flat. The
#      peak signal-over-floor (dB) tells us if ANY 137 signal arrives.
#   2) DECODE attempt: channelize ±25 kHz, FM-demod, resample to 11025, then the same
#      subcarrier-AM + sync-lock decode as apt_decode_hq (reports SYNC_LOCK).
#
#   iq_apt_decode.py <raw_iq.u8> <out_prefix> [--fs 250000]
import sys, numpy as np
from scipy import signal
from PIL import Image

INP=sys.argv[1]; PRE=sys.argv[2]
FS=int(sys.argv[sys.argv.index('--fs')+1]) if '--fs' in sys.argv else 250000
PIX=4160; LINE=2080; SUBC=2400.0
# LRPT (Meteor) captures: the narrowband APT discriminator below is structurally blind
# to a 120 kHz OQPSK envelope (per-column argmax bounces inside the wide signal ->
# huge "drift" -> mislabels real signal "FLAT NOISE"; proven 2026-06-10 on a 1023-CADU
# M2-3 pass). And no waterfall heuristic we tested separates LRPT from pre-filter FM
# hash (wideband temporal-contrast scored EMPTY-sky captures above the true positive).
# So for LRPT the verdict defers to satdump's CADU count (validate_external.sh) —
# deterministic deframed bytes — and the APT subcarrier decode stage is skipped.
LRPT='_LRPT_' in INP.upper() or 'lrpt' in INP.lower()

raw=np.fromfile(INP,dtype=np.uint8).astype(np.float32)-127.5
iq=raw[0::2]+1j*raw[1::2]
dur=len(iq)/FS
print(f"IQ: {len(iq)} samples, {dur:.0f}s @ {FS} Hz")

# ---------------- WATERFALL (definitive diagnostic) ----------------
NFFT=4096; ncols=min(1400, max(200, len(iq)//NFFT))
hop=max(NFFT, (len(iq)-NFFT)//ncols)
f=np.fft.fftshift(np.fft.fftfreq(NFFT,1/FS))
cols=[]
for s in range(0, len(iq)-NFFT, hop):
    seg=iq[s:s+NFFT]*np.hanning(NFFT)
    cols.append(np.abs(np.fft.fftshift(np.fft.fft(seg)))**2)
spec=np.array(cols).T                               # freq x time
floor=np.median(spec)
dcmask=np.abs(f)<3000                                # kill the RTL DC spike (not a signal)
spec[dcmask,:]=floor
specdb=10*np.log10(spec/floor+1e-9)
# per-column peak (excluding DC): does a signal trace exist and drift?
band=np.abs(f)<60000                                 # APT lives within ~±30kHz; look in ±60k
peak_db=specdb[band,:].max(axis=0)
peak_f=f[band][np.argmax(specdb[band,:],axis=0)]
sig_snr=float(np.percentile(peak_db,92))            # strong-column peak over floor
# drift coherence: a real pass's peak-freq moves smoothly (Doppler S-curve); noise jumps randomly
strong=peak_db>np.percentile(peak_db,80)
drift=float(np.std(np.diff(peak_f[strong]))) if strong.sum()>5 else 9e9
fspan=float(np.percentile(peak_f[strong],90)-np.percentile(peak_f[strong],10)) if strong.sum()>5 else 0.0
img=np.clip((specdb-np.percentile(specdb,5))/(np.percentile(specdb,99.5)-np.percentile(specdb,5)+1e-9)*255,0,255).astype(np.uint8)
Image.fromarray(img).save(PRE+"_waterfall.png")
# discriminate: APT sat = coherent drift (small adjacent-step std) over a few-kHz Doppler SPAN;
# fixed RTL spur = coherent but ~0 span; noise = random (huge drift). The waterfall PNG is the
# ground truth — these metrics are a hint, eyeball the image.
if LRPT:
    verdict="LRPT — narrowband discriminator N/A; authoritative verdict = satdump CADUS (eyeball waterfall for the wide envelope)"
elif sig_snr>8 and drift<2500 and 800<fspan<20000:
    verdict="SIGNAL PRESENT (Doppler-drifting trace — APT!)"
elif sig_snr>8 and drift<2500 and fspan<=800:
    verdict="fixed spur (not a satellite — RTL/LO artifact)"
elif drift>6000:
    verdict="FLAT NOISE (no signal arriving)"
else:
    verdict="faint/ambiguous — inspect waterfall"
print(f"WATERFALL peak_snr={sig_snr:.1f}dB | drift_std={drift:.0f}Hz | doppler_span={fspan:.0f}Hz | {verdict} | -> {PRE}_waterfall.png")

if LRPT:
    print("DECODE skipped (LRPT: APT sync-lock meaningless; see satdump CADUS)")
    sys.exit(0)

# ---------------- DECODE attempt (channelize -> FM demod -> APT) ----------------
# lowpass ±25kHz around DC (carrier is within Doppler ±~4kHz of center), decimate to ~50k
dec=5; fs2=FS//dec                                   # 50 kHz
lp=signal.butter(6, 25000/(FS/2), btype='low', output='sos')
iqf=signal.sosfiltfilt(lp, iq)[::dec]
# FM demod: instantaneous frequency
fm=np.angle(iqf[1:]*np.conj(iqf[:-1]))
# resample FM audio to 11025 for the APT stage
n11=int(len(fm)*11025/fs2); aud=signal.resample(fm, n11)
# --- APT subcarrier AM-demod + sync-lock (same as apt_decode_hq) ---
aud=aud-aud.mean(); SR=11025
bp=signal.butter(4,[900/(SR/2),3800/(SR/2)],btype='band',output='sos')
env=np.abs(signal.hilbert(signal.sosfiltfilt(bp,aud)))
px=signal.resample(env, int(len(env)*PIX/SR)); px=px-px.min()
tmpl=np.tile([-1.,-1.,1.,1.],7)
corr=np.maximum(signal.correlate(px-px.mean(),tmpl,mode='valid'),0)
best=(-1,LINE,0)
for P in range(LINE-6,LINE+7):
    nl=len(corr)//P-1
    if nl<10: continue
    for ph in range(0,P,2):
        idx=(np.arange(nl)*P+ph); idx=idx[idx<len(corr)]
        sc=corr[idx].mean()
        if sc>best[0]: best=(sc,P,ph)
sc,P,ph=best; SYNC=sc/(corr.mean()+1e-9)
nl=(len(px)-ph)//P-1
rows=[px[ph+L*P:ph+L*P+LINE] for L in range(nl) if len(px[ph+L*P:ph+L*P+LINE])==LINE]
if rows:
    im=np.array(rows); A=im[:,86:995]
    lo,hi=np.percentile(A,2),np.percentile(A,98)
    Image.fromarray((np.clip((A-lo)/(hi-lo+1e-9),0,1)*255).astype(np.uint8),'L').save(PRE+"_image.png")
    dv="SIGNAL" if SYNC>3 else ("marginal" if SYNC>1.8 else "noise")
    print(f"DECODE SYNC_LOCK={SYNC:.2f} | lines={nl} | {dv} | -> {PRE}_image.png")
else:
    print(f"DECODE SYNC_LOCK={SYNC:.2f} | no lines")
