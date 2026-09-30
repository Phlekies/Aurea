import { z } from 'zod';
import { audioRequest } from './audio';

const decibels = z.number().finite().nullable();
const diagnosticCode = z.enum(['clipping', 'hum', 'rumble', 'low_level', 'low_headroom', 'stationary_noise', 'sibilance', 'plosives']);
const diagnosticValue = z.union([
  z.string(), z.number().finite(), z.boolean(), z.null(),
  z.array(z.string()), z.array(z.number().finite()),
]);
const diagnosticSchema = z.object({
  code: diagnosticCode,
  detected: z.boolean(),
  severity: z.number().finite().min(0).max(1),
  confidence: z.number().finite().min(0).max(1),
  message: z.string().min(1),
  evidence: z.record(z.string(), diagnosticValue),
  parameters: z.record(z.string(), diagnosticValue),
});
const activitySegmentSchema = z.object({
  label: z.enum(['speech', 'noise', 'silence']),
  start_seconds: z.number().finite().nonnegative(),
  end_seconds: z.number().finite().positive(),
}).refine((segment) => segment.end_seconds > segment.start_seconds);
const speechActivitySchema = z.object({
  detector: z.string().min(1), version: z.literal('0.5.0'),
  frame_seconds: z.number().finite().positive(),
  speech_seconds: z.number().finite().nonnegative(),
  noise_seconds: z.number().finite().nonnegative(),
  silence_seconds: z.number().finite().nonnegative(),
  speech_percent: z.number().finite().min(0).max(100),
  speech_rms_dbfs: decibels,
  segments: z.array(activitySegmentSchema).min(1).refine((segments) => segments.every((segment, index) => index === 0
    || (segment.label !== segments[index - 1].label && Math.abs(segment.start_seconds - segments[index - 1].end_seconds) < 1e-6))),
  parameters: z.record(z.string(), decibels),
});
const noiseProfileSchema = z.object({
  frame_count: z.number().int().nonnegative(),
  duration_seconds: z.number().finite().nonnegative(),
  rms_dbfs: decibels, floor_dbfs: decibels,
  spectral_flatness: z.number().finite().min(0).max(1).nullable(),
  relative_power_std: z.number().finite().nonnegative().nullable(),
  spectral_stability: z.number().finite().min(0).max(1).nullable(),
  frequencies_hz: z.array(z.number().finite().nonnegative()).max(4097),
  psd_dbfs_per_hz: z.array(decibels).max(4097),
}).refine((profile) => profile.frequencies_hz.length === profile.psd_dbfs_per_hz.length);
const analysisSchema = z.object({
  audio_id: z.string().regex(/^[a-f0-9]{32}$/),
  analyzer_version: z.string().min(1),
  diagnostics_version: z.literal('0.5.0'),
  diagnostics: z.array(diagnosticSchema).length(8).refine((items) => new Set(items.map((item) => item.code)).size === 8),
  sample_rate: z.number().int().positive(), channels: z.number().int().min(1).max(2),
  duration_seconds: z.number().positive(),
  peak_dbfs: decibels, rms_dbfs: decibels, crest_factor_db: decibels,
  integrated_lufs: decibels, true_peak_dbtp: decibels,
  dc_offset: z.array(z.number().finite().min(-1).max(1)).min(1).max(2),
  zero_crossing_rate: z.number().finite().min(0).max(1),
  silence_percent: z.number().finite().min(0).max(100),
  silence_threshold_dbfs: z.number().finite(),
  bands: z.array(z.object({
    name: z.string(), low_hz: z.number().nonnegative(), high_hz: z.number().positive(),
    power: z.number().finite().nonnegative(), percent: z.number().finite().min(0).max(100),
  })).max(32),
  spectrum: z.object({
    frequencies_hz: z.array(z.number().finite().nonnegative()).min(1).max(4097),
    psd_dbfs_per_hz: z.array(decibels).min(1).max(4097),
  }),
  speech_activity: speechActivitySchema,
  noise_profile: noiseProfileSchema,
  estimated_snr_db: decibels,
  dynamics: z.object({
    window_ms: z.number().positive(),
    points: z.array(z.object({
      start_seconds: z.number().finite().nonnegative(), duration_seconds: z.number().positive(),
      peak_dbfs: decibels, rms_dbfs: decibels,
    })).min(1).max(2000),
  }),
}).refine((value) => value.dc_offset.length === value.channels
  && Math.abs(value.speech_activity.segments[0].start_seconds) < 1e-6
  && Math.abs(value.speech_activity.segments[value.speech_activity.segments.length - 1].end_seconds - value.duration_seconds) < 1e-6
  && value.spectrum.frequencies_hz.length === value.spectrum.psd_dbfs_per_hz.length
  && value.spectrum.frequencies_hz.every((frequency, index, values) => frequency <= value.sample_rate / 2 && (index === 0 || frequency > values[index - 1])));

export type AudioAnalysis = z.infer<typeof analysisSchema>;
export type Diagnostic = z.infer<typeof diagnosticSchema>;
export type SpeechActivity = z.infer<typeof speechActivitySchema>;
export type NoiseProfile = z.infer<typeof noiseProfileSchema>;

export function getAnalysis(id: string, signal?: AbortSignal) {
  return audioRequest(`/api/audio/${encodeURIComponent(id)}/analysis`, analysisSchema.refine((value) => value.audio_id === id), { signal });
}

export function analyzeAudio(id: string, signal?: AbortSignal) {
  return audioRequest(`/api/audio/${encodeURIComponent(id)}/analyze`, analysisSchema.refine((value) => value.audio_id === id), { method: 'POST', signal }, 600000);
}
