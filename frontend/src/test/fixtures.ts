export const audioConfig = {
  formats: ['wav', 'flac', 'mp3', 'm4a', 'ogg'], max_upload_bytes: 104857600,
  max_duration_seconds: 1800, retention_seconds: 86400,
  min_sample_rate: 8000, max_sample_rate: 96000, max_channels: 2,
};

export const audioAsset = {
  id: 'a'.repeat(32), filename: 'episode.wav', format: 'wav', codec: 'pcm_s16le',
  bitrate: 705600, size_bytes: 441044, sample_rate: 44100, channels: 1,
  frames: 220500, duration_seconds: 5,
  created_at: '2026-09-30T12:00:00Z', expires_at: '2026-10-01T12:00:00Z',
};

export const waveform = { duration_seconds: 5, sample_rate: 44100, channels: 1, peaks: [[0.1, 0.2, 0.4, 0.2]] };
export function jsonResponse(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } });
}
