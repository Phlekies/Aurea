import { afterEach, expect, it, vi } from 'vitest';
import { audioAsset, dynamicsReport, jsonResponse, processingPlan, processingReport } from '../test/fixtures';
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

it('retains applied gain curves from the current pipeline', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(dynamicsReport)));
  await expect(getProcessing(audioAsset.id)).resolves.toEqual(dynamicsReport);
});

const levelerCurve = dynamicsReport.gain_envelopes[0];
const withCurve = (curve: unknown) => ({ ...dynamicsReport, gain_envelopes: [curve, dynamicsReport.gain_envelopes[1]] });
it.each([
  ['unequal curve lengths', withCurve({ ...levelerCurve, gain_db: [0] })],
  ['an empty curve', withCurve({ ...levelerCurve, times_seconds: [], gain_db: [] })],
  ['a curve that starts late', withCurve({ ...levelerCurve, times_seconds: [.1, 1, 3, 4.99] })],
  ['unordered curve times', withCurve({ ...levelerCurve, times_seconds: [0, 3, 1, 4.99] })],
  ['repeated curve times', withCurve({ ...levelerCurve, times_seconds: [0, 1, 1, 4.99] })],
  ['negative curve times', withCurve({ ...levelerCurve, times_seconds: [-1, 1, 3, 4.99] })],
  ['a curve outside the recording', withCurve({ ...levelerCurve, times_seconds: [0, 1, 3, 5] })],
  ['a nonfinite gain', withCurve({ ...levelerCurve, gain_db: [0, Infinity, 0, 0] })],
  ['a curve for the wrong processor', withCurve({ ...levelerCurve, processor: 'compressor' })],
  ['a curve for a disabled step', withCurve({ ...levelerCurve, step_index: 1, processor: 'high_pass' })],
  ['a nonexistent step index', withCurve({ ...levelerCurve, step_index: 32 })],
  ['duplicate step curves', { ...dynamicsReport, gain_envelopes: [...dynamicsReport.gain_envelopes, levelerCurve] }],
  ['missing enabled dynamics curves', { ...dynamicsReport, gain_envelopes: [] }],
  ['an oversized curve', withCurve({ ...levelerCurve, times_seconds: Array.from({ length: 18003 }, (_, index) => index / 10000), gain_db: Array(18003).fill(0) })],
])('rejects a report with %s', async (_label, report) => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(report)));
  await expect(getProcessing(audioAsset.id)).rejects.toMatchObject({ name: 'ApiError' });
});
