import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it } from 'vitest';
import type { AudioAnalysis } from '../../api/analysis';
import { analysis } from '../../test/fixtures';
import { ActivityPanel } from './ActivityPanel';

const report = analysis as AudioAnalysis;

it('summarizes speech, approximate SNR and the background profile', () => {
  render(<ActivityPanel report={report} />);
  expect(screen.getByRole('heading', { name: 'Voz y ruido de fondo' })).toBeInTheDocument();
  expect(screen.getByText('60 %')).toBeInTheDocument();
  expect(screen.getByText('32,4 dB')).toBeInTheDocument();
  expect(screen.getByText('Voz frente al fondo, sin referencia limpia.')).toBeInTheDocument();
  expect(screen.getByText('-52,0 dBFS')).toBeInTheDocument();
  expect(screen.getByText('Estable')).toBeInTheDocument();
  expect(screen.getByText(/no una medida exacta/)).toBeInTheDocument();
});

it('draws one timeline lane per label with every segment in place', () => {
  const { container } = render(<ActivityPanel report={report} />);
  const timeline = screen.getByRole('img', { name: /voz durante 60,0 % de la grabación, en 2 tramos/ });
  expect(within(timeline).getByText('Voz')).toBeInTheDocument();
  expect(within(timeline).getByText('Fondo')).toBeInTheDocument();
  expect(within(timeline).getByText('Silencio')).toBeInTheDocument();
  const speech = container.querySelectorAll('.activity-segment.speech');
  expect(speech).toHaveLength(2);
  // 0.5–2 s of a 5 s recording on a 1000-unit lane.
  expect(speech[0].getAttribute('x')).toBe('100');
  expect(Number(speech[0].getAttribute('width'))).toBeCloseTo(300);
  expect(container.querySelectorAll('.activity-segment.noise')).toHaveLength(1);
});

it('omits the silence lane and explains missing estimates', () => {
  render(<ActivityPanel report={{
    ...report,
    estimated_snr_db: null,
    speech_activity: { ...report.speech_activity, silence_seconds: 0, speech_percent: 0, speech_seconds: 0, noise_seconds: 5,
      segments: [{ label: 'noise', start_seconds: 0, end_seconds: 5 }] },
    noise_profile: { ...report.noise_profile, frame_count: 0, duration_seconds: 0, rms_dbfs: null, floor_dbfs: null,
      spectral_flatness: null, relative_power_std: null, spectral_stability: null, frequencies_hz: [], psd_dbfs_per_hz: [] },
  }} />);
  const timeline = screen.getByRole('img', { name: /en 0 tramos/ });
  expect(within(timeline).queryByText('Silencio')).not.toBeInTheDocument();
  expect(screen.getByText('No estimable')).toBeInTheDocument();
  expect(screen.getByText('Faltan tramos de voz o de fondo comparables.')).toBeInTheDocument();
  expect(screen.getAllByText('Sin datos')).toHaveLength(2);
});

it('explains the thresholds used for segmentation', async () => {
  render(<ActivityPanel report={report} />);
  await userEvent.click(screen.getByText('Parámetros de segmentación y perfil de ruido'));
  expect(screen.getByText('Umbral para empezar a hablar')).toBeVisible();
  expect(screen.getByText('-34,0 dBFS')).toBeVisible();
  expect(screen.getByText('Nivel de fondo (percentil 10)')).toBeVisible();
  expect(screen.getByText('No definido')).toBeVisible();
  expect(screen.getByText('50 ventanas · 1,5 s')).toBeVisible();
});
