import { AudioLines } from 'lucide-react';
import type { AudioAnalysis, SpeechActivity } from '../../api/analysis';
import { formatNumber as number, formatTime } from './format';

type Label = SpeechActivity['segments'][number]['label'];

const lanes: { label: Label; title: string }[] = [
  { label: 'speech', title: 'Voz' },
  { label: 'noise', title: 'Fondo' },
  { label: 'silence', title: 'Silencio' },
];

const parameterLabels: Record<string, { label: string; unit: string; digits?: number }> = {
  enter_threshold_dbfs: { label: 'Umbral para empezar a hablar', unit: 'dBFS' },
  stay_threshold_dbfs: { label: 'Umbral para seguir hablando', unit: 'dBFS' },
  floor_level_dbfs: { label: 'Nivel de fondo (percentil 10)', unit: 'dBFS' },
  active_level_dbfs: { label: 'Nivel activo (percentil 90)', unit: 'dBFS' },
  bridge_gap_seconds: { label: 'Pausas unidas hasta', unit: 's', digits: 2 },
  minimum_speech_seconds: { label: 'Voz mínima', unit: 's', digits: 2 },
  hangover_after_seconds: { label: 'Margen tras la voz', unit: 's', digits: 2 },
};

function Timeline({ activity, duration }: { activity: SpeechActivity; duration: number }) {
  const visible = lanes.filter((lane) => lane.label !== 'silence' || activity.silence_seconds > 0);
  // Bars stretch to any width; labels stay HTML text so they remain legible on phones.
  const x = (seconds: number) => seconds / duration * 1000;
  return <div className="activity-timeline" role="img"
    aria-label={`Línea temporal de actividad: voz durante ${number(activity.speech_percent)} % de la grabación, en ${activity.segments.filter((segment) => segment.label === 'speech').length} tramos`}>
    {visible.map((lane) => <div className="activity-row" key={lane.label}>
      <span>{lane.title}</span>
      <svg viewBox="0 0 1000 16" preserveAspectRatio="none" aria-hidden="true">
        <rect className="activity-lane" x="0" y="0" width="1000" height="16" />
        {activity.segments.filter((segment) => segment.label === lane.label).map((segment) => <rect key={segment.start_seconds}
          className={`activity-segment ${lane.label}`} x={x(segment.start_seconds)} y="0"
          width={Math.max(1.5, x(segment.end_seconds) - x(segment.start_seconds))} height="16" />)}
      </svg>
    </div>)}
    <div className="activity-axis" aria-hidden="true">{[0, .5, 1].map((fraction) => <span key={fraction}>{formatTime(fraction * duration)}</span>)}</div>
  </div>;
}

function Stat({ label, value, note }: { label: string; value: string; note: string }) {
  return <div className="activity-stat"><span>{label}</span><strong>{value}</strong><p>{note}</p></div>;
}

export function ActivityPanel({ report }: { report: AudioAnalysis }) {
  const activity = report.speech_activity;
  const profile = report.noise_profile;
  const snr = report.estimated_snr_db;
  const stable = profile.spectral_stability !== null && profile.relative_power_std !== null
    && profile.spectral_stability >= .8 && profile.relative_power_std <= .6;
  return <section className="activity-section" aria-labelledby="activity-title">
    <div className="diagnostics-heading"><h4 id="activity-title"><AudioLines size={18} />Voz y ruido de fondo</h4><span>VAD {activity.detector} · v{activity.version}</span></div>
    <div className="activity-stats">
      <Stat label="Voz detectada" value={`${number(activity.speech_percent, 0)} %`} note={`${number(activity.speech_seconds)} s de ${number(report.duration_seconds)} s`} />
      <Stat label="SNR aproximada" value={snr === null ? 'No estimable' : `${number(snr)} dB`}
        note={snr === null ? 'Faltan tramos de voz o de fondo comparables.' : 'Voz frente al fondo, sin referencia limpia.'} />
      <Stat label="Ruido de fondo" value={profile.rms_dbfs === null ? 'Sin datos' : `${number(profile.rms_dbfs)} dBFS`}
        note={profile.floor_dbfs === null ? 'No hay pausas suficientes para medirlo.' : `Suelo ${number(profile.floor_dbfs)} dBFS en ${number(profile.duration_seconds)} s de pausas.`} />
      <Stat label="Estabilidad del fondo" value={profile.frame_count === 0 ? 'Sin datos' : stable ? 'Estable' : 'Variable'}
        note={profile.spectral_stability === null ? 'Se necesita más fondo sin voz.' : `Similitud espectral ${number(profile.spectral_stability, 2)}.`} />
    </div>
    <Timeline activity={activity} duration={report.duration_seconds} />
    <p className="diagnostics-explanation">La segmentación es heurística: combina energía, planitud espectral y cruces por cero con histéresis. La SNR compara la energía durante la voz con la del fondo; es una aproximación, no una medida exacta.</p>
    <details className="diagnostic-details"><summary>Parámetros de segmentación y perfil de ruido</summary>
      <dl className="diagnostic-measurements">
        {Object.entries(parameterLabels).filter(([key]) => key in activity.parameters).map(([key, format]) => {
          const value = activity.parameters[key];
          return <div key={key}><dt>{format.label}</dt><dd>{value === null ? 'No definido' : `${number(value, format.digits ?? 1)} ${format.unit}`}</dd></div>;
        })}
        <div><dt>Tramos de fondo usados</dt><dd>{profile.frame_count} ventanas · {number(profile.duration_seconds)} s</dd></div>
        <div><dt>Planitud espectral del fondo (0–1)</dt><dd>{number(profile.spectral_flatness, 2)}</dd></div>
        <div><dt>Variación relativa de energía del fondo</dt><dd>{number(profile.relative_power_std, 2)}</dd></div>
      </dl>
    </details>
  </section>;
}
