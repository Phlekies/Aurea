import { useEffect, useRef, useState } from 'react';
import { Activity, Download, LoaderCircle } from 'lucide-react';
import { analyzeAudio, getAnalysis, type AudioAnalysis } from '../../api/analysis';
import { ApiError } from '../../api/client';
import { formatNumber as number, formatTime } from './format';
import { ActivityPanel } from './ActivityPanel';
import { CorrectionsPanel } from './CorrectionsPanel';
import { DiagnosticsPanel } from './DiagnosticsPanel';

function Metric({ label, value, unit, description }: { label: string; value: number | null; unit: string; description: string }) {
  return <div className="metric-card"><span className="metric-label">{label}</span><div className="metric-value">{number(value)} <span className="metric-unit">{unit}</span></div><p>{description}</p></div>;
}

function SpectrumChart({ report }: { report: AudioAnalysis }) {
  const frequencies = report.spectrum.frequencies_hz;
  const values = report.spectrum.psd_dbfs_per_hz;
  const upper = report.sample_rate / 2;
  const measured = values.filter((value): value is number => value !== null);
  const ceiling = measured.length ? Math.ceil(Math.max(...measured) / 10) * 10 : 0;
  const floor = ceiling - 90;
  const x = (frequency: number) => 45 + Math.log10(Math.max(20, frequency) / 20) / Math.log10(upper / 20) * 540;
  const y = (value: number) => 15 + (ceiling - Math.max(floor, value)) / 90 * 140;
  const line = (hz: number[], db: (number | null)[]) => hz.flatMap((frequency, index) => frequency < 20 || frequency > upper || db[index] === null ? [] : [`${x(frequency).toFixed(2)},${y(db[index]!).toFixed(2)}`]).join(' ');
  const points = line(frequencies, values);
  const noise = line(report.noise_profile.frequencies_hz, report.noise_profile.psd_dbfs_per_hz);
  return <svg className="spectrum-chart" viewBox="0 0 610 190" role="img" aria-label={`Densidad espectral promedio${noise ? ' y perfil del ruido de fondo' : ''}: energía por frecuencia, escala horizontal logarítmica`}>
    {[0, 30, 60, 90].map((offset) => <g key={offset}><line className="chart-grid" x1="45" x2="585" y1={y(ceiling - offset)} y2={y(ceiling - offset)} /><text className="chart-axis" x="36" y={y(ceiling - offset) + 4} textAnchor="end">{ceiling - offset}</text></g>)}
    {[20, 100, 1000, 10000, upper].filter((frequency, index, items) => frequency <= upper && items.indexOf(frequency) === index).map((frequency) => <text className="chart-axis" key={frequency} x={x(frequency)} y="176" textAnchor="middle">{frequency >= 1000 ? `${number(frequency / 1000, 0)}k` : frequency}</text>)}
    {noise && <polyline className="chart-line noise" points={noise} fill="none" />}
    {points && <polyline className="chart-line" points={points} fill="none" />}
  </svg>;
}

function DynamicsChart({ report }: { report: AudioAnalysis }) {
  const x = (seconds: number) => 45 + seconds / report.duration_seconds * 540;
  const y = (value: number | null) => 15 + Math.min(60, Math.max(0, -(value ?? -60))) / 60 * 125;
  const series = (key: 'peak_dbfs' | 'rms_dbfs') => {
    const points = report.dynamics.points;
    const last = points[points.length - 1];
    return [...points.map((point) => `${x(point.start_seconds).toFixed(2)},${y(point[key]).toFixed(2)}`), `${x(last.start_seconds + last.duration_seconds).toFixed(2)},${y(last[key]).toFixed(2)}`].join(' ');
  };
  return <svg className="dynamics-chart" viewBox="0 0 610 180" role="img" aria-label="Evolución de los niveles: pico en cian y RMS en turquesa, en dBFS">
    {[0, -20, -40, -60].map((level) => <g key={level}><line className="chart-grid" x1="45" x2="585" y1={y(level)} y2={y(level)} /><text className="chart-axis" x="36" y={y(level) + 4} textAnchor="end">{level}</text></g>)}
    {[0, .5, 1].map((fraction) => <text className="chart-axis" key={fraction} x={x(fraction * report.duration_seconds)} y="163" textAnchor="middle">{formatTime(fraction * report.duration_seconds)}</text>)}
    <polyline className="chart-line" points={series('peak_dbfs')} fill="none" />
    <polyline className="chart-line secondary" points={series('rms_dbfs')} fill="none" />
  </svg>;
}

