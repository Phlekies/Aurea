import { z } from 'zod';
import { ApiError } from './client';

const assetSchema = z.object({
  id: z.string().regex(/^[a-f0-9]{32}$/), filename: z.string(), format: z.string(),
  codec: z.string(), bitrate: z.number().int().positive().nullable(),
  size_bytes: z.number().int().positive(), sample_rate: z.number().int().positive(),
  channels: z.number().int().min(1).max(2), frames: z.number().int().positive(),
  duration_seconds: z.number().positive(), created_at: z.string(), expires_at: z.string(),
});
const configSchema = z.object({
  formats: z.array(z.string()).min(1), max_upload_bytes: z.number().int().positive(),
  max_duration_seconds: z.number().positive(), retention_seconds: z.number().positive(),
  min_sample_rate: z.number().positive(), max_sample_rate: z.number().positive(),
  max_channels: z.number().int().positive(),
});
const waveformSchema = z.object({
  duration_seconds: z.number().positive(), sample_rate: z.number().int().positive(),
  channels: z.number().int().min(1).max(2),
  peaks: z.array(z.array(z.number().min(0).max(1)).min(1)).min(1).max(2),
}).refine((data) => data.peaks.length === data.channels && data.peaks.every((channel) => channel.length === data.peaks[0].length));

export type AudioAsset = z.infer<typeof assetSchema>;
export type AudioConfig = z.infer<typeof configSchema>;
export type Waveform = z.infer<typeof waveformSchema>;
const baseUrl = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '');

export async function audioRequest<T>(path: string, schema: z.ZodType<T>, options: RequestInit = {}, timeoutMs = 10000): Promise<T> {
  const timeout = AbortSignal.timeout(timeoutMs);
  const response = await fetch(`${baseUrl}${path}`, {
    ...options,
    signal: options.signal ? AbortSignal.any([options.signal, timeout]) : timeout,
  });
  const data: unknown = await response.json();
  if (!response.ok) {
    const errorSchema = z.object({ message: z.string() });
    const direct = errorSchema.safeParse(data);
    const nested = z.object({ detail: errorSchema }).safeParse(data);
    throw new ApiError(direct.success ? direct.data.message : nested.success ? nested.data.detail.message : 'No se pudo completar la operación.', response.status);
  }
  const result = schema.safeParse(data);
  if (!result.success) throw new ApiError('La respuesta de audio no es válida.');
  return result.data;
}

export function getAudioConfig(signal?: AbortSignal) {
  return audioRequest('/api/audio/config', configSchema, { signal });
}

export function getAudio(id: string, signal?: AbortSignal) {
  return audioRequest(`/api/audio/${encodeURIComponent(id)}`, assetSchema, { signal });
}

export function getWaveform(id: string, signal?: AbortSignal, resource = 'waveform') {
  return audioRequest(`/api/audio/${encodeURIComponent(id)}/${resource}`, waveformSchema, { signal });
}

export function uploadAudio(file: File, signal?: AbortSignal) {
  const body = new FormData();
  body.append('file', file);
  return audioRequest('/api/audio', assetSchema, { method: 'POST', body, signal }, 600000);
}

export function streamUrl(id: string) {
  return `${baseUrl}/api/audio/${encodeURIComponent(id)}/stream`;
}
