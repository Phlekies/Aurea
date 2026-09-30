import { AlertTriangle, Circle, ScanLine } from 'lucide-react';
import type { Diagnostic } from '../../api/analysis';
import { diagnosticTitles as titles } from './labels';
import { Measurements } from './Measurements';

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
