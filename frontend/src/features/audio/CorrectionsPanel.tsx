import { useEffect, useRef, useState } from 'react';
import { Download, LoaderCircle, Wrench } from 'lucide-react';
import { ApiError } from '../../api/client';
import { automaticallyProcessAudio, getProcessing, getProcessingPlan, getProcessingPresets, processAudio, processedStreamUrl, type ProcessingMetrics, type ProcessingPlan, type ProcessingPreset, type ProcessingReport, type ProcessingStep } from '../../api/processing';
import { getMasteringPresets, type MasteringPreset, type MasteringReport } from '../../api/mastering';
import { WavePlayer } from './AudioPlayer';
import { formatNumber as number } from './format';
import { diagnosticTitles, processorTitles } from './labels';
import { Measurements } from './Measurements';
import { MasteringPanel } from './MasteringPanel';

const scalar = (value: ProcessingStep['parameters'][string]) => typeof value === 'number' ? value : 0;
const algorithms: Record<string, string> = { wiener: 'Filtro de Wiener', spectral_subtraction: 'Sustracción espectral', spectral_gate: 'Puerta espectral' };
const strengths: Record<string, string> = { light: 'Suave', balanced: 'Equilibrado', strong: 'Intenso' };
const decisions = { automatic: 'Automático', recommended: 'Recomendado · revisar', disabled: 'Desactivado', manual: 'Ajuste manual' };

function summary(step: Pick<ProcessingStep, 'processor' | 'parameters'>): string {
  const p = step.parameters;
  switch (step.processor) {
    case 'dc_removal': return `Desplazamiento ${number(Math.max(...(Array.isArray(p.offsets) ? p.offsets.map(Math.abs) : [0])), 4)}`;
    case 'high_pass': return `${number(scalar(p.cutoff_hz), 0)} Hz · ${scalar(p.order) * 6} dB/oct`;
    case 'dehum': return `${number(scalar(p.fundamental_hz), 0)} Hz · ${scalar(p.harmonics)} ${scalar(p.harmonics) === 1 ? 'línea' : 'líneas'} · −${number(scalar(p.attenuation_db), 0)} dB`;
    case 'pre_gain': return `${scalar(p.gain_db) > 0 ? '+' : ''}${number(scalar(p.gain_db))} dB`;
    case 'noise_reduction': return `${algorithms[String(p.algorithm)]} · ${strengths[String(p.strength)]}`;
    case 'speech_leveler': return `Objetivo ${number(scalar(p.target_rms_dbfs), 0)} dBFS · aumento hasta ${number(scalar(p.max_boost_db), 0)} dB`;
    case 'compressor': return `${number(scalar(p.threshold_dbfs), 0)} dBFS · ${number(scalar(p.ratio), 1)}:1`;
    default: return '';
  }
}

function sourceTitle(code: string): string {
  return code in diagnosticTitles ? diagnosticTitles[code as keyof typeof diagnosticTitles] : code;
}

type Setting = { key: string; label: string; min: number; max: number; step: number };
const settings: Record<string, Setting[]> = {
  speech_leveler: [
    { key: 'target_rms_dbfs', label: 'Objetivo de voz (dBFS)', min: -40, max: -12, step: 1 },
    { key: 'max_boost_db', label: 'Aumento máximo (dB)', min: 0, max: 12, step: .5 },
  ],
  compressor: [
    { key: 'threshold_dbfs', label: 'Umbral (dBFS)', min: -60, max: 0, step: 1 },
    { key: 'ratio', label: 'Relación de compresión (:1)', min: 1, max: 20, step: .1 },
    { key: 'knee_db', label: 'Transición suave (dB)', min: 0, max: 24, step: 1 },
    { key: 'attack_ms', label: 'Ataque (ms)', min: .1, max: 200, step: .1 },
    { key: 'release_ms', label: 'Recuperación (ms)', min: 10, max: 2000, step: 10 },
    { key: 'makeup_gain_db', label: 'Compensación (dB)', min: -12, max: 12, step: .5 },
  ],
};

