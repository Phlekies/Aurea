import { useEffect, useState } from 'react';
import { AudioLines, Check, Circle, Headphones, Layers3, SlidersHorizontal, Sparkles } from 'lucide-react';
import { getHealth } from './api/client';
import { AudioWorkspace } from './features/audio/AudioWorkspace';

type ServiceState = 'connecting' | 'online' | 'offline';

export function App() {
  const [service, setService] = useState<ServiceState>('connecting');
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    getHealth(controller.signal)
      .then(() => setService('online'))
      .catch(() => { if (!controller.signal.aborted) setService('offline'); });
    return () => controller.abort();
  }, [attempt]);

  const status = { connecting: 'Conectando con el servicio', online: 'Servicio conectado', offline: 'Servicio sin conexión' }[service];

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="#studio" aria-label="Aurea, inicio"><span className="brand-symbol"><AudioLines size={23} /></span>aurea<span className="brand-dot">.</span></a>
        <div className="workspace-label">TU ESPACIO DE AUDIO</div>
        <nav aria-label="Navegación principal">
          <a className="nav-item active" href="#studio"><Layers3 size={18} />Estudio<span className="nav-indicator" /></a>
          <span className="nav-item unavailable"><Headphones size={18} />Mis proyectos<span className="soon">Pronto</span></span>
          <span className="nav-item unavailable"><SlidersHorizontal size={18} />Presets<span className="soon">Pronto</span></span>
        </nav>
        <div className="sidebar-bottom"><div className="mini-icon"><Sparkles size={18} /></div><strong>Un buen sonido se entiende.</strong><p>Mejora tu audio y descubre el porqué de cada ajuste.</p><span className="version">DESARROLLO · V1.0.0-ALPHA</span></div>
      </aside>

      <main id="studio">
        <header className="topbar"><span>Estudio <span className="breadcrumb">/</span> <strong>Nuevo proyecto</strong></span><span className={`service ${service}`} role="status"><span />{status}</span></header>
        <div className="main-content">
          <div className="eyebrow"><span />PODCAST AUDIO DOCTOR</div>
          <section className="intro"><h1>Tu voz, en su mejor versión.</h1><p>Menos ruido. Más claridad. Un master equilibrado.<br />Y una explicación de cada cambio.</p></section>

          <AudioWorkspace />

          <section className="workflow" aria-label="Cómo funciona Aurea">
            <div className="workflow-heading"><h2>Del primer sonido al último detalle</h2><span>EL FLUJO AUREA</span></div>
            <div className="steps">
              <article><span className="step-number">01</span><h3>Escucha y analiza</h3><p>Conoce el ruido, la dinámica y el nivel de tu grabación.</p></article>
              <article><span className="step-number">02</span><h3>Mejora con criterio</h3><p>Una cadena de ajustes adaptada a lo que tu audio necesita.</p></article>
              <article><span className="step-number">03</span><h3>Compara y exporta</h3><p>Escucha la diferencia y descarga tu master final.</p></article>
            </div>
          </section>

          <footer className="development-note"><div><Check size={15} /><span>Análisis, correcciones y masterización con descarga WAV disponibles</span></div><div><Circle size={12} /><span>Siguiente: experiencia web y exportación multiformato</span></div>{service === 'offline' && <button onClick={() => { setService('connecting'); setAttempt((value) => value + 1); }}>Reintentar conexión</button>}</footer>
        </div>
      </main>
    </div>
  );
}
