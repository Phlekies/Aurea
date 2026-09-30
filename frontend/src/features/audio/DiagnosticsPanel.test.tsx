import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it } from 'vitest';
import type { Diagnostic } from '../../api/analysis';
import { diagnostics } from '../../test/fixtures';
import { DiagnosticsPanel } from './DiagnosticsPanel';

function withChanges(changes: Partial<Record<Diagnostic['code'], Partial<Diagnostic>>>): Diagnostic[] {
  return diagnostics.map((diagnostic) => ({ ...diagnostic, ...changes[diagnostic.code] }));
}

it('prioritizes possible problems by severity and explains their evidence', () => {
  render(<DiagnosticsPanel diagnostics={withChanges({
    low_level: { detected: true, severity: .4, confidence: .6, message: 'El nivel de la grabación es bajo.' },
    clipping: { detected: true, severity: .9, confidence: .95, message: 'La señal llega al límite digital.' },
  })} />);
  expect(screen.getByText('2 posibles problemas detectados.')).toBeInTheDocument();
  const problems = within(screen.getByLabelText('Posibles problemas detectados')).getAllByRole('article');
  expect(problems.map((problem) => problem.getAttribute('aria-label'))).toEqual(['Saturación digital', 'Nivel bajo']);
  expect(within(problems[0]).getByText('Severidad alta')).toBeInTheDocument();
  expect(within(problems[0]).getByText('Evidencia alta')).toBeInTheDocument();
  expect(within(problems[1]).getByText('Severidad media')).toBeInTheDocument();
  expect(within(problems[1]).getByText('Evidencia moderada')).toBeInTheDocument();
  expect(screen.getByText(/estimación heurística, no una probabilidad/)).toBeInTheDocument();
});

it('shows no detections without claiming the recording is perfect', async () => {
  render(<DiagnosticsPanel diagnostics={diagnostics} />);
  expect(screen.getByText('No se han detectado problemas en estas comprobaciones.')).toBeInTheDocument();
  expect(screen.getByText(/Sin indicios no garantiza/)).toBeInTheDocument();
  expect(screen.queryByLabelText('Posibles problemas detectados')).not.toBeInTheDocument();
  await userEvent.click(screen.getByText('Todas las comprobaciones'));
  expect(screen.getAllByRole('article')).toHaveLength(8);
  expect(screen.getAllByText('Sin indicios')).toHaveLength(8);
});

it('distinguishes inconclusive checks from checks with no signs', async () => {
  render(<DiagnosticsPanel diagnostics={withChanges({ sibilance: { confidence: .2, message: 'No hay actividad suficiente para evaluar la sibilancia.' } })} />);
  expect(screen.getByText(/1 comprobación con evidencia insuficiente/)).toBeInTheDocument();
  await userEvent.click(screen.getByText('Todas las comprobaciones'));
  expect(within(screen.getByRole('article', { name: 'Sibilancia' })).getByText('Evidencia insuficiente')).toBeVisible();
  expect(screen.getAllByText('Sin indicios')).toHaveLength(7);
});

it('expands evidence and detector parameters with localized units', async () => {
  render(<DiagnosticsPanel diagnostics={withChanges({ hum: {
    detected: true, severity: .2, confidence: .3, message: 'Hay una línea tonal persistente.',
    evidence: { base_frequency_hz: 50, harmonic_frequencies_hz: [50, 100], harmonic_contrast_db: [12.5, 9], analyzed_windows: 4 },
    parameters: { minimum_duration_seconds: .5, band_low_hz: 40, band_high_hz: 200 },
  } })} />);
  const card = screen.getByRole('article', { name: 'Zumbido eléctrico' });
  expect(within(card).getByText('Evidencia limitada')).toBeVisible();
  expect(within(card).getByText('Frecuencia de red')).not.toBeVisible();
  await userEvent.click(within(card).getByText('Evidencia y parámetros'));
  expect(within(card).getByText('Frecuencia de red')).toBeVisible();
  expect(within(card).getByText('50 Hz')).toBeVisible();
  expect(within(card).getByText('50 Hz · 100 Hz')).toBeVisible();
  expect(within(card).getByText('12,5 dB · 9 dB')).toBeVisible();
  expect(within(card).getByText('Duración mínima')).toBeVisible();
  expect(within(card).getByText('0,5 s')).toBeVisible();
});

it('keeps unknown evidence readable and handles absent measurements', async () => {
  render(<DiagnosticsPanel diagnostics={withChanges({ rumble: {
    detected: true, severity: .1, confidence: .6,
    evidence: { low_band_energy_percent: null, reference_available: false, usable_windows: [], custom_note: 'Revisión pendiente' },
  } })} />);
  const card = screen.getByRole('article', { name: 'Retumbo de graves' });
  await userEvent.click(within(card).getByText('Evidencia y parámetros'));
  expect(within(card).getByText('No medido')).toBeVisible();
  expect(within(card).getByText('No', { exact: true })).toBeVisible();
  expect(within(card).getByText('Ninguno')).toBeVisible();
  expect(within(card).getByText('Custom note')).toBeVisible();
  expect(within(card).getByText('Revisión pendiente')).toBeVisible();
});

it('retains meaningful precision for small detector thresholds', async () => {
  render(<DiagnosticsPanel diagnostics={withChanges({ clipping: {
    detected: true, severity: .8, confidence: .8, parameters: { flat_top_difference_threshold: .000001 },
  } })} />);
  const card = screen.getByRole('article', { name: 'Saturación digital' });
  await userEvent.click(within(card).getByText('Evidencia y parámetros'));
  expect(within(card).getByText('0,000001')).toBeVisible();
});

it('labels the background comparison without presenting it as a measured SNR', async () => {
  render(<DiagnosticsPanel diagnostics={withChanges({ stationary_noise: {
    detected: true, severity: .5, confidence: .7,
    evidence: { speech_to_background_level_gap_db: 14.2 }, parameters: { level_gap_threshold_db: 25 },
  } })} />);
  const card = screen.getByRole('article', { name: 'Ruido continuo' });
  await userEvent.click(within(card).getByText('Evidencia y parámetros'));
  expect(within(card).getByText('Diferencia de nivel entre voz y fondo')).toBeVisible();
  expect(within(card).getByText('14,2 dB')).toBeVisible();
  expect(within(card).getByText('Diferencia máxima entre voz y fondo')).toBeVisible();
  expect(within(card).queryByText(/señal\/ruido/)).not.toBeInTheDocument();
});