function NumericSetting({ setting, value, disabled, onChange }: { setting: Setting; value: number; disabled: boolean; onChange: (value: number) => void }) {
  const [text, setText] = useState(String(value));
  function edit(raw: string) {
    setText(raw);
    const next = Number(raw);
    if (raw !== '' && Number.isFinite(next) && next >= setting.min && next <= setting.max) onChange(next);
  }
  function finish() {
    const next = text === '' || !Number.isFinite(Number(text)) ? value : Math.min(setting.max, Math.max(setting.min, Number(text)));
    setText(String(next)); onChange(next);
  }
  return <label>{setting.label}<input type="number" value={text} disabled={disabled} min={setting.min} max={setting.max} step={setting.step}
    onChange={(event) => edit(event.target.value)} onBlur={finish} /></label>;
}

function Step({ step, onToggle, onParameter, disabled }: { step: ProcessingStep; onToggle: () => void; onParameter: (key: string, value: number) => void; disabled: boolean }) {
  const title = processorTitles[step.processor] ?? step.processor;
  return <article className={`correction-step ${step.enabled ? 'on' : 'off'}`} aria-label={title}>
    <label className="correction-toggle">
      <input type="checkbox" checked={step.enabled} onChange={onToggle} disabled={disabled} />
      <span><strong>{title}</strong><small>{summary(step)}</small></span>
    </label>
    <span className={`decision-badge ${step.decision}`}>{decisions[step.decision]}</span>
    <p className="diagnostic-message">{step.reason}</p>
    {settings[step.processor] && <details className="dynamics-settings"><summary>Ajustes de {step.processor === 'speech_leveler' ? 'nivelado' : 'compresión'}</summary>
      <div className="dynamics-controls">{settings[step.processor].map((setting) => <NumericSetting key={setting.key} setting={setting}
        value={scalar(step.parameters[setting.key])} disabled={disabled} onChange={(value) => onParameter(setting.key, value)} />)}</div>
      <p className="analysis-note">{step.processor === 'speech_leveler' ? 'El nivelado cambia suavemente el volumen de los tramos de voz y protege las pausas.' : 'El compresor reduce los picos por encima del umbral. Una relación mayor reduce más las diferencias de volumen.'} Vuelve a aplicar las correcciones después de ajustar.</p>
    </details>}
    <details className="diagnostic-details"><summary>¿Por qué?</summary>
      {step.source_diagnostic && <p className="diagnostic-confidence">Basado en el diagnóstico «{sourceTitle(step.source_diagnostic)}»{step.confidence !== null && ` · evidencia ${number(step.confidence * 100, 0)} %`}.</p>}
      {!step.source_diagnostic && step.confidence !== null && <p className="diagnostic-confidence">Evidencia de voz: {number(step.confidence * 100, 0)} % · estimación heurística.</p>}
      <h6>Parámetros</h6><Measurements values={Object.fromEntries(Object.entries(step.parameters).filter(([key]) => !key.startsWith('noise_') && !['speech_starts_seconds', 'speech_ends_seconds'].includes(key)).map(([key, value]) => [key, key === 'algorithm' ? algorithms[String(value)] : key === 'strength' ? strengths[String(value)] : value]))} />
      {step.processor === 'noise_reduction' && <p className="analysis-note">Se utiliza el perfil espectral del fondo de esta grabación, ajustado por los filtros anteriores.</p>}
      {step.processor === 'speech_leveler' && <p className="analysis-note">Se utilizan {Array.isArray(step.parameters.speech_starts_seconds) ? step.parameters.speech_starts_seconds.length : 0} tramos de actividad compatible con voz para orientar los cambios de volumen.</p>}
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

function downloadReport(report: ProcessingReport) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a');
  link.href = url; link.download = `aurea-processing-${report.audio_id}.json`; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function Result({ report, revision }: { report: ProcessingReport; revision: number }) {
  const applied = report.steps.filter((step) => step.enabled).map((step) => ['noise_reduction', 'speech_leveler', 'compressor'].includes(step.processor) ? `${processorTitles[step.processor]} (${summary(step)})` : processorTitles[step.processor] ?? step.processor);
  return <div className="correction-result">
    <p className="analysis-note">Resultado aplicado: {report.plan.preset === 'natural' ? 'Natural' : report.plan.preset === 'studio' ? 'Studio' : report.plan.preset === 'balanced' ? 'Balanced' : report.plan.preset} · configuración {report.plan.preset_version}.</p>
    <WavePlayer key={revision} id={report.audio_id} resource="processed/waveform" src={`${processedStreamUrl(report.audio_id)}?r=${revision}`}
      duration={report.duration_seconds} label="Audio corregido" title="AUDIO CORREGIDO" controlSuffix="audio corregido" />
    <p className="analysis-note">{applied.length ? `Aplicado: ${applied.join(', ')}` : 'Sin correcciones activas: copia fiel del original'} · procesado en {number(report.processing_seconds, 2)} s ({number(report.real_time_factor, 3)}× tiempo real).</p>
    {report.warnings.map((warning) => <p key={warning} className="analysis-note correction-warning" role="status">{warning}</p>)}
    {report.gain_envelopes.length > 0 && <div className="gain-summary" aria-label="Ganancia aplicada a la voz">
      <h5>Cambios de volumen aplicados</h5>
      {report.gain_envelopes.map((curve) => <p key={curve.step_index}>{processorTitles[curve.processor] ?? curve.processor}: de {number(Math.min(...curve.gain_db), 1)} a {number(Math.max(...curve.gain_db), 1)} dB.</p>)}
      <p className="analysis-note">El informe conserva las curvas completas de cada ajuste. La reducción global de seguridad se registra por separado{report.safety_gain_db < 0 ? `: ${number(report.safety_gain_db, 1)} dB` : '.'}</p>
    </div>}
    <div className="analysis-actions"><button className="secondary-button" onClick={() => downloadReport(report)}><Download size={14} />Descargar informe de correcciones</button></div>
    {report.artifacts && <div className="noise-quality" aria-label="Comprobaciones de reducción de ruido">
      <p>Fondo reducido: {report.artifacts.background_reduction_db === null ? 'sin datos suficientes' : `${number(report.artifacts.background_reduction_db, 1)} dB`} · Pérdida de energía en regiones de voz: {report.artifacts.speech_energy_loss_db === null ? 'sin datos suficientes' : `${number(report.artifacts.speech_energy_loss_db, 1)} dB`}.</p>
      <p className="analysis-note">Ruido musical: {report.artifacts.musical_noise_score === null ? 'sin intervalos suficientes para comprobarlo' : report.artifacts.possible_musical_noise ? 'posibles indicios' : 'sin aumento significativo de picos aislados'}. Son indicadores aproximados de la cadena completa. Escucha el resultado para valorar la voz.</p>
    </div>}
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
  const [choosing, setChoosing] = useState(false);
  const [presets, setPresets] = useState<ProcessingPreset[]>([]);
  const [targets, setTargets] = useState<MasteringPreset[]>([]);
  const [master, setMaster] = useState<MasteringReport | undefined>();
  const [error, setError] = useState('');
  const controller = useRef<AbortController | null>(null);
  const selectionController = useRef<AbortController | null>(null);

  useEffect(() => {
    const request = new AbortController();
    Promise.allSettled([getProcessingPlan(audioId, request.signal), getProcessing(audioId, request.signal), getProcessingPresets(request.signal), getMasteringPresets(request.signal)]).then(([recommended, existing, choices, goals]) => {
      if (request.signal.aborted) return;
      if (existing.status === 'fulfilled') { setReport(existing.value); setPlan(existing.value.plan); }
      else if (recommended.status === 'fulfilled') setPlan(recommended.value);
      else setError('No se pudo preparar la cadena de correcciones. Vuelve a analizar la grabación.');
      if (choices.status === 'fulfilled') setPresets(choices.value);
      if (goals.status === 'fulfilled') setTargets(goals.value);
      if (choices.status === 'rejected' || goals.status === 'rejected') setError('No se pudieron cargar los presets. Recarga la grabación para elegir otro.');
      if (existing.status === 'rejected' && !(existing.reason instanceof ApiError && existing.reason.status === 404)) {
        setError('No se pudo recuperar el resultado anterior. Puedes volver a aplicar las correcciones.');
      }
    });
    return () => { request.abort(); controller.current?.abort(); selectionController.current?.abort(); };
  }, [audioId]);

  async function choose(preset: string, target: string, keepCorrections: boolean) {
    if (!plan || busy) return;
    selectionController.current?.abort();
    const request = new AbortController(); selectionController.current = request;
    setChoosing(true); setError('');
    try {
      const next = await getProcessingPlan(audioId, request.signal, preset, target);
      if (!request.signal.aborted) setPlan(keepCorrections ? { ...plan, mastering_preset: next.mastering_preset, mastering_steps: next.mastering_steps } : next);
    } catch (failure) {
      if (!request.signal.aborted) setError(failure instanceof ApiError ? failure.message : 'No se pudo preparar el preset. Reintenta.');
    } finally {
      if (selectionController.current === request) { selectionController.current = null; if (!request.signal.aborted) setChoosing(false); }
    }
  }

  async function apply(automatic = false) {
    if (!plan || busy || choosing) return;
    const request = new AbortController(); controller.current = request;
    setBusy(true); setError('');
    try {
      if (automatic) {
        const value = await automaticallyProcessAudio(audioId, plan, request.signal);
        if (!request.signal.aborted) { setReport(value.processing); setMaster(value.mastering); setRevision((current) => current + 1); }
      } else {
        const value = await processAudio(audioId, plan, request.signal);
        if (!request.signal.aborted) { setReport(value); setMaster(undefined); setRevision((current) => current + 1); }
      }
    } catch (failure) {
      if (!request.signal.aborted) setError(failure instanceof ApiError ? failure.message : 'No se pudo aplicar las correcciones. Comprueba la conexión y reintenta.');
      if (automatic && !request.signal.aborted) {
        // Mastering can fail after corrections have been published. Recover that stage
        // so the user can listen and retry mastering without losing the usable result.
        try {
          const corrected = await getProcessing(audioId, request.signal);
          if (!request.signal.aborted) { setReport(corrected); setMaster(undefined); setRevision((current) => current + 1); }
        } catch { /* The original and any previous visible report remain available. */ }
      }
    } finally {
      if (controller.current === request) { controller.current = null; if (!request.signal.aborted) setBusy(false); }
    }
  }

  const active = plan?.steps.filter((step) => step.enabled).length ?? 0;
  const noise = plan?.steps.find((step) => step.processor === 'noise_reduction');
  function changeNoise(key: string, value: string) {
    if (!plan) return;
    setPlan({ ...plan, steps: plan.steps.map((step) => step.processor === 'noise_reduction' ? { ...step, decision: 'manual', parameters: { ...step.parameters, [key]: value } } : step) });
  }
  return <section className="corrections-section" aria-labelledby="corrections-title" aria-busy={busy}>
    <div className="diagnostics-heading"><h4 id="corrections-title"><Wrench size={17} />Correcciones</h4>{plan && <span>Cadena v{plan.version}</span>}</div>
    {!plan && !error && <p className="analysis-note"><LoaderCircle size={14} className="loading-spinner" /> Preparando la cadena recomendada…</p>}
    {plan && <>
      <p className="diagnostics-summary">{active ? `${active} ${active === 1 ? 'corrección activa' : 'correcciones activas'} de ${plan.steps.length}.` : 'Ninguna corrección básica es necesaria según el diagnóstico.'}</p>
      <p className="diagnostics-explanation">Cada paso se decide a partir del diagnóstico y puedes activarlo o desactivarlo. El original nunca se modifica: se crea una versión corregida aparte.</p>
      <div className="noise-controls decision-controls">
        <label>Preset de procesado<select aria-label="Preset de procesado" value={plan.preset} disabled={busy || choosing || !presets.length} onChange={(event) => void choose(event.target.value, plan.mastering_preset, false)}>{presets.map((preset) => <option key={preset.id} value={preset.id}>{preset.name}</option>)}</select></label>
        <label>Objetivo final<select aria-label="Objetivo final" value={plan.mastering_preset} disabled={busy || choosing || !targets.length} onChange={(event) => void choose(plan.preset, event.target.value, true)}>{targets.map((target) => <option key={target.id} value={target.id}>{target.name} · {target.target_lufs} LUFS</option>)}</select></label>
        <p className="analysis-note">{presets.find((preset) => preset.id === plan.preset)?.description} Cambiar el preset prepara una propuesta nueva y sustituye tus ajustes. Los pasos recomendados esperan tu revisión; la evidencia es heurística.</p>
      </div>
      {noise && <div className="noise-controls">
        <label>Método de reducción<select aria-label="Método de reducción" value={String(noise.parameters.algorithm)} disabled={busy || choosing} onChange={(event) => changeNoise('algorithm', event.target.value)}>{Object.entries(algorithms).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label>Intensidad<select aria-label="Intensidad de reducción" value={String(noise.parameters.strength)} disabled={busy || choosing} onChange={(event) => changeNoise('strength', event.target.value)}>{Object.entries(strengths).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <p className="analysis-note">Suave conserva más ambiente; intenso elimina más fondo y puede afectar la voz. Activa el paso para probarlo y vuelve a aplicar las correcciones al cambiar de opción.</p>
      </div>}
      <div className="diagnostic-list">{plan.steps.map((step, index) => <Step key={`${plan.preset}-${plan.preset_version}-${step.processor}`} step={step} disabled={busy || choosing || (step.processor === 'noise_reduction' && step.evidence.profile_available === false)}
        onParameter={(key, value) => setPlan({ ...plan, steps: plan.steps.map((item, position) => position === index ? { ...item, decision: 'manual', parameters: { ...item.parameters, [key]: value } } : item) })}
        onToggle={() => setPlan({ ...plan, steps: plan.steps.map((item, position) => position === index ? { ...item, decision: 'manual', enabled: !item.enabled } : item) })} />)}</div>
      {plan.mastering_steps.length > 0 && <div className="decision-mastering" aria-label="Decisiones de masterización"><strong>Últimos pasos · máster verificado</strong>{plan.mastering_steps.map((step) => <p key={step.processor}>{step.reason}</p>)}</div>}
      <div className="correction-actions">
        <button className="primary-button" disabled={busy || choosing || plan.mastering_steps.length !== 2 || plan.mastering_steps.some((step) => !step.enabled)} onClick={() => void apply(true)}>{busy ? <><LoaderCircle size={16} className="loading-spinner" />Procesando…</> : 'Procesar y crear máster'}</button>
        <button className="secondary-button" disabled={busy || choosing} onClick={() => void apply()}>Aplicar correcciones</button>
        {(busy || choosing) && <span className="analysis-note" aria-live="polite">{choosing ? 'Preparando la propuesta…' : 'Procesando y comprobando el resultado. Puedes seguir escuchando.'}</span>}
      </div>
    </>}
    {error && <div role="alert" className="analysis-error">{error}</div>}
    {report && <><Result report={report} revision={revision} /><MasteringPanel key={revision} audioId={audioId} correctionsBusy={busy} initialReport={master} defaultPreset={report.plan.mastering_preset} /></>}
  </section>;
}
