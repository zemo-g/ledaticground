export const meta = {
  name: 'apt-recover',
  description: 'Fan out independent APT image-recovery strategies on a marginal recording, pick the best by sync-lock',
  phases: [
    { title: 'Recover', detail: 'one agent per recovery strategy, each maximizes SYNC_LOCK' },
    { title: 'Select', detail: 'rank by sync-lock + image structure, pick the winner' },
  ],
}

// args = { recording: "/tmp/cap_...s16", sr: 11025 }
const REC = (args && args.recording) || '/tmp/cap_gain25.s16'
const SR = (args && args.sr) || 11025
const PY = '/opt/homebrew/bin/python3.11'
const GD = '/Users/ledaticempire/projects/ledaticground'

const CTX = `Recording: ${REC} (mono int16, ${SR} Hz, FM-demodulated audio from a NOAA APT pass that
was degraded by FM-broadcast front-end overload). APT facts: 2400 Hz AM subcarrier, 4160 px/s word
rate, 2080 px/line (2 lines/s), Sync-A = 7 pulses of 1040 Hz at line start, channel-A image = px
86..994. A validated baseline decoder is at ${GD}/scripts/apt_decode_hq.py — run it for reference:
  ${PY} ${GD}/scripts/apt_decode_hq.py ${REC} /tmp/ref.png
It prints "SYNC_LOCK <q>": q = (mean Sync-A correlation on the locked line grid) / (global mean corr).
q~1 = pure noise; q>3 = real image structure. Your job: BEAT the baseline's SYNC_LOCK and produce the
most image-like result. Use ${PY} (numpy/scipy/PIL available). Write your own python, iterate, and
SAVE a grayscale PNG. Report honestly — if it's noise, say so (do not fabricate an image).`

const SCHEMA = {
  type: 'object',
  required: ['strategy','sync_lock','produced_image','png','notes'],
  properties: {
    strategy: { type: 'string' },
    sync_lock: { type: 'number', description: 'best SYNC_LOCK achieved' },
    lines: { type: 'number' },
    produced_image: { type: 'boolean', description: 'true only if a real APT image is visible, not noise' },
    png: { type: 'string', description: 'absolute path to the output PNG' },
    notes: { type: 'string', description: 'what you tried, what worked, honest verdict' },
  },
}

const STRATEGIES = [
  { key: 'baseline-sweep', detail: 'Run apt_decode_hq.py with and without --denoise and a sweep of subcarrier bandpass widths (±1000..±2000 Hz). Report the best.' },
  { key: 'spectral-subtraction', detail: 'Estimate the noise spectrum from quiet segments and do spectral subtraction on the audio before AM-demod, then decode. Tune the over-subtraction factor.' },
  { key: 'wiener-adaptive', detail: 'Apply a Wiener / adaptive noise filter to the 2400Hz-subcarrier envelope before sync, then decode. Tune the noise-power estimate.' },
  { key: 'pll-sync', detail: 'Replace the fixed line-grid search with a per-line PLL that tracks line-period drift with sub-sample interpolation. This recovers lock where a fixed grid smears.' },
  { key: 'image-denoise', detail: 'Decode with the baseline, then aggressively denoise the ASSEMBLED image (non-local-means / bilateral / wavelet) to pull faint structure out of the noise. Keep sync honest.' },
  { key: 'narrowband-demod', detail: 'Tightly track the 2400Hz subcarrier (narrow PLL/notch-tracking AM demod) to reject out-of-band noise the wide bandpass lets through, then decode.' },
  { key: 'aptdec-source', detail: 'Convert the recording to a 11025 Hz WAV (ffmpeg), then build aptdec from source (github.com/Xerbo/aptdec or csete/aptdec; deps via brew: libsndfile, libpng) and run it. If build fails, say so and fall back to the baseline.' },
  { key: 'noaa-apt-bin', detail: 'Convert to WAV (ffmpeg), then get noaa-apt (github.com/martinber/noaa-apt) running — prebuilt or cargo build — and decode. If unavailable, say so and fall back to the baseline.' },
]

phase('Recover')
const results = await parallel(STRATEGIES.map((s, i) => () =>
  agent(
    `${CTX}\n\nSTRATEGY (${s.key}): ${s.detail}\nSave your PNG to /tmp/apt_recover_${i}_${s.key}.png and report its SYNC_LOCK.`,
    { label: `recover:${s.key}`, phase: 'Recover', schema: SCHEMA }
  )
)).then(rs => rs.filter(Boolean))

log(`recovery agents done: ${results.length}/${STRATEGIES.length} reported`)
const ranked = results.slice().sort((a,b) => (b.sync_lock||0) - (a.sync_lock||0))
const winners = ranked.filter(r => r.produced_image)

phase('Select')
let verdict
if (winners.length === 0) {
  verdict = {
    outcome: 'NO_IMAGE',
    best_sync_lock: ranked[0]?.sync_lock ?? 0,
    summary: 'No strategy recovered a real image — the signal is below the noise in this recording (gain-25 alone insufficient; FM filter needed).',
    ranked,
  }
} else {
  // independent visual confirmation of the top candidate (guard against a high sync-lock on noise)
  const top = winners[0]
  const check = await agent(
    `Open and inspect this PNG: ${top.png}. Does it show genuine NOAA APT weather-satellite imagery — recognizable cloud/coastline bands, telemetry wedges, two side-by-side channels — or is it noise/artifacts? Be skeptical; a high sync-lock can occur on structured noise.`,
    { label: 'select:visual-confirm', phase: 'Select',
      schema: { type:'object', required:['is_real_image','confidence','what_you_see'],
        properties: { is_real_image:{type:'boolean'}, confidence:{type:'string'}, what_you_see:{type:'string'} } } }
  )
  verdict = { outcome: check?.is_real_image ? 'IMAGE_RECOVERED' : 'INCONCLUSIVE',
    winner: top, visual: check, ranked }
}
return verdict
