import type { Diagnostic } from '../api/analysis';
import type { ProcessingPlan, ProcessingReport } from '../api/processing';

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
  diagnostics_version: '0.6.0', diagnostics,
  speech_activity: {
    detector: 'energy', version: '0.5.0', frame_seconds: .03,
    speech_seconds: 3, noise_seconds: 1.5, silence_seconds: .5, speech_percent: 60, speech_rms_dbfs: -18,
    segments: [
      { label: 'silence', start_seconds: 0, end_seconds: .5 },
      { label: 'speech', start_seconds: .5, end_seconds: 2 },
      { label: 'noise', start_seconds: 2, end_seconds: 3.5 },
      { label: 'speech', start_seconds: 3.5, end_seconds: 5 },
    ],
    parameters: { enter_threshold_dbfs: -34, stay_threshold_dbfs: -40, floor_level_dbfs: null },
  },
  noise_profile: {
    frame_count: 50, duration_seconds: 1.5, rms_dbfs: -52, floor_dbfs: -55,
    spectral_flatness: .6, relative_power_std: .12, spectral_stability: .93,
    frequencies_hz: [0, 100, 1000, 10000], psd_dbfs_per_hz: [null, -95, -97, -99],
    low_window_count: 4, low_frequencies_hz: [0, 4, 8], low_psd_dbfs_per_hz: [null, -80, -82],
  },
  estimated_snr_db: 32.4,
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

export const processingPlan: ProcessingPlan = {
  preset: 'corrective', version: '0.8.0',
  steps: [
    { processor: 'dc_removal', enabled: true, parameters: { offsets: [0.02] }, reason: 'Hay desplazamiento de continua.', source_diagnostic: null, confidence: null, evidence: { max_abs_dc_offset: 0.02, threshold: 0.001 } },
    { processor: 'high_pass', enabled: false, parameters: { cutoff_hz: 60, order: 4 }, reason: 'No se ha detectado ruido grave.', source_diagnostic: 'rumble', confidence: 0.6, evidence: { candidate_cutoffs_hz: [60, 70, 80, 100] } },
    { processor: 'dehum', enabled: true, parameters: { fundamental_hz: 50, harmonics: 3, q: 30, attenuation_db: 24 }, reason: 'Se detectó zumbido de 50 Hz.', source_diagnostic: 'hum', confidence: 0.82, evidence: { strongest_line_contrast_db: 18 } },
    { processor: 'pre_gain', enabled: false, parameters: { gain_db: 0 }, reason: 'El nivel de entrada es adecuado.', source_diagnostic: null, confidence: null, evidence: {} },
  ],
};
const metrics = { peak_dbfs: -6, rms_dbfs: -20, integrated_lufs: -18, true_peak_dbtp: -5.8, dc_offset: [0.02], subbass_percent: 12, noise_rms_dbfs: -55, estimated_snr_db: 30, detected: ['hum'] };
export const processingReport: ProcessingReport = {
  artifacts: null,
  gain_envelopes: [],
  audio_id: audioAsset.id, pipeline_version: '0.8.0', plan: processingPlan,
  steps: processingPlan.steps.map((step) => ({ processor: step.processor, enabled: step.enabled, parameters: step.parameters, seconds: 0.012 })),
  sample_rate: 44100, channels: 1, duration_seconds: 5, safety_gain_db: 0, warnings: [],
  processing_seconds: 0.4, real_time_factor: 0.08,
  before: metrics,
  after: { ...metrics, peak_dbfs: -6.4, dc_offset: [0.00001], subbass_percent: 2, detected: [] },
};

export const dynamicsPlan: ProcessingPlan = {
  ...processingPlan,
  steps: [...processingPlan.steps, {
    processor: 'speech_leveler', enabled: true,
    parameters: { target_rms_dbfs: -24, max_boost_db: 8, max_cut_db: 8, window_ms: 400, smoothing_ms: 500, speech_starts_seconds: [.5, 3.5], speech_ends_seconds: [2, 5], noise_floor_dbfs: -55 },
    reason: 'La voz cambia de nivel entre los tramos analizados.', source_diagnostic: null, confidence: null,
    evidence: { speech_available: true, speech_seconds: 3, speech_rms_dbfs: -18, speech_level_spread_db: 10 },
  }, {
    processor: 'compressor', enabled: true,
    parameters: { threshold_dbfs: -18, ratio: 2, knee_db: 6, attack_ms: 10, release_ms: 150, makeup_gain_db: 0 },
    reason: 'Suaviza las diferencias de volumen en la voz.', source_diagnostic: null, confidence: null, evidence: {},
  }],
};
export const dynamicsReport: ProcessingReport = {
  ...processingReport,
  plan: dynamicsPlan,
  steps: dynamicsPlan.steps.map((step) => ({ processor: step.processor, enabled: step.enabled, parameters: step.parameters, seconds: .012 })),
  gain_envelopes: [
    { processor: 'speech_leveler', step_index: 4, times_seconds: [0, 1, 3, 4.99], gain_db: [0, 5, -4, 0] },
    { processor: 'compressor', step_index: 5, times_seconds: [0, 1, 3, 4.99], gain_db: [0, -2, -5, 0] },
  ],
};
