import { AlertTriangle, Circle, ScanLine } from 'lucide-react';
import type { Diagnostic } from '../../api/analysis';

const titles: Record<Diagnostic['code'], string> = {
  clipping: 'Saturación digital',
  hum: 'Zumbido eléctrico',
  rumble: 'Retumbo de graves',
  low_level: 'Nivel bajo',
  low_headroom: 'Margen de pico reducido',
  stationary_noise: 'Ruido continuo',
  sibilance: 'Sibilancia',
  plosives: 'Golpes de aire',
};

type FieldFormat = { label: string; unit?: string; digits?: number };
const diagnosticFields: Record<string, FieldFormat> = {
  hard_samples_per_channel: { label: 'Muestras saturadas por canal', digits: 0 },
  near_samples_per_channel: { label: 'Muestras próximas al límite por canal', digits: 0 },
  hard_sample_percent: { label: 'Muestras saturadas', unit: '%' },
  near_sample_percent: { label: 'Muestras próximas al límite', unit: '%' },
  max_hard_run_samples: { label: 'Mayor secuencia saturada', unit: 'muestras', digits: 0 },
  max_near_run_samples: { label: 'Mayor secuencia próxima al límite', unit: 'muestras', digits: 0 },
  flat_top_samples: { label: 'Muestras en crestas aplanadas', digits: 0 },
  max_flat_top_run_samples: { label: 'Mayor secuencia aplanada', unit: 'muestras', digits: 0 },
  base_frequency_hz: { label: 'Frecuencia de red', unit: 'Hz' },
  harmonic_frequencies_hz: { label: 'Frecuencias de los armónicos', unit: 'Hz' },
  harmonic_contrast_db: { label: 'Contraste de los armónicos', unit: 'dB' },
  harmonic_persistence: { label: 'Persistencia de los armónicos (0–1)' },
  harmonic_energy_percent: { label: 'Energía en los armónicos', unit: '%' },
  analyzed_windows: { label: 'Ventanas analizadas', digits: 0 },
  low_band_energy_percent: { label: 'Energía en graves', unit: '%' },
  low_activity_low_band_percent: { label: 'Graves durante baja actividad', unit: '%' },
  low_activity_low_rms_dbfs: { label: 'Nivel de graves durante baja actividad', unit: 'dBFS' },
  low_activity_duration_seconds: { label: 'Duración de baja actividad', unit: 's' },
  speech_windows: { label: 'Ventanas con actividad compatible con voz', digits: 0 },
  low_activity_windows: { label: 'Ventanas de baja actividad', digits: 0 },
  integrated_lufs: { label: 'Loudness integrado', unit: 'LUFS' },
  rms_dbfs: { label: 'Nivel RMS', unit: 'dBFS' },
  silence_percent: { label: 'Silencio', unit: '%' },
  true_peak_dbtp: { label: 'True peak estimado', unit: 'dBTP' },
  headroom_db: { label: 'Margen hasta el límite digital', unit: 'dB' },
  noise_rms_dbfs: { label: 'Nivel del posible ruido', unit: 'dBFS' },
  psd_similarity: { label: 'Similitud espectral (0–1)' },
  spectral_flatness: { label: 'Planitud espectral (0–1)' },
  relative_power_std: { label: 'Variación relativa de energía' },
  noise_windows: { label: 'Ventanas de posible ruido', digits: 0 },
  estimated_snr_db: { label: 'SNR aproximada (sin referencia limpia)', unit: 'dB' },
  event_count: { label: 'Eventos detectados', digits: 0 },
  event_times_seconds: { label: 'Instantes de los eventos', unit: 's' },
  max_band_energy_percent: { label: 'Mayor proporción de energía en la banda', unit: '%' },
  background_sibilance_rms_dbfs: { label: 'Nivel de agudos en el fondo', unit: 'dBFS' },
  minimum_duration_seconds: { label: 'Duración mínima', unit: 's' },
  band_low_hz: { label: 'Límite inferior de la banda', unit: 'Hz' },
  band_high_hz: { label: 'Límite superior de la banda', unit: 'Hz' },
  hard_amplitude_threshold: { label: 'Umbral de amplitud saturada (0–1)' },
  near_amplitude_threshold: { label: 'Umbral de proximidad al límite (0–1)' },
  flat_top_difference_threshold: { label: 'Diferencia máxima entre muestras aplanadas', digits: 6 },
  minimum_flat_top_run_samples: { label: 'Secuencia aplanada mínima', unit: 'muestras', digits: 0 },
  near_concentration_threshold_percent: { label: 'Umbral de concentración cerca del límite', unit: '%' },
  minimum_near_run_samples: { label: 'Secuencia mínima cerca del límite', unit: 'muestras', digits: 0 },
  full_confidence_minimum_duration_seconds: { label: 'Duración mínima para evidencia completa', unit: 's' },
  candidate_frequencies_hz: { label: 'Frecuencias de red comprobadas', unit: 'Hz' },
  harmonic_contrast_threshold_db: { label: 'Contraste mínimo de los armónicos', unit: 'dB' },
  harmonic_persistence_threshold: { label: 'Persistencia mínima de los armónicos (0–1)' },
  minimum_harmonic_energy_percent: { label: 'Energía mínima en los armónicos', unit: '%' },
  frequency_tolerance_hz: { label: 'Tolerancia de frecuencia', unit: 'Hz' },
  minimum_analyzed_windows: { label: 'Ventanas mínimas analizadas', digits: 0 },
  minimum_strong_harmonics: { label: 'Armónicos destacados mínimos', digits: 0 },
  fundamental_required: { label: 'Fundamental obligatoria' },
  single_fundamental_contrast_threshold_db: { label: 'Contraste mínimo de una fundamental aislada', unit: 'dB' },
  single_fundamental_persistence_threshold: { label: 'Persistencia mínima de una fundamental aislada (0–1)' },
  global_energy_threshold_percent: { label: 'Umbral de energía grave global', unit: '%' },
  low_activity_energy_threshold_percent: { label: 'Umbral de graves durante baja actividad', unit: '%' },
  minimum_low_rms_dbfs: { label: 'Nivel mínimo de graves en las pausas', unit: 'dBFS' },
  minimum_low_activity_seconds: { label: 'Baja actividad mínima', unit: 's' },
  low_voice_probability_low_band_threshold_percent: { label: 'Graves mínimos para descartar voz', unit: '%' },
  loudness_threshold_lufs: { label: 'Umbral de nivel percibido', unit: 'LUFS' },
  fallback_rms_threshold_dbfs: { label: 'Umbral RMS alternativo', unit: 'dBFS' },
  maximum_silence_percent: { label: 'Silencio máximo para evaluar', unit: '%' },
  true_peak_threshold_dbtp: { label: 'Umbral de true peak', unit: 'dBTP' },
  true_peak_is_estimate: { label: 'True peak estimado' },
  minimum_speech_seconds: { label: 'Actividad de voz mínima', unit: 's' },
  psd_similarity_threshold: { label: 'Similitud espectral mínima (0–1)' },
  spectral_flatness_threshold: { label: 'Planitud espectral mínima (0–1)' },
  maximum_relative_power_std: { label: 'Variación relativa de energía máxima' },
  noise_rms_threshold_dbfs: { label: 'Umbral del nivel de ruido', unit: 'dBFS' },
  snr_threshold_db: { label: 'SNR aproximada máxima', unit: 'dB' },
  snr_is_estimate: { label: 'SNR estimada sin referencia limpia' },
  speech_probability_is_heuristic: { label: 'Identificación de voz heurística' },
  band_energy_threshold_percent: { label: 'Umbral de energía en la banda', unit: '%' },
  minimum_voice_power_dbfs: { label: 'Nivel mínimo en la banda de voz', unit: 'dBFS' },
  minimum_event_seconds: { label: 'Duración mínima del evento', unit: 's' },
  maximum_event_seconds: { label: 'Duración máxima del evento', unit: 's' },
  minimum_nyquist_hz: { label: 'Frecuencia máxima de audio mínima', unit: 'Hz' },
  maximum_event_times: { label: 'Máximo de instantes incluidos', digits: 0 },
  minimum_speech_windows: { label: 'Ventanas de voz mínimas', digits: 0 },
  minimum_background_contrast_db: { label: 'Contraste mínimo frente al fondo', unit: 'dB' },
  maximum_background_voice_band_percent: { label: 'Energía máxima de voz en el fondo', unit: '%' },
  minimum_low_power_dbfs: { label: 'Nivel mínimo de graves', unit: 'dBFS' },
  onset_threshold_db: { label: 'Aumento mínimo al inicio del evento', unit: 'dB' },
  speech_neighborhood_seconds: { label: 'Distancia máxima a la voz', unit: 's' },
};

