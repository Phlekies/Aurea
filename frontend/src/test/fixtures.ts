import type { Diagnostic } from '../api/analysis';

export const audioConfig = {
  formats: ['wav', 'flac', 'mp3', 'm4a', 'ogg'], max_upload_bytes: 104857600,
  max_duration_seconds: 1800, retention_seconds: 86400,
  min_sample_rate: 8000, max_sample_rate: 96000, max_channels: 2,
};

export const audioAsset = {
  id: 'a'.repeat(32), filename: 'episode.wav', format: 'wav', codec: 'pcm_s16le',
  bitrate: 705600, size_bytes: 441044, sample_rate: 44100, channels: 1,
  frames: 220500, duration_seconds: 5,
  created_at: '2026-09-30T12:00:00Z', expires_at: '2026-10-01T12:00:00Z',
};

export const waveform = { duration_seconds: 5, sample_rate: 44100, channels: 1, peaks: [[0.1, 0.2, 0.4, 0.2]] };
export const diagnostics: Diagnostic[] = (['clipping', 'hum', 'rumble', 'low_level', 'low_headroom', 'stationary_noise', 'sibilance', 'plosives'] as const).map((code) => ({
  code, detected: false, severity: 0, confidence: .8,
  message: 'No se han observado indicios suficientes en esta comprobación.',
  evidence: {}, parameters: {},
}));
export const analysis = {
  audio_id: audioAsset.id, analyzer_version: '0.3.0', sample_rate: 44100, channels: 1,
  diagnostics_version: '0.4.0', diagnostics,
  duration_seconds: 5, peak_dbfs: -6, rms_dbfs: -9, crest_factor_db: 3,
  integrated_lufs: -10, true_peak_dbtp: -5.8, dc_offset: [0], zero_crossing_rate: .02,
  silence_percent: 0, silence_threshold_dbfs: -60,
  bands: [{ name: 'Medios', low_hz: 250, high_hz: 2000, power: .125, percent: 100 }],
  spectrum: { frequencies_hz: [0, 440, 22050], psd_dbfs_per_hz: [null, -20, null] },
  dynamics: { window_ms: 100, points: [{ start_seconds: 0, duration_seconds: .1, peak_dbfs: -6, rms_dbfs: -9 }] },
};
export function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
}
