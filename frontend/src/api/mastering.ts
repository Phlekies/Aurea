import { z } from 'zod';
import { audioRequest } from './audio';
import { ApiError } from './client';

const finite = z.number().finite();
const db = finite.nullable();
const presetSchema = z.object({
  id: z.string().regex(/^[a-z][a-z0-9_]{0,63}$/), name: z.string().min(1),
  target_lufs: finite.min(-40).max(-5), max_true_peak_dbtp: finite.min(-8).max(-.1),
  target_lra_lu: finite.min(1).max(50), loudness_tolerance_lu: finite.positive().max(1),
});
const measurements = z.object({
  integrated_lufs: db, momentary_max_lufs: db, short_term_max_lufs: db,
  loudness_range_lu: finite.nonnegative().nullable(), lra_stable: z.boolean(),
  true_peak_dbtp: db, sample_peak_dbfs: db, rms_dbfs: db,
  points: z.array(z.object({ time_seconds: finite.nonnegative(), momentary_lufs: db, short_term_lufs: db })).max(18002),
}).refine((m) => m.points.every((p, index, points) => index === 0 || p.time_seconds > points[index - 1].time_seconds));
export const qcTitles = {
  loudness: 'Loudness objetivo', true_peak: 'Techo de true peak', clipping: 'Sin nueva saturación digital',
  duration: 'Duración conservada', channels: 'Canales conservados', sample_rate: 'Muestreo conservado',
  finite: 'Muestras válidas', not_silent: 'Sin silencio accidental',
};
const qcCode = z.enum(Object.keys(qcTitles) as [keyof typeof qcTitles, ...(keyof typeof qcTitles)[]]);
const reportSchema = z.object({
  audio_id: z.string().regex(/^[a-f0-9]{32}$/), mastering_version: z.literal('0.9.0'),
  source_revision: z.string().regex(/^[a-f0-9]{64}$/), preset: presetSchema,
  sample_rate: z.number().int().min(8000).max(96000), channels: z.number().int().min(1).max(2),
  frames: z.number().int().positive(), duration_seconds: finite.positive(), bit_depth: z.literal(24),
  before: measurements, after: measurements,
  qc: z.object({ passed: z.literal(true), checks: z.array(z.object({
    code: qcCode, passed: z.literal(true), observed: z.union([finite, z.boolean(), z.string(), z.null()]), expected: z.string().min(1),
  })).length(8) }),
  normalization_mode: z.enum(['linear', 'dynamic']), requested_gain_db: finite,
  limiter_ceiling_dbtp: finite, attempts: z.number().int().min(1).max(3), processing_seconds: finite.nonnegative(),
  output_sha256: z.string().regex(/^[a-f0-9]{64}$/),
}).refine((r) => new Set(r.qc.checks.map((c) => c.code)).size === 8
  && Math.abs(r.duration_seconds - r.frames / r.sample_rate) < 1e-9
  && r.after.integrated_lufs !== null && Math.abs(r.after.integrated_lufs - r.preset.target_lufs) <= r.preset.loudness_tolerance_lu
  && r.after.true_peak_dbtp !== null && r.after.true_peak_dbtp <= r.preset.max_true_peak_dbtp + .02
  && r.limiter_ceiling_dbtp <= r.preset.max_true_peak_dbtp
  && [...r.before.points, ...r.after.points].every((p) => p.time_seconds <= r.duration_seconds));

export type MasteringPreset = z.infer<typeof presetSchema>;
export type MasteringReport = z.infer<typeof reportSchema>;
const baseUrl = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '');
const path = (id: string, suffix: string) => `/api/audio/${encodeURIComponent(id)}/${suffix}`;

export function getMasteringPresets(signal?: AbortSignal) {
  return audioRequest('/api/audio/mastering/presets', z.array(presetSchema).min(1), { signal });
}
export function getMastering(id: string, signal?: AbortSignal) {
  return audioRequest(path(id, 'mastering'), reportSchema.refine((r) => r.audio_id === id), { signal });
}
export function masterAudio(id: string, preset: string, signal?: AbortSignal) {
  return audioRequest(path(id, 'master'), reportSchema.refine((r) => r.audio_id === id), {
    method: 'POST', signal, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ preset }),
  }, 1800000);
}
export function masteredStreamUrl(id: string) { return `${baseUrl}${path(id, 'mastered/stream')}`; }

export async function downloadMaster(id: string, signal?: AbortSignal): Promise<Blob> {
  const timeout = AbortSignal.timeout(120000);
  const response = await fetch(`${baseUrl}${path(id, 'mastered/download')}`, { signal: signal ? AbortSignal.any([signal, timeout]) : timeout });
  if (!response.ok) {
    const error = z.object({ message: z.string() }).safeParse(await response.json());
    throw new ApiError(error.success ? error.data.message : 'No se pudo descargar el máster verificado.', response.status);
  }
  if (!response.headers.get('content-type')?.startsWith('audio/wav')) throw new ApiError('El archivo de audio recibido no es válido.');
  return response.blob();
}
