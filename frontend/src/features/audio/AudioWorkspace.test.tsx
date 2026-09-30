import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { audioAsset, audioConfig, jsonResponse } from '../../test/fixtures';
import { AudioWorkspace } from './AudioWorkspace';

vi.mock('./AudioPlayer', () => ({ AudioPlayer: () => <div>Reproductor de prueba</div> }));
vi.mock('./AnalysisPanel', () => ({ AnalysisPanel: ({ audioId }: { audioId: string }) => <div>Informe para {audioId}</div> }));
afterEach(() => vi.unstubAllGlobals());

it('uploads a chosen file, shows native metadata, and allows replacing it', async () => {
  vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) => Promise.resolve(jsonResponse(url === '/api/audio/config' ? audioConfig : audioAsset))));
  render(<AudioWorkspace />);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Subir una grabación' })).toBeEnabled());
  await userEvent.upload(screen.getByLabelText('Seleccionar archivo de audio'), new File(['RIFF'], 'episode.wav', { type: 'audio/wav' }));
  expect(await screen.findByText('episode.wav')).toBeInTheDocument();
  expect(screen.getByText('44.1 kHz')).toBeInTheDocument();
  expect(screen.getByText('Mono')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Cambiar audio' })).toBeEnabled();
});

it('checks dropped format and server-configured size before sending an upload', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ ...audioConfig, max_upload_bytes: 2 })));
  const { container } = render(<AudioWorkspace />);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Subir una grabación' })).toBeEnabled());
  const zone = container.querySelector('.drop-zone')!;
  fireEvent.drop(zone, { dataTransfer: { files: [new File(['x'], 'video.mov')] } });
  expect(screen.getByRole('alert')).toHaveTextContent('Usa un archivo');
  fireEvent.drop(zone, { dataTransfer: { files: [new File(['large'], 'large.wav')] } });
  expect(screen.getByRole('alert')).toHaveTextContent('no superar');
  expect(fetch).toHaveBeenCalledTimes(1);
});

it('shows server failures and permits another upload', async () => {
  vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string) => Promise.resolve(url === '/api/audio/config' ? jsonResponse(audioConfig) : jsonResponse({ code: 'invalid_audio_file', message: 'El archivo está dañado.' }, 422))));
  render(<AudioWorkspace />);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Subir una grabación' })).toBeEnabled());
  await userEvent.upload(screen.getByLabelText('Seleccionar archivo de audio'), new File(['bad'], 'broken.wav'));
  expect(await screen.findByRole('alert')).toHaveTextContent('El archivo está dañado.');
  expect(screen.getByRole('button', { name: 'Subir una grabación' })).toBeEnabled();
});

it('cancels an in-flight request and restores the upload controls', async () => {
  vi.stubGlobal('fetch', vi.fn().mockImplementation((url: string, options: RequestInit) => {
    if (url === '/api/audio/config') return Promise.resolve(jsonResponse(audioConfig));
    return new Promise((_resolve, reject) => options.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError'))));
  }));
  render(<AudioWorkspace />);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Subir una grabación' })).toBeEnabled());
  await userEvent.upload(screen.getByLabelText('Seleccionar archivo de audio'), new File(['x'], 'test.wav'));
  await userEvent.click(await screen.findByRole('button', { name: 'Cancelar carga' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'Subir una grabación' })).toBeEnabled());
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});
