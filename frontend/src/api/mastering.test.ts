import { afterEach, expect, it, vi } from 'vitest';
import { getMastering } from './mastering';
import { audioAsset, jsonResponse } from '../test/fixtures';
import { masteringReport } from '../test/mastering-fixtures';

afterEach(() => vi.unstubAllGlobals());

it.each([
  { ...masteringReport, qc: { passed: true, checks: Array(8).fill(masteringReport.qc.checks[0]) } },
  { ...masteringReport, after: { ...masteringReport.after, true_peak_dbtp: 0 } },
  { ...masteringReport, after: { ...masteringReport.after, integrated_lufs: -20 } },
  { ...masteringReport, audio_id: 'd'.repeat(32) },
])('rejects incomplete QC, out-of-target levels or a different recording', async (report) => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(report)));
  await expect(getMastering(audioAsset.id)).rejects.toThrow();
});
