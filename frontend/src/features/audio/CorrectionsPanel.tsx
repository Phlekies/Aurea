import { useEffect, useRef, useState } from 'react';
import { LoaderCircle, Wrench } from 'lucide-react';
import { ApiError } from '../../api/client';
import { getProcessing, getProcessingPlan, processAudio, processedStreamUrl, type ProcessingMetrics, type ProcessingPlan, type ProcessingReport, type ProcessingStep } from '../../api/processing';
import { WavePlayer } from './AudioPlayer';
import { formatNumber as number } from './format';
import { diagnosticTitles, processorTitles } from './labels';
import { Measurements } from './Measurements';

const scalar = (value: ProcessingStep['parameters'][string]) => typeof value === 'number' ? value : 0;

function summary(step: ProcessingStep): string {
  const p = step.parameters;
  switch (step.processor) {
    case 'dc_removal': return `Desplazamiento ${number(Math.max(...(Array.isArray(p.offsets) ? p.offsets.map(Math.abs) : [0])), 4)}`;
    case 'high_pass': return `${number(scalar(p.cutoff_hz), 0)} Hz · ${scalar(p.order) * 6} dB/oct`;
    case 'dehum': return `${number(scalar(p.fundamental_hz), 0)} Hz · ${scalar(p.harmonics)} ${scalar(p.harmonics) === 1 ? 'línea' : 'líneas'} · −${number(scalar(p.attenuation_db), 0)} dB`;
    case 'pre_gain': return `${scalar(p.gain_db) > 0 ? '+' : ''}${number(scalar(p.gain_db))} dB`;
    default: return '';
  }
}

function sourceTitle(code: string): string {
  return code in diagnosticTitles ? diagnosticTitles[code as keyof typeof diagnosticTitles] : code;
}

function Step({ step, onToggle, disabled }: { step: ProcessingStep; onToggle: () => void; disabled: boolean }) {
  const title = processorTitles[step.processor] ?? step.processor;
  return <article className={`correction-step ${step.enabled ? 'on' : 'off'}`} aria-label={title}>
    <label className="correction-toggle">
      <input type="checkbox" checked={step.enabled} onChange={onToggle} disabled={disabled} />
      <span><strong>{title}</strong><small>{summary(step)}</small></span>
    </label>
    <p className="diagnostic-message">{step.reason}</p>
    <details className="diagnostic-details"><summary>¿Por qué?</summary>
      {step.source_diagnostic && <p className="diagnostic-confidence">Basado en el diagnóstico «{sourceTitle(step.source_diagnostic)}»{step.confidence !== null && ` · evidencia ${number(step.confidence * 100, 0)} %`}.</p>}
      <h6>Parámetros</h6><Measurements values={step.parameters} />
      {Object.keys(step.evidence).length > 0 && <><h6>Evidencia</h6><Measurements values={step.evidence} /></>}
    </details>
  </article>;
}

const rows: { label: string; unit: string; value: (metrics: ProcessingMetrics) => number | null; digits?: number }[] = [
  { label: 'Pico de muestra', unit: 'dBFS', value: (m) => m.peak_dbfs },
  { label: 'True peak', unit: 'dBTP', value: (m) => m.true_peak_dbtp },
  { label: 'Loudness integrado', unit: 'LUFS', value: (m) => m.integrated_lufs },
  { label: 'Nivel RMS', unit: 'dBFS', value: (m) => m.rms_dbfs },
  { label: 'Desplazamiento DC (máx.)', unit: '', value: (m) => Math.max(...m.dc_offset.map(Math.abs)), digits: 5 },
  { label: 'Energía en subgraves (<80 Hz)', unit: '%', value: (m) => m.subbass_percent },
  { label: 'Ruido de fondo', unit: 'dBFS', value: (m) => m.noise_rms_dbfs },
  { label: 'SNR aproximada', unit: 'dB', value: (m) => m.estimated_snr_db },
];

function problems(metrics: ProcessingMetrics): string {
  return metrics.detected.length ? metrics.detected.map(sourceTitle).join(', ') : 'Ninguno';
}

