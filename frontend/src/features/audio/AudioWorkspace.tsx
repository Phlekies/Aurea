import { useEffect, useRef, useState, type DragEvent } from 'react';
import { ArrowRight, AudioLines, FileAudio, LoaderCircle, ShieldCheck, Upload } from 'lucide-react';
import { getAudioConfig, uploadAudio, type AudioAsset, type AudioConfig } from '../../api/audio';
import { ApiError } from '../../api/client';
import { AudioPlayer } from './AudioPlayer';
import { AnalysisPanel } from './AnalysisPanel';
import { formatSize, formatTime } from './format';

export function AudioWorkspace() {
  const input = useRef<HTMLInputElement>(null);
  const uploadController = useRef<AbortController | null>(null);
  const [config, setConfig] = useState<AudioConfig | null>(null);
  const [configAttempt, setConfigAttempt] = useState(0);
  const [configError, setConfigError] = useState('');
  const [asset, setAsset] = useState<AudioAsset | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    getAudioConfig(controller.signal).then(setConfig).catch(() => {
      if (!controller.signal.aborted) setConfigError('No se pudieron cargar los límites del servicio.');
    });
    return () => controller.abort();
  }, [configAttempt]);
  useEffect(() => () => uploadController.current?.abort(), []);

  async function submit(file: File) {
    if (!config || busy) return;
    setError('');
    const extension = file.name.split('.').pop()?.toLowerCase() ?? '';
    if (!config.formats.includes(extension)) { setError('Usa un archivo WAV, FLAC, MP3, M4A u OGG.'); return; }
    if (file.size === 0 || file.size > config.max_upload_bytes) { setError(`El archivo debe contener audio y no superar ${formatSize(config.max_upload_bytes)}.`); return; }
    const controller = new AbortController();
    uploadController.current = controller;
    setBusy(true);
    try {
      const recording = await uploadAudio(file, controller.signal);
      if (!controller.signal.aborted) setAsset(recording);
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure instanceof ApiError ? failure.message : 'No se pudo subir el archivo. Comprueba la conexión y reintenta.');
    } finally {
      if (uploadController.current === controller) { uploadController.current = null; setBusy(false); }
    }
  }

  function drop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault(); setDragging(false);
    if (event.dataTransfer.files.length !== 1) { setError('Sube una sola grabación cada vez.'); return; }
    void submit(event.dataTransfer.files[0]);
  }

  return (
    <section className="studio-card" aria-labelledby="session-title">
      <div className="card-heading"><div><h2 id="session-title">{asset ? 'Escucha tu grabación' : 'Todo empieza con tu audio'}</h2><p>{asset ? 'La señal original, preparada para escuchar y analizar.' : 'Sube una grabación y descubre sus primeros detalles.'}</p></div><span className="phase-badge">{asset ? 'Audio cargado' : 'Nuevo proyecto'}</span></div>
      <input ref={input} type="file" className="sr-only" aria-label="Seleccionar archivo de audio" accept={config?.formats.map((format) => `.${format}`).join(',') ?? '.wav,.flac,.mp3,.m4a,.ogg'} disabled={!config || busy}
        onChange={(event) => { const file = event.target.files?.[0]; if (file) void submit(file); event.target.value = ''; }} />
      {configError && <div role="alert" className="upload-error">{configError}<button onClick={() => { setConfigError(''); setConfigAttempt((value) => value + 1); }}>Reintentar</button></div>}
      {error && <div role="alert" className="upload-error">{error}</div>}
      {busy ? (
        <div className="upload-placeholder" aria-busy="true"><LoaderCircle className="loading-spinner" size={30} /><h3>Preparando tu grabación</h3><p>Subiendo, verificando y generando la vista de audio.<br />Los archivos largos pueden tardar unos instantes.</p><button className="secondary-button" onClick={() => uploadController.current?.abort()}>Cancelar carga</button></div>
      ) : !asset ? (
        <div className={`upload-placeholder drop-zone ${dragging ? 'dragging' : ''}`} onDragOver={(event) => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={drop}>
          <div className="audio-icon"><AudioLines size={34} strokeWidth={1.5} /></div><h3>Arrastra tu audio aquí</h3><p>Tu podcast, entrevista o nota de voz.<br />Nos ocupamos de preparar la grabación.</p>
          <button className="primary-button" disabled={!config} onClick={() => input.current?.click()}>Subir una grabación<ArrowRight size={17} /></button>
          <span className="formats">WAV · FLAC · MP3 · M4A · OGG</span>
          {config && <span className="upload-limits">Hasta {formatSize(config.max_upload_bytes)} · {config.max_duration_seconds / 60} min · Mono o estéreo</span>}
        </div>
      ) : (
        <div className="recording">
          <div className="recording-heading"><div className="recording-title"><FileAudio size={21} /><div><h3>{asset.filename}</h3><p>{asset.format.toUpperCase()} · {formatSize(asset.size_bytes)}{asset.bitrate !== null ? ` · ${Math.round(asset.bitrate / 1000)} kbps` : ''}</p></div></div><button className="secondary-button" onClick={() => input.current?.click()} disabled={!config}><Upload size={14} />Cambiar audio</button></div>
          <div className="audio-metadata"><div><span>DURACIÓN</span><strong>{formatTime(asset.duration_seconds)}</strong></div><div><span>MUESTREO</span><strong>{asset.sample_rate / 1000} kHz</strong></div><div><span>CANALES</span><strong>{asset.channels === 1 ? 'Mono' : 'Estéreo'}</strong></div><div><span>CÓDEC</span><strong>{asset.codec}</strong></div></div>
          <AudioPlayer key={asset.id} asset={asset} />
          <AnalysisPanel key={`analysis-${asset.id}`} audioId={asset.id} />
        </div>
      )}
      <div className="privacy"><ShieldCheck size={17} /><span>Original sin modificar. {config ? `Borrado automático tras ${Math.round(config.retention_seconds / 3600)} h.` : 'Consultando conservación…'}</span></div>
    </section>
  );
}
