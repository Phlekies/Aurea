import { useEffect, useRef, useState } from 'react';
import { Pause, Play } from 'lucide-react';
import WaveSurfer from 'wavesurfer.js';
import { getWaveform, streamUrl, type AudioAsset } from '../../api/audio';
import { formatTime } from './format';

type WavePlayerProps = {
  id: string;
  resource: string;
  src: string;
  duration: number;
  label: string;
  title: string;
  /** Distinguishes control labels when several players share a page. */
  controlSuffix?: string;
};

/** Original recording player: waveform, seeking and playback rate. */
export function AudioPlayer({ asset }: { asset: AudioAsset }) {
  return <WavePlayer id={asset.id} resource="waveform" src={streamUrl(asset.id)} duration={asset.duration_seconds}
    label="Audio original" title="AUDIO ORIGINAL" />;
}

/** Waveform player for any rendering of a recording served with precomputed peaks. */
export function WavePlayer({ id, resource, src, duration, label, title, controlSuffix }: WavePlayerProps) {
  const suffix = controlSuffix ? ` (${controlSuffix})` : '';
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
    getWaveform(id, controller.signal, resource).then((data) => {
      if (controller.signal.aborted || !container.current || !audio.current) return;
      wave = WaveSurfer.create({
        container: container.current, media: audio.current, peaks: data.peaks,
        duration: data.duration_seconds, url: src, height: 100,
        waveColor: '#00E5FF', progressColor: '#1DE9B6', cursorColor: '#FFFFFF',
        barWidth: 3, barGap: 2, barRadius: 2, normalize: false, dragToSeek: true,
      });
      wave.on('ready', () => setReady(true));
      wave.on('error', () => { if (!controller.signal.aborted) setWaveError('El waveform no está disponible. Puedes intentar recargarlo.'); });
    }).catch(() => {
      if (!controller.signal.aborted) setWaveError('No se pudo cargar el waveform.');
    });
    return () => { controller.abort(); wave?.destroy(); };
  }, [id, resource, src, attempt]);

  async function togglePlayback() {
    const media = audio.current;
    if (!media) return;
    setPlayError('');
    if (!media.paused) { media.pause(); return; }
    try { await media.play(); }
    catch { setPlayError('No se pudo reproducir el audio. Si ha caducado, vuelve a subirlo.'); }
  }

  return (
    <section className="audio-player" aria-label={`Reproductor de ${label.toLowerCase()}`}>
      <div className="wave-label"><span>{title}</span><span>{ready ? 'Haz clic en la onda para desplazarte' : 'Preparando waveform…'}</span></div>
      <div ref={container} className="waveform" />
      {waveError && <div className="player-error" role="alert">{waveError}<button onClick={() => { setWaveError(''); setReady(false); setAttempt((value) => value + 1); }}>Recargar waveform</button></div>}
      <audio ref={audio} src={src} preload="metadata" aria-label={label}
        onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)}
        onTimeUpdate={() => setTime(audio.current?.currentTime ?? 0)}
        onError={() => setPlayError('No se pudo cargar el audio. Vuelve a subirlo si ha caducado.')} />
      <div className="player-controls">
        <button className="play-button" onClick={() => { void togglePlayback(); }} aria-label={playing ? 'Pausar' : 'Reproducir'}>{playing ? <Pause size={18} /> : <Play size={18} />}</button>
        <span className="player-time">{formatTime(time)}</span>
        <input type="range" aria-label={`Posición de reproducción${suffix}`} min={0} max={duration} step={0.01} value={time}
          onChange={(event) => { const position = Number(event.target.value); if (audio.current) audio.current.currentTime = position; setTime(position); }} />
        <span className="player-time">{formatTime(duration)}</span>
        <label className="speed-label"><span className="sr-only">Velocidad de reproducción{suffix}</span><select defaultValue="1" onChange={(event) => { if (audio.current) audio.current.playbackRate = Number(event.target.value); }}><option value="0.75">0,75×</option><option value="1">1×</option><option value="1.25">1,25×</option><option value="1.5">1,5×</option></select></label>
      </div>
      {playError && <p role="alert" className="player-error">{playError}</p>}
    </section>
  );
}
