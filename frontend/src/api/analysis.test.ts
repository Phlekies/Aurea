import { afterEach, expect, it, vi } from 'vitest';
import { analyzeAudio, getAnalysis } from './analysis';
import { analysis, audioAsset, jsonResponse } from '../test/fixtures';

afterEach(() => vi.unstubAllGlobals());

it('requests analysis explicitly and accepts undefined measurements as null', async () => {
  const silent = { ...analysis, peak_dbfs: null, rms_dbfs: null, integrated_lufs: null, true_peak_dbtp: null, crest_factor_db: null, silence_percent: 100 };
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(silent)));
  await expect(analyzeAudio(audioAsset.id)).resolves.toEqual(silent);
  expect(fetch).toHaveBeenCalledWith(`/api/audio/${audioAsset.id}/analyze`, expect.objectContaining({ method: 'POST' }));
});

it('rejects reports for another recording and mismatched spectral arrays', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(jsonResponse({ ...analysis, audio_id: 'b'.repeat(32) })).mockResolvedValueOnce(jsonResponse({ ...analysis, spectrum: { frequencies_hz: [0, 1], psd_dbfs_per_hz: [0] } })));
  await expect(getAnalysis(audioAsset.id)).rejects.toMatchObject({ name: 'ApiError' });
  await expect(getAnalysis(audioAsset.id)).rejects.toMatchObject({ name: 'ApiError' });
});

it('rejects numeric strings and non-finite values before graph rendering', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(jsonResponse({ ...analysis, peak_dbfs: '-inf' })).mockResolvedValueOnce(jsonResponse({ ...analysis, zero_crossing_rate: 2 })));
  await expect(getAnalysis(audioAsset.id)).rejects.toMatchObject({ name: 'ApiError' });
  await expect(getAnalysis(audioAsset.id)).rejects.toMatchObject({ name: 'ApiError' });
});

it.each([
  ['an older diagnosis version', { ...analysis, diagnostics_version: '0.3.0' }],
  ['a report without speech activity', { ...analysis, speech_activity: undefined }],
  ['an older activity version', { ...analysis, speech_activity: { ...analysis.speech_activity, version: '0.4.0' } }],
  ['a timeline gap', { ...analysis, speech_activity: { ...analysis.speech_activity, segments: [
    { label: 'speech', start_seconds: 0, end_seconds: 2 }, { label: 'noise', start_seconds: 2.5, end_seconds: 5 }] } }],
  ['unmerged timeline segments', { ...analysis, speech_activity: { ...analysis.speech_activity, segments: [
    { label: 'noise', start_seconds: 0, end_seconds: 2 }, { label: 'noise', start_seconds: 2, end_seconds: 5 }] } }],
  ['a timeline shorter than the recording', { ...analysis, speech_activity: { ...analysis.speech_activity, segments: [
    { label: 'noise', start_seconds: 0, end_seconds: 4 }] } }],
  ['an unknown activity label', { ...analysis, speech_activity: { ...analysis.speech_activity, segments: [
    { label: 'music', start_seconds: 0, end_seconds: 5 }] } }],
  ['a noise spectrum length mismatch', { ...analysis, noise_profile: { ...analysis.noise_profile, psd_dbfs_per_hz: [-90] } }],
  ['a numeric-string SNR', { ...analysis, estimated_snr_db: '32.4' }],
  ['a report without diagnoses', { ...analysis, diagnostics: undefined }],
  ['an incomplete set of checks', { ...analysis, diagnostics: analysis.diagnostics.slice(1) }],
  ['duplicate detector codes', { ...analysis, diagnostics: [...analysis.diagnostics.slice(0, 7), analysis.diagnostics[0]] }],
  ['an unknown detector', { ...analysis, diagnostics: analysis.diagnostics.map((item, index) => index === 0 ? { ...item, code: 'unknown' } : item) }],
])('rejects %s', async (_label, report) => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(report)));
  await expect(getAnalysis(audioAsset.id)).rejects.toMatchObject({ name: 'ApiError' });
});

it.each([
  { severity: -0.1 }, { severity: 1.1 }, { confidence: -0.1 }, { confidence: 1.1 },
  { confidence: Infinity }, { severity: NaN }, { detected: 'true' },
  { evidence: { nested: { invalid: true } } }, { parameters: { invalid: [1, null] } },
])('rejects invalid diagnostic values: %j', async (change) => {
  const report = { ...analysis, diagnostics: analysis.diagnostics.map((item, index) => index === 0 ? { ...item, ...change } : item) };
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(report)));
  await expect(getAnalysis(audioAsset.id)).rejects.toMatchObject({ name: 'ApiError' });
});

it('preserves detector evidence and parameters in the report', async () => {
  const report = { ...analysis, diagnostics: analysis.diagnostics.map((item, index) => index === 0 ? {
    ...item, detected: true, severity: .9, confidence: .8,
    evidence: { max_hard_run_samples: 8, note: 'Valores observados', per_channel: [4, 8], channels: ['L', 'R'], value: null, available: true },
    parameters: { threshold: .999 },
  } : item) };
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(report)));
  await expect(getAnalysis(audioAsset.id)).resolves.toEqual(report);
});
