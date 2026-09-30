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
