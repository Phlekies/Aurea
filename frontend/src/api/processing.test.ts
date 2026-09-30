import { afterEach, expect, it, vi } from 'vitest';
import { audioAsset, jsonResponse, processingPlan, processingReport } from '../test/fixtures';
import { getProcessing, getProcessingPlan, processAudio, processedStreamUrl } from './processing';

afterEach(() => vi.unstubAllGlobals());

it('reads the plan and posts it back as JSON', async () => {
  const fetch = vi.fn().mockResolvedValueOnce(jsonResponse(processingPlan)).mockResolvedValueOnce(jsonResponse(processingReport));
  vi.stubGlobal('fetch', fetch);
  const plan = await getProcessingPlan(audioAsset.id);
  await expect(processAudio(audioAsset.id, plan)).resolves.toEqual(processingReport);
  const [url, options] = fetch.mock.calls[1] as [string, RequestInit];
  expect(url).toBe(`/api/audio/${audioAsset.id}/process`);
  expect(JSON.parse(String(options.body))).toEqual({ plan: processingPlan });
  expect(processedStreamUrl(audioAsset.id)).toBe(`/api/audio/${audioAsset.id}/processed/stream`);
});

it.each([
  ['another recording', { ...processingReport, audio_id: 'f'.repeat(32) }],
  ['an older pipeline', { ...processingReport, pipeline_version: '0.5.0' }],
  ['a positive safety gain', { ...processingReport, safety_gain_db: 2 }],
  ['steps that do not match the plan', { ...processingReport, steps: processingReport.steps.slice(1) }],
  ['DC offsets for the wrong channel count', { ...processingReport, after: { ...processingReport.after, dc_offset: [0, 0] } }],
  ['a numeric-string duration', { ...processingReport, steps: processingReport.steps.map((step, index) => index ? step : { ...step, seconds: '0.1' }) }],
])('rejects a report with %s', async (_label, report) => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(report)));
  await expect(getProcessing(audioAsset.id)).rejects.toMatchObject({ name: 'ApiError' });
});
