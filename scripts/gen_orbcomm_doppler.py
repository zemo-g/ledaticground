#!/usr/bin/env python3
# Synthetic Orbcomm Doppler-drift reference vector for src/doppler_real.rail (IS-4/IS-5
# cross-check, the SYNTHETIC-VALIDATION half). Builds a single carrier whose frequency
# follows an SGP4-SHAPED Doppler S-curve through TCA (the characteristic downward sweep
# a genuine LEO carrier exhibits), oversampled into the 16384-sample / FS=60000 window
# format that doppler_real.rail reads from /tmp/dop_real.iq (uint8 cu8, DC spike present
# so the DC-skip path is exercised). The per-window TRUE centroid frequency is written to
# /tmp/dopcap_synth/dop_truth.npy so scripts/doppler_fit.py --synth can correlate the
# measured centroid track against it.
#
# THE FINGERPRINT THESIS: Orbcomm has NO satdump reference pipeline, so the independent
# proof-of-reception cross-check is the DOPPLER FINGERPRINT — physics that cannot be
# faked. A genuine LEO carrier follows the SGP4-predicted drift curve; matching the
# measured drift to the prediction PROVES the node heard THAT satellite on THAT pass.
# This generator is the OFFLINE stand-in for a real pass: it lets us prove doppler_real
# tracks a drifting carrier and doppler_fit correlates it, with no live RF.
#
#   gen_orbcomm_doppler.py [--windows N] [--span-hz S] [--snr DB]
#
# --windows : number of 16384-sample FFT windows (default 24, ~6.5 s at 60k -> a short
#             pass segment around TCA; doppler_real emits one DOP line per window).
# --span-hz : total Doppler sweep across the pass (default 16000 -> +8k..-8k through TCA;
#             representative LEO @137 MHz: ~+/-3 kHz, but a wider span makes the drift
#             unambiguous for the offline track test and stays inside +/-30 kHz @ 60k).
# --snr     : per-window SNR (dB) for the synthetic carrier (default 18).
import os, sys, numpy as np

N = 16384
FS = 60000.0


def argi(flag, d):
    return int(sys.argv[sys.argv.index(flag) + 1]) if flag in sys.argv else d


def argf(flag, d):
    return float(sys.argv[sys.argv.index(flag) + 1]) if flag in sys.argv else d


def main():
    nw = argi('--windows', 24)
    span = argf('--span-hz', 16000.0)
    snr_db = argf('--snr', 18.0)
    rng = np.random.default_rng(137)

    os.makedirs('/tmp/dopcap_synth', exist_ok=True)

    # SGP4-shaped Doppler S-curve: f(t) = -A * tanh((t - tca)/tau). Monotonic decreasing
    # through TCA (the canonical descending sweep), steepest at closest approach.
    tw = np.arange(nw, dtype=np.float64)
    tca = (nw - 1) / 2.0
    tau = nw / 6.0
    f_centre = -(span / 2.0) * np.tanh((tw - tca) / tau)   # Hz, per-window carrier centre

    sigma = (1.0 / np.sqrt(2)) / (10 ** (snr_db / 20.0))
    i_all = np.empty(0, np.int8)
    q_all = np.empty(0, np.int8)
    truth = []
    phase = 0.0
    for w in range(nw):
        fc = float(f_centre[w])
        n = np.arange(N, dtype=np.float64)
        # continuous phase across windows so the carrier is coherent (a real carrier is)
        ph = phase + 2 * np.pi * (fc / FS) * n
        phase = ph[-1] + 2 * np.pi * (fc / FS)
        z = np.exp(1j * ph)
        z = z + (rng.standard_normal(N) + 1j * rng.standard_normal(N)) * sigma
        A = 60.0
        i8 = np.clip(np.round(z.real * A) + 0, -127, 127).astype(np.int8)
        q8 = np.clip(np.round(z.imag * A) + 0, -127, 127).astype(np.int8)
        # to cu8 (uint8, bias 128) — doppler_real reads uint8 then DC-skips
        i_all = np.concatenate([i_all, i8])
        q_all = np.concatenate([q_all, q8])
        truth.append(fc)

    # interleave to cu8 (uint8 = int8 + 128)
    iq = np.empty(2 * len(i_all), np.uint8)
    iq[0::2] = (i_all.astype(np.int16) + 128).astype(np.uint8)
    iq[1::2] = (q_all.astype(np.int16) + 128).astype(np.uint8)
    iq.tofile('/tmp/dop_real.iq')
    np.save('/tmp/dopcap_synth/dop_truth.npy', np.array(truth, np.float64))

    print(f"gen_orbcomm_doppler: windows={nw} span={span:.0f}Hz snr={snr_db}dB "
          f"f_centre[0]={truth[0]:.0f} f_centre[tca]~0 f_centre[-1]={truth[-1]:.0f} "
          f"-> /tmp/dop_real.iq ({len(iq)} bytes), truth -> /tmp/dopcap_synth/dop_truth.npy")
    print("NOTE: synthetic drifting carrier — NO payload, NO real RF. This is the offline "
          "fingerprint-track validation vector, not a decode.")


if __name__ == "__main__":
    main()