function downloadReport(report: AudioAnalysis) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a');
  link.href = url; link.download = `aurea-analysis-${report.audio_id}.json`; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function AnalysisPanel({ audioId }: { audioId: string }) {
  const [report, setReport] = useState<AudioAnalysis | null>(null);
  const [busy, setBusy] = useState(false);
  const [checking, setChecking] = useState(true);
  const [error, setError] = useState('');
  const controller = useRef<AbortController | null>(null);

  useEffect(() => {
    const request = new AbortController();
    getAnalysis(audioId, request.signal).then((value) => { if (!request.signal.aborted) setReport(value); }).catch((failure: unknown) => {
      if (!request.signal.aborted && !(failure instanceof ApiError && failure.status === 404)) setError('No se pudo recuperar el informe. Puedes volver a analizar la grabación.');
    }).finally(() => { if (!request.signal.aborted) setChecking(false); });
    return () => { request.abort(); controller.current?.abort(); };
  }, [audioId]);

  async function analyze() {
    if (busy) return;
    const request = new AbortController(); controller.current = request;
    setBusy(true); setError('');
    try {
      const value = await analyzeAudio(audioId, request.signal);
      if (!request.signal.aborted) setReport(value);
    } catch (failure) {
      if (!request.signal.aborted) setError(failure instanceof ApiError ? failure.message : 'No se pudo completar el análisis. Comprueba la conexión y reintenta.');
    } finally {
      if (controller.current === request) { controller.current = null; if (!request.signal.aborted) setBusy(false); }
    }
  }

  return <section className="analysis-section" aria-labelledby="analysis-title" aria-busy={busy || checking}>
    <div className="analysis-heading"><div><h3 id="analysis-title"><Activity size={17} />Informe de audio</h3><p>{report ? 'Mediciones, diagnóstico y actividad de voz de tu grabación original.' : 'Mide la señal, separa la voz del fondo y detecta posibles problemas.'}</p></div>
      {report ? <button className="secondary-button" onClick={() => downloadReport(report)}><Download size={14} />Descargar informe</button> : <button className="primary-button" disabled={busy || checking} onClick={() => void analyze()}>{busy ? <><LoaderCircle size={16} className="loading-spinner" />Analizando…</> : 'Analizar grabación'}</button>}
    </div>
    {busy && <p className="analysis-note" aria-live="polite">Calculando las métricas. Puedes seguir escuchando tu audio.</p>}
    {error && <div role="alert" className="analysis-error">{error}</div>}
    {report && <>
      <DiagnosticsPanel diagnostics={report.diagnostics} />
      <ActivityPanel report={report} />
      <CorrectionsPanel audioId={report.audio_id} />
      <div className="analysis-grid">
        <Metric label="Loudness integrado" value={report.integrated_lufs} unit="LUFS" description="Nivel percibido a lo largo de la grabación." />
        <Metric label="True peak" value={report.true_peak_dbtp} unit="dBTP" description="Pico estimado entre las muestras digitales." />
        <Metric label="Pico de muestra" value={report.peak_dbfs} unit="dBFS" description="El punto más alto de la señal." />
        <Metric label="Nivel RMS" value={report.rms_dbfs} unit="dBFS" description="Energía media del audio." />
        <Metric label="Crest factor" value={report.crest_factor_db} unit="dB" description="Diferencia entre el pico y el nivel RMS." />
        <Metric label="Silencio" value={report.silence_percent} unit="%" description={`Tiempo bajo ${number(report.silence_threshold_dbfs, 0)} dBFS en ambos canales.`} />
      </div>
      {(report.integrated_lufs === null || report.peak_dbfs === null) && <p className="analysis-note">— indica una medición no definida: silencio o audio insuficiente para medir loudness.</p>}
      <div className="analysis-charts"><div><h4>Espectro promedio <span>PSD · dBFS/Hz</span>{report.noise_profile.frame_count > 0 && <span className="chart-legend"><i className="legend-swatch" />Espectro <i className="legend-swatch noise" />Fondo</span>}</h4><SpectrumChart report={report} /></div><div><h4>Dinámica temporal <span>Pico / RMS · dBFS</span></h4><DynamicsChart report={report} /><p className="analysis-note">Ventanas de {number(report.dynamics.window_ms, 0)} ms. Vista limitada a −60 dBFS; el informe conserva los valores completos.</p></div></div>
      <details className="analysis-details"><summary>Bandas de frecuencia y detalles técnicos</summary>
        <div className="band-list">{report.bands.map((band) => <div className="band-item" key={band.name}><span>{band.name} <small>{number(band.low_hz, 0)}–{number(band.high_hz, 0)} Hz</small></span><div className="band-bar"><span style={{ width: `${band.percent}%` }} /></div><strong>{number(band.percent)} %</strong></div>)}</div>
        <p className="analysis-note">DC offset por canal: {report.dc_offset.map((offset) => number(offset, 6)).join(' / ')}. Cruces por cero: {number(report.zero_crossing_rate * report.sample_rate, 1)}/s por canal.</p>
      </details>
      <p className="analysis-note">El análisis conserva tu grabación original. Siguiente: reducción de ruido.</p>
    </>}
  </section>;
}
