import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import type { ProcessingPlan } from '../../api/processing';
import { audioAsset, jsonResponse, processingPlan, processingReport, waveform } from '../../test/fixtures';
import { masteringPresets, masteringReport } from '../../test/mastering-fixtures';
import { processingPresets } from '../../test/processing-fixtures';
import { CorrectionsPanel } from './CorrectionsPanel';

vi.mock('wavesurfer.js', () => ({ default: { create: vi.fn(() => ({ on: vi.fn(), destroy: vi.fn() })) } }));
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

function proposal(preset = 'balanced', target = 'podcast_standard'): ProcessingPlan {
  return { ...processingPlan, preset, mastering_preset: target,
    steps: processingPlan.steps.map((step) => step.processor === 'dehum' ? { ...step, enabled: false, decision: 'recommended' } : step),
    mastering_steps: [
      { processor: 'loudness_normalization', enabled: true, decision: 'automatic', parameters: { target_lufs: target === 'podcast_standard' ? -16 : -23 }, reason: 'Normalizar el loudness.', source_diagnostic: null, confidence: null, evidence: {} },
      { processor: 'true_peak_limiter', enabled: true, decision: 'automatic', parameters: { max_true_peak_dbtp: -1 }, reason: 'Proteger los picos y verificar el máster.', source_diagnostic: null, confidence: null, evidence: {} },
    ],
  };
}

function server(options: { failPreset?: boolean; failAuto?: boolean; silent?: boolean } = {}) {
  const fetch = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    const address = new URL(url, 'http://localhost');
    if (address.pathname.endsWith('/processing/presets')) return Promise.resolve(jsonResponse(processingPresets));
    if (address.pathname.endsWith('/mastering/presets')) return Promise.resolve(jsonResponse(masteringPresets));
    if (address.pathname.endsWith('/processing/plan')) {
      if (address.search && options.failPreset) return Promise.resolve(jsonResponse({ message: 'No se pudo preparar el preset.' }, 503));
      const plan = proposal(address.searchParams.get('preset') ?? undefined, address.searchParams.get('mastering_preset') ?? undefined);
      if (options.silent) plan.mastering_steps = plan.mastering_steps.map((step) => ({ ...step, enabled: false }));
      return Promise.resolve(jsonResponse(plan));
    }
    if (address.pathname.endsWith('/auto-process')) {
      if (options.failAuto) return Promise.resolve(jsonResponse({ message: 'El máster no supera el control de calidad.' }, 422));
      const plan = JSON.parse(String(init?.body)).plan as ProcessingPlan;
      return Promise.resolve(jsonResponse({ processing: { ...processingReport, plan }, mastering: masteringReport }));
    }
    if (address.pathname.endsWith('/processing')) return Promise.resolve(options.failAuto ? jsonResponse({ ...processingReport, plan: proposal() }) : jsonResponse({ message: 'Pendiente.' }, 404));
    if (address.pathname.endsWith('/waveform')) return Promise.resolve(jsonResponse(waveform));
    return Promise.resolve(jsonResponse({ message: 'Pendiente.' }, 404));
  });
  vi.stubGlobal('fetch', fetch);
  return fetch;
}

it('prepares a new preset and resets manual choices while retaining the published result', async () => {
  const fetch = server();
  render(<CorrectionsPanel audioId={audioAsset.id} />);
  const hum = await screen.findByRole('article', { name: 'Eliminar zumbido' });
  expect(within(hum).getByText('Recomendado · revisar')).toBeInTheDocument();
  expect(within(hum).getByRole('checkbox')).not.toBeChecked();
  await userEvent.click(within(hum).getByRole('checkbox'));
  expect(within(hum).getByText('Ajuste manual')).toBeInTheDocument();
  await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Preset de procesado' }), 'natural');
  await waitFor(() => expect(screen.getByRole('combobox', { name: 'Preset de procesado' })).toHaveValue('natural'));
  expect(within(screen.getByRole('article', { name: 'Eliminar zumbido' })).getByRole('checkbox')).not.toBeChecked();
  expect(fetch.mock.calls.some(([url]) => String(url).includes('preset=natural'))).toBe(true);
});

it('changing the final objective keeps reviewed correction choices', async () => {
  server(); render(<CorrectionsPanel audioId={audioAsset.id} />);
  const hum = await screen.findByRole('article', { name: 'Eliminar zumbido' });
  await userEvent.click(within(hum).getByRole('checkbox'));
  await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Objetivo final' }), 'broadcast_r128');
  await waitFor(() => expect(screen.getByRole('combobox', { name: 'Objetivo final' })).toHaveValue('broadcast_r128'));
  expect(within(hum).getByRole('checkbox')).toBeChecked();
  expect(within(hum).getByText('Ajuste manual')).toBeInTheDocument();
});

it('one action sends the reviewed plan and displays the verified master', async () => {
  const fetch = server(); render(<CorrectionsPanel audioId={audioAsset.id} />);
  const hum = await screen.findByRole('article', { name: 'Eliminar zumbido' });
  await userEvent.click(within(hum).getByRole('checkbox'));
  await userEvent.click(screen.getByRole('button', { name: 'Procesar y crear máster' }));
  expect(await screen.findByText('Control de calidad aprobado · Podcast Standard')).toBeInTheDocument();
  const call = fetch.mock.calls.find(([url]) => String(url).endsWith('/auto-process'))!;
  const sent = JSON.parse(String((call[1] as RequestInit).body));
  expect(sent.plan.steps.find((s: { processor: string }) => s.processor === 'dehum')).toMatchObject({ enabled: true, decision: 'manual' });
  expect(sent.plan.mastering_steps).toHaveLength(2);
  expect(screen.getByRole('button', { name: 'Descargar máster WAV' })).toBeEnabled();
  expect(fetch.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
});

it('a failed preset request preserves the current proposal and permits retry', async () => {
  server({ failPreset: true }); render(<CorrectionsPanel audioId={audioAsset.id} />);
  const selector = await screen.findByRole('combobox', { name: 'Preset de procesado' });
  await userEvent.selectOptions(selector, 'studio');
  expect(await screen.findByRole('alert')).toHaveTextContent('No se pudo preparar el preset.');
  expect(selector).toHaveValue('balanced');
  expect(screen.getByRole('button', { name: 'Procesar y crear máster' })).toBeEnabled();
});

it('QC failure recovers the corrections without offering an unverified download', async () => {
  server({ failAuto: true }); render(<CorrectionsPanel audioId={audioAsset.id} />);
  await userEvent.click(await screen.findByRole('button', { name: 'Procesar y crear máster' }));
  expect(await screen.findByText('El máster no supera el control de calidad.')).toBeInTheDocument();
  expect(await screen.findByText(/Resultado aplicado: Balanced/)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'Descargar máster WAV' })).not.toBeInTheDocument();
});

it('silence leaves automatic mastering unavailable and corrective processing available', async () => {
  server({ silent: true }); render(<CorrectionsPanel audioId={audioAsset.id} />);
  expect(await screen.findByRole('button', { name: 'Procesar y crear máster' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Aplicar correcciones' })).toBeEnabled();
});