function Result({ report, revision }: { report: ProcessingReport; revision: number }) {
  const applied = report.steps.filter((step) => step.enabled).map((step) => processorTitles[step.processor] ?? step.processor);
  return <div className="correction-result">
    <WavePlayer key={revision} id={report.audio_id} resource="processed/waveform" src={`${processedStreamUrl(report.audio_id)}?r=${revision}`}
      duration={report.duration_seconds} label="Audio corregido" title="AUDIO CORREGIDO" controlSuffix="audio corregido" />
    <p className="analysis-note">{applied.length ? `Aplicado: ${applied.join(', ')}` : 'Sin correcciones activas: copia fiel del original'} · procesado en {number(report.processing_seconds, 2)} s ({number(report.real_time_factor, 3)}× tiempo real).</p>
    {report.warnings.map((warning) => <p key={warning} className="analysis-note correction-warning" role="status">{warning}</p>)}
    <table className="comparison-table">
      <caption>Antes y después de las correcciones</caption>
      <thead><tr><th scope="col">Medida</th><th scope="col">Antes</th><th scope="col">Después</th></tr></thead>
      <tbody>
        {rows.map((row) => <tr key={row.label}><th scope="row">{row.label}</th>
          {[report.before, report.after].map((metrics, index) => {
            const value = row.value(metrics);
            return <td key={index}>{value === null ? '—' : `${number(value, row.digits ?? 1)}${row.unit ? ` ${row.unit}` : ''}`}</td>;
          })}</tr>)}
        <tr><th scope="row">Problemas detectados</th><td>{problems(report.before)}</td><td>{problems(report.after)}</td></tr>
      </tbody>
    </table>
  </div>;
}

export function CorrectionsPanel({ audioId }: { audioId: string }) {
  const [plan, setPlan] = useState<ProcessingPlan | null>(null);
  const [report, setReport] = useState<ProcessingReport | null>(null);
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const controller = useRef<AbortController | null>(null);

  useEffect(() => {
    const request = new AbortController();
    Promise.allSettled([getProcessingPlan(audioId, request.signal), getProcessing(audioId, request.signal)]).then(([recommended, existing]) => {
      if (request.signal.aborted) return;
      if (existing.status === 'fulfilled') { setReport(existing.value); setPlan(existing.value.plan); }
      else if (recommended.status === 'fulfilled') setPlan(recommended.value);
      else setError('No se pudo preparar la cadena de correcciones. Vuelve a analizar la grabación.');
      if (existing.status === 'rejected' && !(existing.reason instanceof ApiError && existing.reason.status === 404)) {
        setError('No se pudo recuperar el resultado anterior. Puedes volver a aplicar las correcciones.');
      }
    });
    return () => { request.abort(); controller.current?.abort(); };
  }, [audioId]);

  async function apply() {
    if (!plan || busy) return;
    const request = new AbortController(); controller.current = request;
    setBusy(true); setError('');
    try {
      const value = await processAudio(audioId, plan, request.signal);
      if (!request.signal.aborted) { setReport(value); setRevision((current) => current + 1); }
    } catch (failure) {
      if (!request.signal.aborted) setError(failure instanceof ApiError ? failure.message : 'No se pudo aplicar las correcciones. Comprueba la conexión y reintenta.');
    } finally {
      if (controller.current === request) { controller.current = null; if (!request.signal.aborted) setBusy(false); }
    }
  }

  const active = plan?.steps.filter((step) => step.enabled).length ?? 0;
  return <section className="corrections-section" aria-labelledby="corrections-title" aria-busy={busy}>
    <div className="diagnostics-heading"><h4 id="corrections-title"><Wrench size={17} />Correcciones</h4>{plan && <span>Cadena v{plan.version}</span>}</div>
    {!plan && !error && <p className="analysis-note"><LoaderCircle size={14} className="loading-spinner" /> Preparando la cadena recomendada…</p>}
    {plan && <>
      <p className="diagnostics-summary">{active ? `${active} ${active === 1 ? 'corrección activa' : 'correcciones activas'} de ${plan.steps.length}.` : 'Ninguna corrección básica es necesaria según el diagnóstico.'}</p>
      <p className="diagnostics-explanation">Cada paso se decide a partir del diagnóstico y puedes activarlo o desactivarlo. El original nunca se modifica: se crea una versión corregida aparte.</p>
      <div className="diagnostic-list">{plan.steps.map((step, index) => <Step key={step.processor} step={step} disabled={busy}
        onToggle={() => setPlan({ ...plan, steps: plan.steps.map((item, position) => position === index ? { ...item, enabled: !item.enabled } : item) })} />)}</div>
      <div className="correction-actions">
        <button className="primary-button" disabled={busy} onClick={() => void apply()}>{busy ? <><LoaderCircle size={16} className="loading-spinner" />Procesando…</> : 'Aplicar correcciones'}</button>
        {busy && <span className="analysis-note" aria-live="polite">Filtrando y midiendo el resultado. Puedes seguir escuchando.</span>}
      </div>
    </>}
    {error && <div role="alert" className="analysis-error">{error}</div>}
    {report && <Result report={report} revision={revision} />}
  </section>;
}
