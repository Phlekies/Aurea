import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { analysis, audioAsset, jsonResponse } from '../../test/fixtures';
import { AnalysisPanel } from './AnalysisPanel';

afterEach(() => vi.unstubAllGlobals());

it('runs analysis on request and shows the metrics and both charts', async () => {
  vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) => Promise.resolve(url.endsWith('/analysis') ? jsonResponse({ message: 'Informe pendiente.' }, 404) : jsonResponse(analysis))));
  render(<AnalysisPanel audioId={audioAsset.id} />);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Analizar grabación' })).toBeEnabled());
  expect(fetch).toHaveBeenCalledTimes(1);
  await userEvent.click(screen.getByRole('button', { name: 'Analizar grabación' }));
  expect(await screen.findByText('LUFS')).toBeInTheDocument();
  expect(screen.getByText('True peak')).toBeInTheDocument();
  expect(screen.getAllByRole('img')).toHaveLength(2);
  expect(screen.getByRole('button', { name: 'Descargar informe' })).toBeEnabled();
});

it('recovers a cached report without submitting another analysis', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(analysis)));
  render(<AnalysisPanel audioId={audioAsset.id} />);
  expect(await screen.findByText('LUFS')).toBeInTheDocument();
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole('button', { name: 'Analizar grabación' })).not.toBeInTheDocument();
});

it('explains undefined loudness for silence without inventing a numeric floor', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ ...analysis, integrated_lufs: null, peak_dbfs: null })));
  render(<AnalysisPanel audioId={audioAsset.id} />);
  expect(await screen.findByText(/indica una medición no definida/)).toBeInTheDocument();
  expect(screen.getAllByText('—', { exact: false }).length).toBeGreaterThanOrEqual(1);
});

it('allows retry after an analysis failure', async () => {
  let posts = 0;
  vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) => {
    if (url.endsWith('/analysis')) return Promise.resolve(jsonResponse({ message: 'Sin informe.' }, 404));
    posts += 1;
    return Promise.resolve(posts === 1 ? jsonResponse({ message: 'El servicio está ocupado.' }, 503) : jsonResponse(analysis));
  }));
  render(<AnalysisPanel audioId={audioAsset.id} />);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Analizar grabación' })).toBeEnabled());
  await userEvent.click(screen.getByRole('button', { name: 'Analizar grabación' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('El servicio está ocupado.');
  await userEvent.click(screen.getByRole('button', { name: 'Analizar grabación' }));
  expect(await screen.findByText('LUFS')).toBeInTheDocument();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

it('aborts the pending analysis request when changing recordings', async () => {
  let pendingSignal: AbortSignal | null | undefined;
  vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string, options: RequestInit) => {
    if (url.endsWith('/analysis')) return Promise.resolve(jsonResponse({ message: 'Sin informe.' }, 404));
    pendingSignal = options.signal;
    return new Promise((_resolve, reject) => options.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError'))));
  }));
  const view = render(<AnalysisPanel audioId={audioAsset.id} />);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Analizar grabación' })).toBeEnabled());
  await userEvent.click(screen.getByRole('button', { name: 'Analizar grabación' }));
  view.unmount();
  expect(pendingSignal?.aborted).toBe(true);
});
