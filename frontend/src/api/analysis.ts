import { z } from 'zod';
import { audioRequest } from './audio';

const decibels = z.number().finite().nullable();
const analysisSchema = z.object({
  audio_id: z.string().regex(/^[a-f0-9]{32}$/),
  analyzer_version: z.string().min(1),
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
  dynamics: z.object({
    window_ms: z.number().positive(),
    points: z.array(z.object({
      start_seconds: z.number().finite().nonnegative(), duration_seconds: z.number().positive(),
      peak_dbfs: decibels, rms_dbfs: decibels,
    })).min(1).max(2000),
  }),
}).refine((value) => value.dc_offset.length === value.channels
  && value.spectrum.frequencies_hz.length === value.spectrum.psd_dbfs_per_hz.length
  && value.spectrum.frequencies_hz.every((frequency, index, values) => frequency <= value.sample_rate / 2 && (index === 0 || frequency > values[index - 1])));

export type AudioAnalysis = z.infer<typeof analysisSchema>;

export function getAnalysis(id: string, signal?: AbortSignal) {
  return audioRequest(`/api/audio/${encodeURIComponent(id)}/analysis`, analysisSchema.refine((value) => value.audio_id === id), { signal });
}

export function analyzeAudio(id: string, signal?: AbortSignal) {
  return audioRequest(`/api/audio/${encodeURIComponent(id)}/analyze`, analysisSchema.refine((value) => value.audio_id === id), { method: 'POST', signal }, 600000);
}