function fieldFormat(key: string): FieldFormat {
  if (diagnosticFields[key]) return diagnosticFields[key];
  const label = key.replace(/_/g, ' ');
  return { label: `${label[0]?.toUpperCase() ?? ''}${label.slice(1)}` };
}

function formatValue(value: Diagnostic['evidence'][string], field: FieldFormat): string {
  if (value === null) return 'No medido';
  if (typeof value === 'boolean') return value ? 'Sí' : 'No';
  if (Array.isArray(value)) return value.length ? value.map((item) => formatValue(item, field)).join(' · ') : 'Ninguno';
  const text = typeof value === 'number' ? value.toLocaleString('es-ES', { maximumFractionDigits: field.digits ?? 3 }) : value;
  return field.unit ? `${text} ${field.unit}` : text;
}

function Measurements({ values }: { values: Diagnostic['evidence'] }) {
  return <dl className="diagnostic-measurements">{Object.entries(values).map(([key, value]) => {
    const field = fieldFormat(key);
    return <div key={key}><dt>{field.label}</dt><dd>{formatValue(value, field)}</dd></div>;
  })}</dl>;
}

function DiagnosticCard({ diagnostic }: { diagnostic: Diagnostic }) {
  const level = diagnostic.severity >= .7 ? 'alta' : diagnostic.severity >= .35 ? 'media' : 'leve';
  const confidence = diagnostic.confidence >= .75 ? 'Evidencia alta' : diagnostic.confidence >= .4 ? 'Evidencia moderada' : 'Evidencia limitada';
  return <article className={`diagnostic-card ${diagnostic.detected ? `detected severity-${level}` : 'undetected'}`} aria-label={titles[diagnostic.code]}>
    <div className="diagnostic-card-heading"><h5>{diagnostic.detected ? <AlertTriangle size={16} /> : <Circle size={14} />}{titles[diagnostic.code]}</h5>
      <span className="diagnostic-badge">{diagnostic.detected ? `Severidad ${level}` : diagnostic.confidence < .4 ? 'Evidencia insuficiente' : 'Sin indicios'}</span>
    </div>
    <p className="diagnostic-message">{diagnostic.message}</p>
    {diagnostic.detected && <p className="diagnostic-confidence">{confidence}</p>}
    <details className="diagnostic-details"><summary>Evidencia y parámetros</summary>
      <h6>Mediciones observadas</h6><Measurements values={diagnostic.evidence} />
      <h6>Parámetros de detección</h6><Measurements values={diagnostic.parameters} />
    </details>
  </article>;
}

