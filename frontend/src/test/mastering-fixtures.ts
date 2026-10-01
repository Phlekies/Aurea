import type { MasteringPreset, MasteringReport } from '../api/mastering';
import { audioAsset } from './fixtures';

export const masteringPresets: MasteringPreset[] = [
  { id: 'podcast_standard', name: 'Podcast Standard', target_lufs: -16, max_true_peak_dbtp: -1, target_lra_lu: 11, loudness_tolerance_lu: .5 },
  { id: 'broadcast_r128', name: 'Broadcast R128', target_lufs: -23, max_true_peak_dbtp: -1, target_lra_lu: 11, loudness_tolerance_lu: .5 },
];
const metrics = {
  integrated_lufs: -23, momentary_max_lufs: -20, short_term_max_lufs: -22, loudness_range_lu: 2,
  lra_stable: false, true_peak_dbtp: -12, sample_peak_dbfs: -12.1, rms_dbfs: -24,
  points: [
    { time_seconds: .1, momentary_lufs: null, short_term_lufs: null },
    { time_seconds: .4, momentary_lufs: -23, short_term_lufs: null },
    { time_seconds: 3, momentary_lufs: -20, short_term_lufs: -22 },
  ],
};
export const masteringReport: MasteringReport = {
  audio_id: audioAsset.id, mastering_version: '0.9.0', source_revision: 'b'.repeat(64),
  preset: masteringPresets[0], sample_rate: 44100, channels: 1, frames: 220500, duration_seconds: 5, bit_depth: 24,
  before: metrics, after: { ...metrics, integrated_lufs: -16, true_peak_dbtp: -1.3, momentary_max_lufs: -12, short_term_max_lufs: -15,
    points: metrics.points.map((p) => ({ ...p, momentary_lufs: p.momentary_lufs === null ? null : p.momentary_lufs + 7, short_term_lufs: p.short_term_lufs === null ? null : p.short_term_lufs + 7 })) },
  qc: { passed: true, checks: [
    { code: 'loudness', passed: true, observed: -16, expected: '-16 LUFS ±0,5 LU' },
    { code: 'true_peak', passed: true, observed: -1.3, expected: '≤-1 dBTP' },
    { code: 'clipping', passed: true, observed: 0, expected: '0 muestras saturadas' },
    { code: 'duration', passed: true, observed: 220500, expected: '220500 frames' },
    { code: 'channels', passed: true, observed: 1, expected: '1 canal' },
    { code: 'sample_rate', passed: true, observed: 44100, expected: '44100 Hz' },
    { code: 'finite', passed: true, observed: true, expected: 'Todas las muestras finitas' },
    { code: 'not_silent', passed: true, observed: -17, expected: 'Señal audible' },
  ] },
  normalization_mode: 'dynamic', requested_gain_db: 7, limiter_ceiling_dbtp: -1.3, attempts: 1, processing_seconds: .5,
  output_sha256: 'c'.repeat(64),
};
