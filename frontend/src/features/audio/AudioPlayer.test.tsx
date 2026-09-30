import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { audioAsset, jsonResponse, waveform } from '../../test/fixtures';
import { AudioPlayer } from './AudioPlayer';
import WaveSurfer from 'wavesurfer.js';

vi.mock('wavesurfer.js', () => ({ default: { create: vi.fn(() => ({ on: vi.fn(), destroy: vi.fn() })) } }));
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

it('passes server peaks and duration to the renderer and seeks via keyboard control', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(waveform)));
  const { container, unmount } = render(<AudioPlayer asset={audioAsset} />);
  await waitFor(() => expect(WaveSurfer.create).toHaveBeenCalled());
  expect(WaveSurfer.create).toHaveBeenCalledWith(expect.objectContaining({ peaks: waveform.peaks, duration: 5 }));
  const media = container.querySelector('audio')!;
  fireEvent.change(screen.getByRole('slider', { name: 'Posición de reproducción' }), { target: { value: '2.5' } });
  expect(media.currentTime).toBe(2.5);
  fireEvent.change(screen.getByRole('combobox', { name: 'Velocidad de reproducción' }), { target: { value: '1.5' } });
  expect(media.playbackRate).toBe(1.5);
  unmount();
});

it('reports a playback failure without an unhandled promise', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(waveform)));
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockRejectedValue(new Error('Not allowed'));
  render(<AudioPlayer asset={audioAsset} />);
  await userEvent.click(screen.getByRole('button', { name: 'Reproducir' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('No se pudo reproducir');
});
