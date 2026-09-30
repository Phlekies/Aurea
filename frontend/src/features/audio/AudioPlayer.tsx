import { useEffect, useRef, useState } from 'react';
import { Pause, Play } from 'lucide-react';
import WaveSurfer from 'wavesurfer.js';
import { getWaveform, streamUrl, type AudioAsset } from '../../api/audio';
import { formatTime } from './format';

export function AudioPlayer({ asset }: { asset: AudioAsset }) {
  const container = useRef<HTMLDivElement>(null);
  const audio = useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [waveError, setWaveError] = useState('');
  const [playError, setPlayError] = useState('');
  const [ready, setReady] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let wave: WaveSurfer | undefined;
    getWaveform(asset.id, controller.signal).then((data) => {
      if (controller.signal.aborted || !container.current || !audio.current) return;
      wave = WaveSurfer.create({
        container: container.current, media: audio.current, peaks: data.peaks,
        duration: data.duration_seconds, url: streamUrl(asset.id), height: 100,
        waveColor: '#b6c6a7', progressColor: '#35583f', cursorColor: '#a58a4f',
        barWidth: 3, barGap: 2, barRadius: 2, normalize: false, dragToSeek: true,
      });
      wave.on('ready', () => setReady(true));
      wave.on('error', () => { if (!controller.signal.aborted) setWaveError('El waveform no está disponible. Puedes intentar recargarlo.'); });
    }).catch(() => {
      if (!controller.signal.aborted) setWaveError('No se pudo cargar el waveform.');
    });
    return () => { controller.abort(); wave?.destroy(); };
  }, [asset.id, attempt]);

  async function togglePlayback() {
    const media = audio.current;
    if (!media) return;
    setPlayError('');
    if (!media.paused) { media.pause(); return; }
    try { await media.play(); }
    catch { setPlayError('No se pudo reproducir el audio. Si ha caducado, vuelve a subirlo.'); }
  }

  return (
    <section className="audio-player" aria-label="Reproductor de audio original">
      <div className="wave-label"><span>AUDIO ORIGINAL</span><span>{ready ? 'Haz clic en la onda para desplazarte' : 'Preparando waveform…'}</span></div>
      <div ref={container} className="waveform" />
      {waveError && <div className="player-error" role="alert">{waveError}<button onClick={() => { setWaveError(''); setReady(false); setAttempt((value) => value + 1); }}>Recargar waveform</button></div>}
      <audio ref={audio} src={streamUrl(asset.id)} preload="metadata" aria-label="Audio original"
        onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)}
        onTimeUpdate={() => setTime(audio.current?.currentTime ?? 0)}
        onError={() => setPlayError('No se pudo cargar el audio. Vuelve a subirlo si ha caducado.')} />
      <div className="player-controls">
        <button className="play-button" onClick={() => { void togglePlayback(); }} aria-label={playing ? 'Pausar' : 'Reproducir'}>{playing ? <Pause size={18} /> : <Play size={18} />}</button>
        <span className="player-time">{formatTime(time)}</span>
        <input type="range" aria-label="Posición de reproducción" min={0} max={asset.duration_seconds} step={0.01} value={time}
          onChange={(event) => { const position = Number(event.target.value); if (audio.current) audio.current.currentTime = position; setTime(position); }} />
        <span className="player-time">{formatTime(asset.duration_seconds)}</span>
        <label className="speed-label"><span className="sr-only">Velocidad de reproducción</span><select defaultValue="1" onChange={(event) => { if (audio.current) audio.current.playbackRate = Number(event.target.value); }}><option value="0.75">0,75×</option><option value="1">1×</option><option value="1.25">1,25×</option><option value="1.5">1,5×</option></select></label>
      </div>
      {playError && <p role="alert" className="player-error">{playError}</p>}
    </section>
  );
}
