import { useEffect, useRef, useState } from 'react';
import { CheckCircle2, Download, LoaderCircle, SlidersHorizontal } from 'lucide-react';
import { ApiError } from '../../api/client';
import { downloadMaster, getMastering, getMasteringPresets, masterAudio, masteredStreamUrl, qcTitles, type MasteringPreset, type MasteringReport } from '../../api/mastering';
import { WavePlayer } from './AudioPlayer';
import { formatNumber as number } from './format';

function save(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a'); link.href = url; link.download = filename; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

const rows: { key: keyof Pick<MasteringReport['after'], 'integrated_lufs' | 'true_peak_dbtp' | 'momentary_max_lufs' | 'short_term_max_lufs' | 'loudness_range_lu'>; label: string; unit: string }[] = [
  { key: 'integrated_lufs', label: 'Loudness integrado', unit: 'LUFS' },
  { key: 'true_peak_dbtp', label: 'True peak', unit: 'dBTP' },
  { key: 'momentary_max_lufs', label: 'Máximo momentáneo (400 ms)', unit: 'LUFS' },
  { key: 'short_term_max_lufs', label: 'Máximo a corto plazo (3 s)', unit: 'LUFS' },
  { key: 'loudness_range_lu', label: 'Rango de loudness (LRA)', unit: 'LU' },
];

function LoudnessChart({ report }: { report: MasteringReport }) {
  const points = report.after.points;
  if (!points.some((p) => p.momentary_lufs !== null)) return null;
  const stride = Math.max(1, Math.ceil(points.length / 400));
  const sampled = points.filter((_, i) => i % stride === 0 || i === points.length - 1);
  const x = (time: number) => 42 + time / report.duration_seconds * 478;
  const y = (level: number) => 16 + (Math.min(0, Math.max(-60, level)) / -60) * 112;
  return <figure className="loudness-chart">
    <figcaption>Loudness del máster en el tiempo <span><i className="legend-m" />Momentáneo <i className="legend-s" />Corto plazo</span></figcaption>
    <svg viewBox="0 0 536 152" role="img" aria-label="Evolución del loudness momentáneo y a corto plazo del máster">
      {[-12, -24, -36, -48, -60].map((level) => <g key={level}><line x1="42" x2="520" y1={y(level)} y2={y(level)} className="loudness-grid" /><text x="4" y={y(level) + 3}>{level}</text></g>)}
      <line x1="42" x2="520" y1={y(report.preset.target_lufs)} y2={y(report.preset.target_lufs)} className="loudness-target" />
      {(['momentary_lufs', 'short_term_lufs'] as const).map((key) => {
        const segments: string[] = []; let segment = '';
        sampled.forEach((p) => { if (p[key] !== null) segment += `${x(p.time_seconds).toFixed(1)},${y(p[key]).toFixed(1)} `; else if (segment) { segments.push(segment); segment = ''; } });
        if (segment) segments.push(segment);
        return segments.map((points, i) => <polyline key={`${key}-${i}`} points={points} className={key === 'momentary_lufs' ? 'loudness-m' : 'loudness-s'} />);
      })}
      <text x="42" y="146">0 s</text><text x="520" y="146" textAnchor="end">{number(report.duration_seconds, 1)} s</text>
    </svg>
    <p className="analysis-note">LUFS · Línea discontinua: objetivo. Los primeros 400 ms / 3 s necesitan completar su ventana de medida.</p>
  </figure>;
}

export function MasteringPanel({ audioId, correctionsBusy = false, initialReport, defaultPreset = 'podcast_standard' }: { audioId: string; correctionsBusy?: boolean; initialReport?: MasteringReport; defaultPreset?: string }) {
  const [presets, setPresets] = useState<MasteringPreset[]>([]);
  const [selected, setSelected] = useState(initialReport?.preset.id ?? defaultPreset);
  const [report, setReport] = useState<MasteringReport | null>(initialReport ?? null);
  const [busy, setBusy] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState('');
  const controller = useRef<AbortController | null>(null);
  useEffect(() => {
    const request = new AbortController();
    Promise.allSettled([getMasteringPresets(request.signal), getMastering(audioId, request.signal)]).then(([choices, previous]) => {
      if (request.signal.aborted) return;
      if (choices.status === 'fulfilled') {
        setPresets(choices.value);
        if (!choices.value.some((p) => p.id === 'podcast_standard')) setSelected(choices.value[0].id);
      } else setError('No se pudieron cargar los objetivos de masterización. Recarga la grabación.');
      if (previous.status === 'fulfilled') { setReport(previous.value); setSelected(previous.value.preset.id); }
      else if (!(previous.reason instanceof ApiError && previous.reason.status === 404)) setError('No se pudo recuperar el máster anterior. Puedes volver a crearlo.');
    });
    return () => { request.abort(); controller.current?.abort(); };
  }, [audioId]);

  async function apply() {
    if (busy || correctionsBusy) return;
    const request = new AbortController(); controller.current = request;
    setBusy(true); setError('');
    try {
      const result = await masterAudio(audioId, selected, request.signal);
      if (!request.signal.aborted) setReport(result);
    } catch (failure) {
      if (!request.signal.aborted) setError(failure instanceof ApiError ? failure.message : 'No se pudo masterizar. Comprueba la conexión y reintenta.');
    } finally {
      if (controller.current === request) { controller.current = null; if (!request.signal.aborted) setBusy(false); }
    }
  }

  async function download() {
    if (!report || downloading || busy || correctionsBusy) return;
    const request = new AbortController(); controller.current = request;
    setDownloading(true); setError('');
    try {
      const blob = await downloadMaster(audioId, request.signal);
      if (!request.signal.aborted) save(blob, `aurea-master-${audioId}.wav`);
    } catch (failure) {
      if (!request.signal.aborted) setError(failure instanceof ApiError ? failure.message : 'No se pudo descargar el audio. Reintenta.');
    } finally {
      if (controller.current === request) { controller.current = null; if (!request.signal.aborted) setDownloading(false); }
    }
  }

  const preset = presets.find((p) => p.id === selected);
  const disabled = busy || downloading || correctionsBusy;
  return <section className="mastering-section" aria-labelledby="mastering-title" aria-busy={busy}>
    <div className="diagnostics-heading"><h4 id="mastering-title"><SlidersHorizontal size={17} />Masterización</h4><span>Salida final · WAV 24 bits</span></div>
    <p className="diagnostics-explanation">Ajusta el loudness del audio corregido y controla los picos entre muestras. La descarga se habilita después de comprobar el archivo final.</p>
    {presets.length > 0 && <div className="noise-controls mastering-controls">
      <label>Objetivo de publicación<select value={selected} disabled={disabled} onChange={(event) => setSelected(event.target.value)}>{presets.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
      {preset && <p className="mastering-target">{number(preset.target_lufs, 0)} LUFS <span>· True peak ≤ {number(preset.max_true_peak_dbtp, 1)} dBTP · Tolerancia ±{number(preset.loudness_tolerance_lu, 1)} LU</span></p>}
      <p className="analysis-note">Elige el objetivo que necesite tu plataforma. Un archivo silencioso o demasiado corto no permite una normalización fiable.</p>
    </div>}
    <div className="correction-actions"><button className="primary-button" disabled={disabled || !preset} onClick={() => void apply()}>{busy ? <><LoaderCircle size={16} className="loading-spinner" />Masterizando y verificando…</> : 'Crear máster'}</button></div>
    {error && <div role="alert" className="analysis-error">{error}</div>}
    {report && <div className="mastering-result">
      <p className="mastering-passed" role="status"><CheckCircle2 size={18} />Control de calidad aprobado · {report.preset.name}</p>
      {selected !== report.preset.id && <p className="analysis-note">El resultado corresponde a {report.preset.name}. Pulsa «Crear máster» para aplicar el nuevo objetivo.</p>}
      <WavePlayer key={report.output_sha256} id={audioId} resource="mastered/waveform" src={`${masteredStreamUrl(audioId)}?r=${report.output_sha256}`} duration={report.duration_seconds} label="Máster final" title="MÁSTER FINAL" controlSuffix="máster final" />
      <div className="mastering-downloads">
        <button className="primary-button" disabled={disabled} onClick={() => void download()}><Download size={15} />{downloading ? 'Verificando descarga…' : 'Descargar máster WAV'}</button>
        <button className="secondary-button" onClick={() => save(new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' }), `aurea-mastering-${audioId}.json`)}><Download size={14} />Informe del máster</button>
      </div>
      <p className="analysis-note">WAV PCM · {report.bit_depth} bits · {number(report.sample_rate / 1000, 1)} kHz · {report.channels === 1 ? 'Mono' : 'Estéreo'} · {report.normalization_mode === 'linear' ? 'Ganancia lineal' : 'Normalización dinámica con limitador'} · {number(report.processing_seconds, 2)} s de procesado.</p>
      <table className="comparison-table"><caption>Antes y después de masterizar</caption><thead><tr><th scope="col">Medida</th><th scope="col">Corregido</th><th scope="col">Máster</th></tr></thead><tbody>{rows.map((row) => <tr key={row.key}><th scope="row">{row.label}</th>{[report.before, report.after].map((m, i) => <td key={i}>{m[row.key] === null ? '—' : `${number(m[row.key], 1)} ${row.unit}`}</td>)}</tr>)}</tbody></table>
      {!report.after.lra_stable && <p className="analysis-note">En grabaciones de menos de 60 s, el LRA puede ser inestable; en menos de 3 s no se informa.</p>}
      <LoudnessChart report={report} />
      <details className="diagnostic-details"><summary>Ver las 8 comprobaciones de calidad</summary><ul className="mastering-checks">{report.qc.checks.map((check) => <li key={check.code}><CheckCircle2 size={14} /><span><strong>{qcTitles[check.code]}</strong><small>{check.expected}</small></span></li>)}</ul><p className="analysis-note">Estos controles evitan defectos en la salida. El daño de una saturación presente en el original puede seguir siendo audible.</p></details>
    </div>}
  </section>;
}
