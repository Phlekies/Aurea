import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { audioAsset, jsonResponse, processingPlan, processingReport, waveform } from '../../test/fixtures';
import { CorrectionsPanel } from './CorrectionsPanel';

vi.mock('wavesurfer.js', () => ({ default: { create: vi.fn(() => ({ on: vi.fn(), destroy: vi.fn() })) } }));
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

function server(options: { existing?: boolean; process?: () => Response } = {}) {
  const fetch = vi.fn().mockImplementation((url: string) => {
    if (url.endsWith('/processing/plan')) return Promise.resolve(jsonResponse(processingPlan));
    if (url.endsWith('/processing')) return Promise.resolve(options.existing ? jsonResponse(processingReport) : jsonResponse({ message: 'Sin versión procesada.' }, 404));
    if (url.endsWith('/processed/waveform')) return Promise.resolve(jsonResponse(waveform));
    if (url.endsWith('/process')) return Promise.resolve(options.process ? options.process() : jsonResponse(processingReport));
    return Promise.resolve(jsonResponse({ message: 'No encontrado.' }, 404));
  });
  vi.stubGlobal('fetch', fetch);
  return fetch;
}

it('lists every recommended step with its reason, parameters and evidence', async () => {
  server();
  render(<CorrectionsPanel audioId={audioAsset.id} />);
  expect(await screen.findByText('2 correcciones activas de 4.')).toBeInTheDocument();
  const dehum = screen.getByRole('article', { name: 'Eliminar zumbido' });
  expect(within(dehum).getByRole('checkbox')).toBeChecked();
  expect(within(dehum).getByText('50 Hz · 3 líneas · −24 dB')).toBeInTheDocument();
  expect(within(dehum).getByText('Se detectó zumbido de 50 Hz.')).toBeInTheDocument();
  await userEvent.click(within(dehum).getByText('¿Por qué?'));
  expect(within(dehum).getByText(/Basado en el diagnóstico «Zumbido eléctrico» · evidencia 82 %/)).toBeVisible();
  expect(within(dehum).getByText('Atenuación en cada línea')).toBeVisible();
  expect(within(dehum).getByText('Contraste de la línea más destacada')).toBeVisible();
  expect(within(screen.getByRole('article', { name: 'Filtro paso alto' })).getByRole('checkbox')).not.toBeChecked();
  expect(screen.getByText(/El original nunca se modifica/)).toBeInTheDocument();
});

it('sends the user-adjusted plan and shows the before/after comparison', async () => {
  const fetch = server();
  render(<CorrectionsPanel audioId={audioAsset.id} />);
  const highPass = await screen.findByRole('article', { name: 'Filtro paso alto' });
  await userEvent.click(within(highPass).getByRole('checkbox'));
  await userEvent.click(within(screen.getByRole('article', { name: 'Eliminar zumbido' })).getByRole('checkbox'));
  expect(screen.getByText('2 correcciones activas de 4.')).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Aplicar correcciones' }));
  const call = fetch.mock.calls.find(([url]) => String(url).endsWith('/process'))!;
  const sent = JSON.parse(String((call[1] as RequestInit).body));
  expect(sent.plan.steps.map((step: { enabled: boolean }) => step.enabled)).toEqual([true, true, false, false]);
  expect((call[1] as RequestInit).method).toBe('POST');
  const table = await screen.findByRole('table', { name: 'Antes y después de las correcciones' });
  expect(within(table).getByRole('row', { name: /Desplazamiento DC/ })).toHaveTextContent('0,02000');
  expect(within(table).getByRole('row', { name: /Desplazamiento DC/ })).toHaveTextContent('0,00001');
  expect(within(table).getByRole('row', { name: /Problemas detectados/ })).toHaveTextContent('Zumbido eléctricoNinguno');
  expect(screen.getByRole('region', { name: 'Reproductor de audio corregido' })).toBeInTheDocument();
  expect(screen.getByText(/Aplicado: Eliminar desplazamiento DC, Eliminar zumbido · procesado en 0,40 s/)).toBeInTheDocument();
});

it('restores a previous rendering and its applied plan', async () => {
  server({ existing: true });
  render(<CorrectionsPanel audioId={audioAsset.id} />);
  expect(await screen.findByRole('table', { name: 'Antes y después de las correcciones' })).toBeInTheDocument();
  expect(within(screen.getByRole('article', { name: 'Eliminar zumbido' })).getByRole('checkbox')).toBeChecked();
});

it('reports rendering failures and safety trims without losing the plan', async () => {
  let attempts = 0;
  server({
    process: () => {
      attempts += 1;
      return attempts === 1
        ? jsonResponse({ code: 'processing_failed', message: 'No se pudo completar el procesado. El original no se ha modificado.' }, 503)
        : jsonResponse({ ...processingReport, safety_gain_db: -1.2, warnings: ['Se aplicó una reducción de seguridad de 1,20 dB sin recortar.'] });
    },
  });
  render(<CorrectionsPanel audioId={audioAsset.id} />);
  await userEvent.click(await screen.findByRole('button', { name: 'Aplicar correcciones' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('El original no se ha modificado.');
  expect(screen.getByRole('article', { name: 'Eliminar zumbido' })).toBeInTheDocument();
  await userEvent.click(screen.getByRole('button', { name: 'Aplicar correcciones' }));
  expect(await screen.findByRole('status')).toHaveTextContent('reducción de seguridad de 1,20 dB');
  await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
});
