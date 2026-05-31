# synthetic carrier with a known Doppler S-curve, for doppler.rail validation
import numpy as np
FS=48000; DUR=5.0; N=int(FS*DUR); t=np.arange(N)/FS
f=3000*np.cos(np.pi*t/DUR)
ph=2*np.pi*np.cumsum(f)/FS
I=np.clip(127.5+120*np.cos(ph),0,255).astype(np.uint8)
Q=np.clip(127.5+120*np.sin(ph),0,255).astype(np.uint8)
iq=np.empty(2*N,np.uint8); iq[0::2]=I; iq[1::2]=Q; iq.tofile('/tmp/dop_test.iq')
W=4096; nw=N//W
np.save('/tmp/dop_truth.npy', np.array([f[int((w+0.5)*W)] for w in range(nw)]))
print("dop synth ok")
