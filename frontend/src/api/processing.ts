import { z } from 'zod';
import { audioRequest, getWaveform, type Waveform } from './audio';

const value = z.union([z.boolean(), z.number().finite(), z.string(), z.array(z.number().finite())]);
const decibels = z.number().finite().nullable();

const stepSchema = z.object({
  processor: z.string().min(1),
  enabled: z.boolean(),
  parameters: z.record(z.string(), value),
  reason: z.string().min(1),
  source_diagnostic: z.string().nullable(),
  confidence: z.number().finite().min(0).max(1).nullable(),
  evidence: z.record(z.string(), value),
});
const planSchema = z.object({
  preset: z.string().min(1), version: z.string().min(1), steps: z.array(stepSchema).max(32),
});
const metricsSchema = z.object({
  peak_dbfs: decibels, rms_dbfs: decibels, integrated_lufs: decibels, true_peak_dbtp: decibels,
  dc_offset: z.array(z.number().finite()).min(1).max(2),
  subbass_percent: z.number().finite().min(0).max(100),
  noise_rms_dbfs: decibels, estimated_snr_db: decibels,
  detected: z.array(z.string()),
});
const gainEnvelopeSchema = z.object({
  processor: z.string().min(1), step_index: z.number().int().nonnegative(),
  times_seconds: z.array(z.number().finite().nonnegative()).min(1).max(18002),
  gain_db: z.array(z.number().finite()).min(1).max(18002),
}).refine((curve) => curve.times_seconds.length === curve.gain_db.length
  && curve.times_seconds[0] === 0
  && curve.times_seconds.every((time, index, times) => index === 0 || time > times[index - 1]));
const reportSchema = z.object({
  audio_id: z.string().regex(/^[a-f0-9]{32}$/),
  pipeline_version: z.literal('0.9.0'),
  plan: planSchema,
  steps: z.array(z.object({
    processor: z.string().min(1), enabled: z.boolean(),
    parameters: z.record(z.string(), value), seconds: z.number().finite().nonnegative(),
  })),
  sample_rate: z.number().int().positive(), channels: z.number().int().min(1).max(2),
  duration_seconds: z.number().positive(),
  safety_gain_db: z.number().finite().max(0),
  warnings: z.array(z.string()),
  processing_seconds: z.number().finite().nonnegative(),
  real_time_factor: z.number().finite().nonnegative(),
  before: metricsSchema, after: metricsSchema,
  gain_envelopes: z.array(gainEnvelopeSchema).max(32),
  artifacts: z.object({
    total_reduction_db: decibels, speech_energy_loss_db: decibels,
    background_reduction_db: decibels,
    musical_noise_score: z.number().finite().min(0).max(1).nullable(),
    excessive_reduction: z.boolean(), significant_speech_loss: z.boolean(), possible_musical_noise: z.boolean(),
  }).nullable(),
}).refine((report) => report.steps.length === report.plan.steps.length
  && report.before.dc_offset.length === report.channels && report.after.dc_offset.length === report.channels
  && new Set(report.gain_envelopes.map((curve) => curve.step_index)).size === report.gain_envelopes.length
  && report.steps.every((step, index) => !step.enabled || !['speech_leveler', 'compressor'].includes(step.processor)
    || report.gain_envelopes.some((curve) => curve.step_index === index))
  && report.gain_envelopes.every((curve) => {
    const step = report.steps[curve.step_index];
    const planned = report.plan.steps[curve.step_index];
    return step?.enabled && planned?.enabled && step.processor === curve.processor
      && planned.processor === curve.processor && curve.times_seconds.every((time) => time < report.duration_seconds);
  }));

export type ProcessingStep = z.infer<typeof stepSchema>;
export type ProcessingPlan = z.infer<typeof planSchema>;
export type ProcessingMetrics = z.infer<typeof metricsSchema>;
export type ProcessingReport = z.infer<typeof reportSchema>;

const baseUrl = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '');
const path = (id: string, suffix: string) => `/api/audio/${encodeURIComponent(id)}/${suffix}`;

export function getProcessingPlan(id: string, signal?: AbortSignal) {
  return audioRequest(path(id, 'processing/plan'), planSchema, { signal });
}

export function getProcessing(id: string, signal?: AbortSignal) {
  return audioRequest(path(id, 'processing'), reportSchema.refine((report) => report.audio_id === id), { signal });
}

export function processAudio(id: string, plan: ProcessingPlan, signal?: AbortSignal) {
  return audioRequest(path(id, 'process'), reportSchema.refine((report) => report.audio_id === id), {
    method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ plan }),
  }, 600000);
}

export function getProcessedWaveform(id: string, signal?: AbortSignal): Promise<Waveform> {
  return getWaveform(id, signal, 'processed/waveform');
}

export function processedStreamUrl(id: string) {
  return `${baseUrl}${path(id, 'processed/stream')}`;
}
