import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { audioAsset, jsonResponse, waveform } from '../../test/fixtures';
import { masteringPresets, masteringReport } from '../../test/mastering-fixtures';
import { MasteringPanel } from './MasteringPanel';

vi.mock('wavesurfer.js', () => ({ default: { create: vi.fn(() => ({ on: vi.fn(), destroy: vi.fn() })) } }));
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

function server({ existing = false, master = () => jsonResponse(masteringReport), download = () => new Response(new Blob(['RIFF']), { headers: { 'Content-Type': 'audio/wav' } }) } = {}) {
  const fetch = vi.fn().mockImplementation((url: string) => {
    if (url.endsWith('/mastering/presets')) return Promise.resolve(jsonResponse(masteringPresets));
    if (url.endsWith('/mastering')) return Promise.resolve(existing ? jsonResponse(masteringReport) : jsonResponse({ message: 'Sin máster.' }, 404));
    if (url.endsWith('/mastered/waveform')) return Promise.resolve(jsonResponse(waveform));
    if (url.endsWith('/master')) return Promise.resolve(master());
    if (url.endsWith('/mastered/download')) return Promise.resolve(download());
    throw new Error(`Unexpected URL ${url}`);
  });
  vi.stubGlobal('fetch', fetch); return fetch;
}

it('offers configurable targets and enables WAV download only after QC', async () => {
  const fetch = server(); render(<MasteringPanel audioId={audioAsset.id} />);
  const select = await screen.findByRole('combobox', { name: 'Objetivo de publicación' });
  expect(screen.queryByRole('button', { name: 'Descargar máster WAV' })).not.toBeInTheDocument();
  await userEvent.selectOptions(select, 'broadcast_r128');
  await userEvent.click(screen.getByRole('button', { name: 'Crear máster' }));
  const call = fetch.mock.calls.find(([url]) => String(url).endsWith('/master'))!;
  expect(JSON.parse(String((call[1] as RequestInit).body))).toEqual({ preset: 'broadcast_r128' });
  expect(await screen.findByText(/Control de calidad aprobado/)).toBeInTheDocument();
  expect(screen.getByText(/El resultado corresponde a Podcast Standard/)).toBeInTheDocument();
  const table = screen.getByRole('table', { name: 'Antes y después de masterizar' });
  expect(within(table).getByRole('row', { name: /Loudness integrado/ })).toHaveTextContent('-16,0 LUFS');
  expect(screen.getByRole('region', { name: 'Reproductor de máster final' })).toBeInTheDocument();
  await userEvent.click(screen.getByText('Ver las 8 comprobaciones de calidad'));
  expect(screen.getByText('Sin nueva saturación digital')).toBeVisible();
  expect(screen.getByRole('img', { name: /Evolución del loudness/ })).toBeInTheDocument();
});

it('restores a verified master and downloads the checked endpoint', async () => {
  const fetch = server({ existing: true });
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  const create = vi.fn(() => 'blob:master'); vi.stubGlobal('URL', { createObjectURL: create, revokeObjectURL: vi.fn() });
  render(<MasteringPanel audioId={audioAsset.id} />);
  await userEvent.click(await screen.findByRole('button', { name: 'Descargar máster WAV' }));
  await waitFor(() => expect(click).toHaveBeenCalledOnce());
  expect(fetch.mock.calls.some(([url]) => String(url).endsWith('/mastered/download'))).toBe(true);
  expect(create).toHaveBeenCalledWith(expect.objectContaining({ type: 'audio/wav' }));
});

it('does not expose a failed or malformed QC report', async () => {
  server({ master: () => jsonResponse({ ...masteringReport, qc: { ...masteringReport.qc, passed: false } }) });
  render(<MasteringPanel audioId={audioAsset.id} />);
  await userEvent.click(await screen.findByRole('button', { name: 'Crear máster' }));
  expect(await screen.findByRole('alert')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Descargar máster WAV' })).not.toBeInTheDocument();
});

it('shows a backend QC rejection and permits retrying', async () => {
  let attempts = 0; server({ master: () => ++attempts === 1 ? jsonResponse({ message: 'La salida no supera el control de calidad.' }, 422) : jsonResponse(masteringReport) });
  render(<MasteringPanel audioId={audioAsset.id} />);
  await userEvent.click(await screen.findByRole('button', { name: 'Crear máster' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('La salida no supera el control de calidad.');
  await userEvent.click(screen.getByRole('button', { name: 'Crear máster' }));
  expect(await screen.findByRole('button', { name: 'Descargar máster WAV' })).toBeEnabled();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

it('reports invalidated downloads without saving an error response', async () => {
  server({ existing: true, download: () => jsonResponse({ message: 'Vuelve a masterizar las correcciones actuales.' }, 404) });
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  render(<MasteringPanel audioId={audioAsset.id} />);
  await userEvent.click(await screen.findByRole('button', { name: 'Descargar máster WAV' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Vuelve a masterizar');
  expect(click).not.toHaveBeenCalled();
});

it('disables rendering and download while corrections are running', async () => {
  server({ existing: true }); render(<MasteringPanel audioId={audioAsset.id} correctionsBusy />);
  expect(await screen.findByRole('button', { name: 'Descargar máster WAV' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Crear máster' })).toBeDisabled();
});