export function DiagnosticsPanel({ diagnostics }: { diagnostics: Diagnostic[] }) {
  const detected = diagnostics.filter((diagnostic) => diagnostic.detected).sort((a, b) => b.severity - a.severity);
  const remaining = diagnostics.filter((diagnostic) => !diagnostic.detected);
  const inconclusive = remaining.filter((diagnostic) => diagnostic.confidence < .4).length;
  return <section className="diagnostics-section" aria-labelledby="diagnostics-title">
    <div className="diagnostics-heading"><h4 id="diagnostics-title"><ScanLine size={18} />Diagnóstico automático</h4><span>{diagnostics.length} comprobaciones</span></div>
    <p className="diagnostics-summary">{detected.length ? `${detected.length} ${detected.length === 1 ? 'posible problema detectado' : 'posibles problemas detectados'}.` : 'No se han detectado problemas en estas comprobaciones.'}{inconclusive > 0 && ` ${inconclusive} ${inconclusive === 1 ? 'comprobación con evidencia insuficiente' : 'comprobaciones con evidencia insuficiente'}.`}</p>
    <p className="diagnostics-explanation">La evidencia indica cuánto respalda la señal cada conclusión; es una estimación heurística, no una probabilidad. Sin indicios no garantiza un audio libre de problemas.</p>
    {detected.length > 0 && <div className="diagnostic-list" aria-label="Posibles problemas detectados">{detected.map((diagnostic) => <DiagnosticCard key={diagnostic.code} diagnostic={diagnostic} />)}</div>}
    <details className="diagnostic-checks"><summary>Todas las comprobaciones</summary>
      <p className="analysis-note">{remaining.length ? 'Comprobaciones sin detección, además de los posibles problemas anteriores.' : 'Las ocho comprobaciones muestran indicios y aparecen arriba.'}</p>
      <div className="diagnostic-list">{remaining.map((diagnostic) => <DiagnosticCard key={diagnostic.code} diagnostic={diagnostic} />)}</div>
    </details>
  </section>;
}
