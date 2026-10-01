import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { audioAsset, dynamicsPlan, dynamicsReport, jsonResponse, processingPlan, processingReport, waveform } from '../../test/fixtures';
import { CorrectionsPanel } from './CorrectionsPanel';
import type { ProcessingPlan } from '../../api/processing';

vi.mock('wavesurfer.js', () => ({ default: { create: vi.fn(() => ({ on: vi.fn(), destroy: vi.fn() })) } }));
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

function server(options: { existing?: boolean; process?: () => Response; plan?: ProcessingPlan } = {}) {
  const fetch = vi.fn().mockImplementation((url: string) => {
    if (url.endsWith('/processing/plan')) return Promise.resolve(jsonResponse(options.plan ?? processingPlan));
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

it('changes noise algorithm and intensity without editing DSP parameters', async () => {
  const plan = { ...processingPlan, steps: [...processingPlan.steps, {
    processor: 'noise_reduction', enabled: true,
    parameters: { algorithm: 'wiener', strength: 'balanced', noise_frequencies_hz: [0, 22050], noise_psd_dbfs_per_hz: [-90, -90] },
    reason: 'Se detectó un fondo estacionario.', source_diagnostic: 'stationary_noise', confidence: .7,
    evidence: { profile_available: true },
  }] };
  const fetch = server({ plan, process: () => jsonResponse({ ...processingReport, artifacts: {
    total_reduction_db: 3, speech_energy_loss_db: 1, background_reduction_db: 9,
    musical_noise_score: .01, excessive_reduction: false, significant_speech_loss: false, possible_musical_noise: false,
  } }) });
  render(<CorrectionsPanel audioId={audioAsset.id} />);
  await userEvent.selectOptions(await screen.findByRole('combobox', { name: 'Método de reducción' }), 'spectral_gate');
  await userEvent.selectOptions(screen.getByRole('combobox', { name: 'Intensidad de reducción' }), 'strong');
  await userEvent.click(screen.getByRole('button', { name: 'Aplicar correcciones' }));
  const call = fetch.mock.calls.find(([url]) => String(url).endsWith('/process'))!;
  const sent = JSON.parse(String((call[1] as RequestInit).body));
  expect(sent.plan.steps.at(-1).parameters).toMatchObject({ algorithm: 'spectral_gate', strength: 'strong' });
  expect(sent.plan.steps.at(-1).parameters.noise_psd_dbfs_per_hz).toEqual([-90, -90]);
  expect(await screen.findByLabelText('Comprobaciones de reducción de ruido')).toHaveTextContent('Fondo reducido: 9,0 dB');
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

it('edits dynamics settings, preserves speech intervals and reports the applied settings', async () => {
  const fetch = server({ plan: dynamicsPlan, process: () => jsonResponse(dynamicsReport) });
  render(<CorrectionsPanel audioId={audioAsset.id} />);
  const leveler = await screen.findByRole('article', { name: 'Nivelar la voz' });
  const compressor = screen.getByRole('article', { name: 'Comprimir la dinámica' });
  await userEvent.click(within(leveler).getByText('Ajustes de nivelado'));
  await userEvent.clear(within(leveler).getByRole('spinbutton', { name: 'Objetivo de voz (dBFS)' }));
  await userEvent.type(within(leveler).getByRole('spinbutton', { name: 'Objetivo de voz (dBFS)' }), '-22');
  await userEvent.clear(within(leveler).getByRole('spinbutton', { name: 'Aumento máximo (dB)' }));
  await userEvent.type(within(leveler).getByRole('spinbutton', { name: 'Aumento máximo (dB)' }), '6');
  await userEvent.click(within(leveler).getByText('¿Por qué?'));
  expect(within(leveler).getByText(/Se utilizan 2 tramos de actividad/)).toBeVisible();
  expect(within(leveler).queryByText('Speech starts seconds')).not.toBeInTheDocument();
  await userEvent.click(within(compressor).getByText('Ajustes de compresión'));
  for (const [name, value] of [['Umbral (dBFS)', '-20'], ['Relación de compresión (:1)', '3'], ['Transición suave (dB)', '8'], ['Ataque (ms)', '15'], ['Recuperación (ms)', '200'], ['Compensación (dB)', '1']]) {
    const input = within(compressor).getByRole('spinbutton', { name });
    await userEvent.clear(input); await userEvent.type(input, value);
  }
  await userEvent.click(screen.getByRole('button', { name: 'Aplicar correcciones' }));
  const call = fetch.mock.calls.find(([url]) => String(url).endsWith('/process'))!;
  const sent = JSON.parse(String((call[1] as RequestInit).body));
  expect(sent.plan.steps[4].parameters).toMatchObject({ target_rms_dbfs: -22, max_boost_db: 6, speech_starts_seconds: [.5, 3.5], speech_ends_seconds: [2, 5] });
  expect(sent.plan.steps[5].parameters).toEqual({ threshold_dbfs: -20, ratio: 3, knee_db: 8, attack_ms: 15, release_ms: 200, makeup_gain_db: 1 });
  expect(await screen.findByLabelText('Ganancia aplicada a la voz')).toHaveTextContent('Nivelar la voz: de -4,0 a 5,0 dB');
  expect(screen.getByText(/Aplicado:.*Nivelar la voz \(Objetivo -24 dBFS · aumento hasta 8 dB\).*Comprimir la dinámica \(-18 dBFS · 2,0:1\)/)).toBeInTheDocument();
});

it('bounds an out-of-range setting and downloads complete applied curves', async () => {
  server({ plan: dynamicsPlan, process: () => jsonResponse(dynamicsReport) });
  const createUrl = vi.fn().mockReturnValue('blob:processing-report');
  vi.stubGlobal('URL', class extends URL { static createObjectURL = createUrl; static revokeObjectURL = vi.fn(); });
  const clicked = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  render(<CorrectionsPanel audioId={audioAsset.id} />);
  const leveler = await screen.findByRole('article', { name: 'Nivelar la voz' });
  await userEvent.click(within(leveler).getByText('Ajustes de nivelado'));
  const boost = within(leveler).getByRole('spinbutton', { name: 'Aumento máximo (dB)' });
  await userEvent.clear(boost); await userEvent.type(boost, '99'); await userEvent.tab();
  expect(boost).toHaveValue(12);
  await userEvent.click(screen.getByRole('button', { name: 'Aplicar correcciones' }));
  await userEvent.click(await screen.findByRole('button', { name: 'Descargar informe de correcciones' }));
  expect(clicked).toHaveBeenCalledOnce();
  const exported = await new Promise<string>((resolve) => {
    const reader = new FileReader(); reader.onload = () => resolve(String(reader.result));
    reader.readAsText(createUrl.mock.calls[0][0] as Blob);
  });
  expect(JSON.parse(exported)).toEqual(dynamicsReport);
});
