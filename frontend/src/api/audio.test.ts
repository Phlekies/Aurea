import { afterEach, expect, it, vi } from 'vitest';
import { getAudio, getWaveform, uploadAudio } from './audio';
import { audioAsset, jsonResponse, waveform } from '../test/fixtures';

afterEach(() => vi.unstubAllGlobals());

it('uploads multipart content without forcing a boundary header', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse(audioAsset, 201)));
  await expect(uploadAudio(new File(['x'], 'episode.wav', { type: 'audio/wav' }))).resolves.toEqual(audioAsset);
  expect(fetch).toHaveBeenCalledWith('/api/audio', expect.objectContaining({ method: 'POST', body: expect.any(FormData) }));
  const options = vi.mocked(fetch).mock.calls[0][1];
  expect(options?.headers).toBeUndefined();
});

it('rejects malformed metadata before it reaches the player', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ ...audioAsset, channels: 6 })));
  await expect(getAudio(audioAsset.id)).rejects.toMatchObject({ name: 'ApiError' });
});

it('rejects inconsistent waveform channel counts', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ ...waveform, channels: 2 })));
  await expect(getWaveform(audioAsset.id)).rejects.toMatchObject({ name: 'ApiError' });
});

it('displays the server validation message', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ code: 'invalid_audio_file', message: 'El archivo está dañado.' }, 422)));
  await expect(uploadAudio(new File(['x'], 'broken.wav'))).rejects.toMatchObject({ status: 422, message: 'El archivo está dañado.' });
});
