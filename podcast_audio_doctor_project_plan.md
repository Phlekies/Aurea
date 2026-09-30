# Podcast Audio Doctor — Plan de desarrollo

> Roadmap técnico y de producto para construir una aplicación de restauración y mastering automático de audio orientada a podcasts.
>
> Objetivo: que el proyecto funcione a la vez como **producto utilizable**, **proyecto de portfolio** y **demostración de conocimientos de DSP, backend, frontend, testing y arquitectura de software**.

---

# 1. Visión del producto

## 1.1 Problema

Muchos podcasts, entrevistas y grabaciones de voz se producen en condiciones no ideales:

- ruido de fondo;
- hum eléctrico;
- reverberación;
- volumen desigual;
- diferencias de nivel entre interlocutores;
- sibilancia;
- plosivas;
- clipping;
- exceso de graves;
- voz demasiado apagada o brillante;
- pausas o silencios innecesarios;
- loudness final inconsistente.

Las herramientas profesionales permiten corregir estos problemas, pero normalmente requieren experiencia en audio o funcionan como una "caja negra".

## 1.2 Propuesta

Construir una aplicación que funcione como un **Audio Doctor para podcasts**:

```text
Audio original
      │
      ▼
Análisis automático
      │
      ▼
Diagnóstico explicable
      │
      ▼
Cadena de procesado recomendada
      │
      ▼
Procesamiento automático
      │
      ▼
Comparación antes / después
      │
      ▼
Mastering para podcast
      │
      ▼
Exportación
```

La característica diferencial no debe ser únicamente "mejorar voz".

Debe ser:

> **Analizar → explicar → decidir → procesar → medir.**

El usuario debe poder ver:

- qué problemas ha detectado la aplicación;
- con qué intensidad;
- con qué confianza;
- qué procesamiento va a aplicar;
- por qué lo va a aplicar;
- cuánto ha cambiado el audio;
- si el resultado cumple el objetivo de loudness seleccionado.

---

# 2. Objetivos del proyecto

## Objetivo principal

Crear una aplicación web capaz de recibir un archivo de podcast o voz, analizarlo automáticamente y producir una versión técnicamente mejorada y preparada para publicación.

## Objetivos técnicos

El proyecto debe demostrar:

- DSP;
- análisis espectral;
- procesamiento temporal;
- filtros digitales;
- STFT / ISTFT;
- estimación de ruido;
- VAD;
- reducción de ruido;
- dinámica;
- loudness;
- arquitectura backend;
- procesamiento asíncrono;
- frontend interactivo;
- testing;
- benchmarking;
- diseño de APIs;
- ingeniería de software.

## Objetivo de portfolio

El proyecto debe poder enseñarse mediante:

1. repositorio GitHub;
2. README profesional;
3. demo web;
4. ejemplos de audio antes/después;
5. métricas;
6. capturas del diagnóstico;
7. explicación de arquitectura;
8. benchmarks.

---

# 3. Principios del proyecto

## 3.1 Producto primero

Las funcionalidades deben priorizarse según su utilidad real para un podcaster.

El camino crítico NO debe centrarse en:

- implementar una FFT propia;
- implementar un codec propio;
- rehacer algoritmos que NumPy/SciPy ya proporcionan.

Esas tareas pueden mantenerse en un directorio educativo separado, pero no deben bloquear el producto.

## 3.2 DSP explicable

Cada procesamiento automático debe dejar un registro.

Ejemplo:

```json
{
  "problem": "electrical_hum",
  "detected": true,
  "confidence": 0.94,
  "frequency_hz": 50.1,
  "severity": "medium",
  "recommended_action": {
    "processor": "notch_filter",
    "frequency_hz": 50.0,
    "q": 30.0
  }
}
```

## 3.3 Procesamiento no destructivo

El audio original nunca debe modificarse.

Guardar:

```text
original
processed_preview
final_export
analysis_report
processing_manifest
```

## 3.4 Automatización con control manual

Tres modos:

### Auto

La aplicación decide toda la cadena.

### Assisted

La aplicación recomienda y el usuario confirma.

### Manual

El usuario configura cada módulo.

Para la primera versión pública, priorizar **Auto + Assisted**.

---

# 4. Alcance del MVP

El MVP debe aceptar un archivo de voz y poder realizar:

- carga de WAV, FLAC, MP3, M4A y OGG mediante FFmpeg;
- visualización waveform;
- reproducción;
- análisis automático;
- detección de clipping;
- detección de hum 50/60 Hz;
- detección de exceso de baja frecuencia;
- estimación de ruido;
- VAD;
- estimación aproximada de SNR;
- high-pass automático;
- de-hum;
- reducción de ruido;
- nivelado de voz;
- normalización de loudness;
- true-peak limiting;
- comparación A/B;
- exportación WAV/FLAC/MP3;
- informe del procesamiento aplicado.

No incluir todavía:

- edición multipista completa;
- transcripción;
- edición de texto;
- clonación de voz;
- generación de voz;
- DAW completo;
- entrenamiento de modelos grandes;
- procesamiento en tiempo real.

---

# 5. Arquitectura propuesta

## 5.1 Stack

### Backend

- Python 3.12+;
- FastAPI;
- NumPy;
- SciPy;
- SoundFile;
- librosa cuando aporte utilidad;
- pyloudnorm o implementación validada de loudness;
- FFmpeg para formatos comprimidos;
- Pydantic;
- pytest.

### Frontend

- React;
- TypeScript;
- Vite o Next.js;
- WaveSurfer.js para waveform y reproducción;
- Plotly o ECharts para gráficas.

### Infraestructura

Inicial:

- almacenamiento local temporal;
- procesamiento dentro del backend.

Posteriormente:

- Redis;
- worker de procesamiento;
- object storage;
- jobs asíncronos.

### DevOps

- Docker;
- docker-compose;
- GitHub Actions;
- Ruff;
- mypy;
- pytest.

---

# 6. Arquitectura lógica

```text
┌─────────────────────────────────────────────────────────┐
│                       Frontend                          │
│ Upload · Waveform · Diagnosis · A/B · Settings · Export│
└──────────────────────────┬──────────────────────────────┘
                           │ REST
                           ▼
┌─────────────────────────────────────────────────────────┐
│                        FastAPI                          │
│ Upload API · Analysis API · Processing API · Export API│
└──────────────────────────┬──────────────────────────────┘
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
   Audio Core        Analysis Engine   Pipeline Engine
          │                │                │
          └────────────────┼────────────────┘
                           ▼
                    DSP Processors
                           │
                           ▼
                  Evaluation / QA
                           │
                           ▼
                       Export
```

---

# 7. Modelo de dominio

Crear modelos independientes de frameworks.

## AudioAsset

```python
AudioAsset(
    id: str,
    path: Path,
    sample_rate: int,
    channels: int,
    duration_seconds: float,
    format: str,
)
```

## AudioBuffer

```python
AudioBuffer(
    samples: np.ndarray,
    sample_rate: int,
)
```

Convención interna:

```text
float32
[-1.0, +1.0]
shape = (samples, channels)
```

## AudioAnalysis

Debe contener:

```python
AudioAnalysis(
    duration,
    sample_rate,
    channels,
    peak_dbfs,
    rms_dbfs,
    crest_factor,
    integrated_lufs,
    loudness_range,
    true_peak_dbtp,
    estimated_snr_db,
    clipping,
    hum,
    low_frequency_noise,
    noise_profile,
    speech_activity,
)
```

## Diagnostic

```python
Diagnostic(
    code: str,
    detected: bool,
    severity: float,
    confidence: float,
    message: str,
    evidence: dict,
)
```

## ProcessingStep

```python
ProcessingStep(
    processor: str,
    enabled: bool,
    parameters: dict,
    reason: str,
)
```

## ProcessingPlan

```python
ProcessingPlan(
    preset: str,
    steps: list[ProcessingStep],
)
```

## ProcessingReport

Debe registrar:

- métricas antes;
- métricas después;
- pasos aplicados;
- parámetros;
- tiempo de ejecución;
- warnings;
- errores;
- versión del pipeline.

---

# 8. Estructura de repositorio

```text
podcast-audio-doctor/
│
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   ├── routes_audio.py
│   │   │   ├── routes_analysis.py
│   │   │   ├── routes_processing.py
│   │   │   └── routes_export.py
│   │   │
│   │   ├── domain/
│   │   │   ├── audio.py
│   │   │   ├── analysis.py
│   │   │   ├── diagnostics.py
│   │   │   ├── processing.py
│   │   │   └── reports.py
│   │   │
│   │   ├── audio/
│   │   │   ├── io.py
│   │   │   ├── ffmpeg.py
│   │   │   ├── resampling.py
│   │   │   └── conversion.py
│   │   │
│   │   ├── dsp/
│   │   │   ├── framing.py
│   │   │   ├── stft.py
│   │   │   ├── filters.py
│   │   │   ├── dynamics.py
│   │   │   ├── loudness.py
│   │   │   ├── limiter.py
│   │   │   └── equalization.py
│   │   │
│   │   ├── analysis/
│   │   │   ├── features.py
│   │   │   ├── clipping.py
│   │   │   ├── hum.py
│   │   │   ├── vad.py
│   │   │   ├── noise.py
│   │   │   ├── spectral.py
│   │   │   └── analyzer.py
│   │   │
│   │   ├── processors/
│   │   │   ├── highpass.py
│   │   │   ├── dehum.py
│   │   │   ├── spectral_gate.py
│   │   │   ├── spectral_subtraction.py
│   │   │   ├── wiener.py
│   │   │   ├── compressor.py
│   │   │   ├── leveler.py
│   │   │   ├── deesser.py
│   │   │   ├── deplosive.py
│   │   │   ├── normalizer.py
│   │   │   └── true_peak_limiter.py
│   │   │
│   │   ├── pipeline/
│   │   │   ├── decision_engine.py
│   │   │   ├── presets.py
│   │   │   ├── processor.py
│   │   │   └── registry.py
│   │   │
│   │   ├── evaluation/
│   │   │   ├── metrics.py
│   │   │   ├── quality_checks.py
│   │   │   └── benchmark.py
│   │   │
│   │   ├── services/
│   │   │   ├── analysis_service.py
│   │   │   ├── processing_service.py
│   │   │   └── export_service.py
│   │   │
│   │   └── main.py
│   │
│   ├── tests/
│   │   ├── unit/
│   │   ├── integration/
│   │   ├── regression/
│   │   └── fixtures/
│   │
│   └── pyproject.toml
│
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   ├── features/
│   │   │   ├── upload/
│   │   │   ├── player/
│   │   │   ├── analysis/
│   │   │   ├── processing/
│   │   │   └── export/
│   │   ├── api/
│   │   └── app/
│   └── package.json
│
├── research/
│   ├── notebooks/
│   ├── dsp_from_scratch/
│   └── experiments/
│
├── samples/
│   ├── clean/
│   ├── noisy/
│   └── processed/
│
├── benchmarks/
├── docs/
├── docker-compose.yml
├── Makefile
├── README.md
└── LICENSE
```

---

# 9. FASE 0 — Bootstrap del proyecto

## Objetivo

Crear una base limpia y reproducible antes de implementar DSP.

## Tareas Codex

### Backend

- crear proyecto Python;
- configurar `pyproject.toml`;
- instalar dependencias;
- configurar Ruff;
- configurar mypy;
- configurar pytest;
- crear aplicación FastAPI mínima;
- endpoint `/health`.

### Frontend

- crear aplicación React + TypeScript;
- configurar cliente HTTP;
- crear pantalla inicial.

### Infraestructura

- Dockerfile backend;
- Dockerfile frontend;
- docker-compose;
- `.env.example`;
- `.gitignore`;
- GitHub Actions.

## Criterios de aceptación

- `docker compose up` levanta frontend y backend;
- `GET /health` devuelve HTTP 200;
- tests ejecutables con un único comando;
- lint ejecutable con un único comando.

## Entregable

```text
v0.1.0 — project skeleton
```

---

# 10. FASE 1 — Ingesta y representación de audio

## Objetivo

Aceptar audio real procedente de usuarios y transformarlo al formato interno.

## Tareas Codex

### Importación

Soportar:

- WAV;
- FLAC;
- MP3;
- M4A;
- OGG.

Usar FFmpeg para decodificar cuando sea necesario.

### Validaciones

Comprobar:

- extensión;
- MIME;
- duración máxima;
- tamaño máximo;
- sample rate válido;
- número de canales.

### Conversión interna

Convertir a:

```text
float32
sample rate original
mono/stereo conservado
```

Crear funciones:

```python
load_audio()
save_audio()
convert_to_float()
to_mono()
resample()
```

### Metadata

Extraer:

- duración;
- sample rate;
- canales;
- codec;
- bitrate cuando exista;
- tamaño del fichero.

### Preview

Crear una versión de baja resolución para waveform.

## API

```http
POST /api/audio
GET  /api/audio/{id}
GET  /api/audio/{id}/waveform
GET  /api/audio/{id}/stream
```

## Tests

- WAV mono;
- WAV stereo;
- MP3;
- 44.1 kHz;
- 48 kHz;
- archivo corrupto;
- formato no soportado.

## Criterios de aceptación

Un usuario puede subir un podcast real y reproducirlo desde la aplicación.

## Entregable

```text
v0.2.0 — audio ingestion
```

---

# 11. FASE 2 — Motor de análisis básico

## Objetivo

Crear el informe técnico inicial.

## Métricas

Calcular:

- peak dBFS;
- RMS dBFS;
- crest factor;
- DC offset;
- duración;
- zero-crossing rate;
- energía por bandas;
- espectro promedio;
- PSD;
- dinámica temporal;
- porcentaje de silencio;
- loudness integrado;
- true peak.

## Ventanas temporales

Calcular estadísticas por bloques, por ejemplo:

```text
20 ms
100 ms
400 ms
3 s
```

según la métrica.

## API

```http
POST /api/audio/{id}/analyze
GET  /api/audio/{id}/analysis
```

## Salida

```json
{
  "peak_dbfs": -2.1,
  "rms_dbfs": -21.4,
  "integrated_lufs": -22.7,
  "true_peak_dbtp": -1.8,
  "crest_factor_db": 19.3
}
```

## Criterios de aceptación

- resultados deterministas;
- no aparecen NaN;
- funcionamiento con mono y stereo;
- tests con señales sintéticas.

## Entregable

```text
v0.3.0 — audio analyzer
```

---

# 12. FASE 3 — Diagnóstico automático

## Objetivo

Transformar métricas en problemas comprensibles.

## 3.1 Clipping

Detectar:

- hard clipping;
- concentración anormal de muestras cerca de ±1;
- secuencias consecutivas saturadas.

Salida:

```json
{
  "code": "clipping",
  "detected": true,
  "severity": 0.42,
  "confidence": 0.91
}
```

## 3.2 Hum

Analizar:

```text
50 Hz
100 Hz
150 Hz
...

60 Hz
120 Hz
180 Hz
...
```

Comparar energía de armónicos con bins vecinos.

Detectar automáticamente si el patrón dominante corresponde a 50 o 60 Hz.

## 3.3 Rumble / low-frequency noise

Calcular energía relativa en la zona grave.

Evitar confundir voz masculina con ruido usando actividad temporal y distribución espectral.

## 3.4 Nivel insuficiente

Detectar grabaciones excesivamente bajas.

## 3.5 Riesgo de saturación

Detectar poco headroom.

## 3.6 Ruido estacionario

Comparar PSD en regiones de baja probabilidad de voz.

## 3.7 Sibilancia

Primera implementación heurística.

Medir energía relativa en banda de sibilancia durante segmentos de voz.

## 3.8 Plosivas

Buscar bursts de energía de baja frecuencia asociados a onset de voz.

## Contrato común

Todos los detectores implementan:

```python
class Detector(Protocol):
    def analyze(self, audio, context) -> Diagnostic:
        ...
```

## Criterios de aceptación

Cada diagnóstico debe incluir:

- `detected`;
- `severity`;
- `confidence`;
- evidencia;
- mensaje entendible;
- parámetros relevantes.

## Entregable

```text
v0.4.0 — explainable diagnostics
```

---

# 13. FASE 4 — VAD y noise profile

## Objetivo

Separar regiones de voz y no voz para mejorar el análisis.

## Primera versión

VAD basado en:

- energía;
- spectral flatness;
- zero-crossing rate;
- hysteresis;
- smoothing temporal.

## Segunda versión

Permitir sustituir el detector por:

- WebRTC VAD;
- Silero VAD;
- otro backend.

Crear interfaz:

```python
class VoiceActivityDetector(Protocol):
    def detect(self, audio) -> SpeechActivity:
        ...
```

## Noise profile

Usar segmentos sin voz para estimar:

- PSD de ruido;
- espectro medio;
- noise floor;
- estabilidad temporal.

## Estimación SNR

Calcular una SNR aproximada usando:

```text
potencia durante voz
potencia estimada del ruido
```

No presentarla como una medida exacta cuando no existe señal limpia de referencia.

## Visualización

Mostrar una timeline:

```text
VOICE ━━━━━━━
NOISE        ━━━
VOICE           ━━━━━
```

## Criterios de aceptación

- máscara temporal disponible;
- noise profile reproducible;
- integración con diagnósticos.

## Entregable

```text
v0.5.0 — speech/noise segmentation
```

---

# 14. FASE 5 — Procesamiento correctivo básico

## Objetivo

Resolver primero los defectos simples y de alta confianza.

## 5.1 DC removal

Eliminar DC offset cuando sea relevante.

## 5.2 High-pass adaptativo

Presets iniciales:

```text
off
60 Hz
70 Hz
80 Hz
100 Hz
```

El motor debe escoger el mínimo corte razonable.

## 5.3 De-hum

Crear notch filters para fundamental y armónicos.

Parámetros:

- fundamental;
- Q;
- número de armónicos;
- attenuation.

## 5.4 Pre-gain

Ajuste de nivel antes de módulos sensibles.

## Arquitectura de procesadores

```python
class AudioProcessor(Protocol):
    name: str

    def process(
        self,
        audio: AudioBuffer,
        params: dict,
    ) -> AudioBuffer:
        ...
```

Todos los procesadores deben ser registrables.

## Criterios de aceptación

- ningún módulo cambia sample rate;
- ningún módulo genera NaN;
- ninguna salida supera límites numéricos internos;
- tests con señales sintéticas.

## Entregable

```text
v0.6.0 — corrective filters
```

---

# 15. FASE 6 — Reducción de ruido DSP

## Objetivo

Implementar varios algoritmos para poder compararlos y elegir según la señal.

## 6.1 Spectral Subtraction

Pipeline:

```text
audio
  ↓
STFT
  ↓
noise estimate
  ↓
spectral subtraction
  ↓
spectral floor
  ↓
ISTFT
```

Implementar:

- oversubtraction;
- spectral floor;
- smoothing;
- preservación de fase.

## 6.2 Spectral Gate

Crear máscara:

```text
M(f,t)
```

con:

- threshold;
- attack;
- release;
- frequency smoothing;
- temporal smoothing;
- minimum gain.

Evitar gates binarios duros.

## 6.3 Wiener Filter

Implementar estimación:

```text
noise PSD
signal PSD
a-priori SNR
gain
```

## 6.4 Artefact detection

Añadir métricas aproximadas para detectar:

- musical noise;
- reducción excesiva;
- pérdida importante de energía de voz.

## Interfaz común

```python
class NoiseReducer(AudioProcessor):
    ...
```

## Benchmark

Comparar:

- calidad;
- tiempo;
- reducción de ruido;
- coste.

## Criterios de aceptación

El usuario puede cambiar entre:

```text
light
balanced
strong
```

sin conocer los parámetros internos.

## Entregable

```text
v0.7.0 — DSP denoising engine
```

---

# 16. FASE 7 — Dinámica y nivelado de voz

## Objetivo

Hacer que el audio suene consistente, no únicamente limpio.

Esta fase es esencial para que la aplicación sea útil para podcasts.

## 7.1 Compressor

Implementar:

- threshold;
- ratio;
- knee;
- attack;
- release;
- makeup gain.

## 7.2 Speech leveler

Nivelar cambios lentos de volumen.

No debe comportarse como una normalización global.

Debe analizar segmentos de voz y ajustar ganancia suavemente.

Pipeline conceptual:

```text
VAD
 ↓
short/mid-term loudness
 ↓
target speech level
 ↓
gain curve
 ↓
smoothing
 ↓
audio
```

## 7.3 Gain envelope

Guardar la curva de ganancia aplicada.

Esto permitirá mostrarla posteriormente en la UI.

## Tests

Crear grabaciones sintéticas con:

- hablante A muy bajo;
- hablante B alto;
- cambios progresivos;
- silencios;
- ruido.

## Criterios de aceptación

El nivelador no debe aumentar agresivamente silencios o noise-only regions.

## Entregable

```text
v0.8.0 — adaptive speech leveling
```

---

# 17. FASE 8 — Mastering para podcast

## Objetivo

Generar un fichero final consistente y listo para publicar.

## 8.1 Loudness

Implementar medición compatible con el estándar elegido.

Métricas:

- integrated loudness;
- short-term loudness;
- momentary loudness;
- loudness range.

## 8.2 Presets

Crear presets configurables.

Ejemplo inicial:

```text
Podcast Standard
target: -16 LUFS
true peak: -1 dBTP
```

No codificar las decisiones dentro del algoritmo.

Usar configuración:

```yaml
podcast_standard:
  target_lufs: -16.0
  max_true_peak_dbtp: -1.0
```

Permitir futuros presets.

## 8.3 True-peak limiter

Implementar limiter con oversampling o utilizar una implementación validada.

## 8.4 Output QC

Comprobar después del procesamiento:

- target LUFS;
- true peak;
- clipping;
- duración;
- NaN;
- silencio accidental;
- cambio de canales.

## Criterios de aceptación

El pipeline nunca debe exportar un fichero sin ejecutar Output QC.

## Entregable

```text
v0.9.0 — podcast mastering
```

---

# 18. FASE 9 — Decision Engine

## Objetivo

Convertir los módulos independientes en el verdadero producto.

## Entrada

```python
analysis: AudioAnalysis
diagnostics: list[Diagnostic]
preset: ProcessingPreset
```

## Salida

```python
ProcessingPlan
```

## Primera implementación

Reglas deterministas.

Ejemplo:

```python
if hum.detected and hum.confidence > 0.8:
    add_dehum()

if low_frequency_noise.severity > 0.4:
    add_highpass()

if background_noise.severity > 0.3:
    add_noise_reduction()

if speech_level_variation > threshold:
    add_leveler()

add_loudness_normalization()
add_true_peak_limiter()
```

## Requisito

Las reglas NO deben estar desperdigadas por el código.

Centralizarlas.

## Explicabilidad

Cada decisión debe incluir:

```json
{
  "processor": "high_pass",
  "enabled": true,
  "parameters": {
    "cutoff_hz": 70
  },
  "reason": "Elevated low-frequency energy was detected below 60 Hz.",
  "source_diagnostic": "low_frequency_noise"
}
```

## Confidence gating

Evitar procesamiento agresivo cuando la confianza sea baja.

Ejemplo:

```text
confidence > 0.85 → automático
0.55–0.85        → recomendar
< 0.55            → no activar
```

Los valores definitivos deben validarse experimentalmente.

## Presets de intensidad

```text
Natural
Balanced
Studio
```

### Natural

Procesamiento conservador.

### Balanced

Default.

### Studio

Mayor intervención.

## Criterios de aceptación

Dos ejecuciones sobre el mismo audio y configuración generan el mismo plan.

## Entregable

```text
v1.0.0-alpha — automatic processing engine
```

---

# 19. FASE 10 — Interfaz web usable

## Objetivo

Convertir el motor en una experiencia que una persona real pueda utilizar.

## Pantalla 1 — Upload

```text
Drop your podcast here
```

Mostrar:

- formatos;
- límite de tamaño;
- privacidad;
- botón upload.

## Pantalla 2 — Analysis

Mostrar cards:

```text
Background noise      HIGH
Electrical hum        NONE
Clipping              LOW
Voice level variation MEDIUM
Loudness              -22.4 LUFS
```

## Pantalla 3 — Recommended Fixes

Ejemplo:

```text
✓ High-pass filter       70 Hz
✓ Noise reduction        Balanced
✓ Speech leveling        Medium
✓ Loudness normalization -16 LUFS
✓ True peak limiting     -1 dBTP
```

Cada elemento debe poder expandirse.

Mostrar:

```text
Why?
```

## Pantalla 4 — Processing

Progress:

```text
Analyzing
Estimating noise
Reducing noise
Leveling speech
Mastering
Generating preview
```

## Pantalla 5 — Compare

Componente central del producto.

Incluir:

- waveform original;
- waveform processed;
- play/pause;
- botón A/B;
- misma posición temporal;
- loop de selección;
- volume matching cuando sea útil.

## Pantalla 6 — Results

Mostrar:

| Metric | Before | After |
|---|---:|---:|
| Loudness | -22.4 LUFS | -16.1 LUFS |
| True Peak | -2.4 dBTP | -1.0 dBTP |
| Estimated SNR | 8.1 dB | 15.7 dB |
| Clipping | 0.03% | 0% |

## Export

Opciones:

```text
WAV
FLAC
MP3
```

## Criterios de aceptación

Un usuario no técnico puede completar:

```text
upload → enhance → compare → download
```

sin consultar documentación.

## Entregable

```text
v1.0.0 — usable MVP
```

---

# 20. FASE 11 — De-esser, de-plosive y reparación

## Objetivo

Añadir los problemas de voz que hacen que el producto resulte más completo.

## 11.1 De-esser

Detectar sibilancia en segmentos de voz.

Aplicar reducción dinámica selectiva.

No implementar simplemente un low-pass.

## 11.2 De-plosive

Detectar eventos de baja frecuencia de corta duración.

Aplicar filtrado localizado.

## 11.3 De-click

Detectar impulsos anómalos.

Reparar mediante interpolación local.

## 11.4 Clipping

Primera versión:

- detectar;
- informar.

Versión posterior:

- declipping aproximado;
- reconstrucción opcional.

## Criterio

Estos módulos deben poder activarse solo en intervalos concretos.

## Entregable

```text
v1.1.0 — voice repair tools
```

---

# 21. FASE 12 — Reverb y modelos modernos

## Objetivo

Comparar DSP clásico con speech enhancement moderno.

No bloquear el producto inicial con esta fase.

## Arquitectura

Crear backend intercambiable:

```python
class EnhancementBackend(Protocol):
    def enhance(self, audio) -> AudioBuffer:
        ...
```

Backends posibles:

```text
classic_dsp
rnnoise
pretrained_model
```

## Evaluación

Comparar:

- calidad perceptual;
- inteligibilidad;
- tiempo;
- CPU;
- memoria;
- artefactos;
- real-time factor.

## UX

El usuario no necesita conocer el modelo.

Puede seleccionar:

```text
Classic
AI
Auto
```

## Entregable

```text
v1.2.0 — hybrid enhancement
```

---

# 22. FASE 13 — Multitrack

## Objetivo

Permitir podcasts con una pista por participante.

## Funciones

- varias pistas;
- alineamiento;
- nivelado individual;
- detección de quién habla;
- gate por pista;
- reducción de bleed;
- loudness por voz;
- mixdown;
- ducking de música.

## Modelo

```text
Speaker 1 ─┐
Speaker 2 ─┼─► analysis ► per-track processing ► mix
Music ─────┘
```

## Entregable

```text
v1.3.0 — multitrack podcast processing
```

---

# 23. FASE 14 — Batch, jobs y arquitectura de producción

## Objetivo

Hacer posible el uso real con archivos largos.

## Backend

Separar:

```text
API
Worker
Storage
Job Queue
```

## Estados

```text
UPLOADED
ANALYZING
READY_FOR_PROCESSING
PROCESSING
READY
FAILED
```

## API

```http
POST /jobs
GET  /jobs/{id}
POST /jobs/{id}/process
GET  /jobs/{id}/report
GET  /jobs/{id}/download
```

## Requisitos

- idempotencia;
- cancelación;
- retries;
- progress;
- logs;
- cleanup de temporales.

## Entregable

```text
v1.4.0 — production job system
```

---

# 24. FASE 15 — Métricas y evaluación experimental

## Objetivo

Demostrar técnicamente que el procesamiento funciona.

## Dataset sintético

Partir de:

```text
clean speech
```

Añadir:

- white noise;
- pink noise;
- hum;
- room noise;
- fan;
- keyboard;
- street noise;
- reverberation;
- clipping.

## SNR

Probar:

```text
20 dB
10 dB
5 dB
0 dB
-5 dB
```

## Métricas con referencia limpia

- input SNR;
- output SNR;
- SNR improvement;
- MSE;
- SI-SDR si se implementa;
- STOI si se integra;
- PESQ/POLQA únicamente si la licencia y el entorno lo permiten.

## Métricas sin referencia

- loudness;
- true peak;
- clipping;
- noise floor;
- spectral statistics;
- processing time;
- real-time factor.

## Regresión

Guardar un pequeño conjunto fijo:

```text
tests/regression/audio/
```

Cada cambio importante debe comprobar que no empeora de forma inesperada los resultados.

## Report

Generar:

```text
benchmark_results.json
benchmark_results.csv
benchmark_plots/
```

## Entregable

```text
v1.5.0 — benchmark suite
```

---

# 25. FASE 16 — Historial y presets del usuario

## Objetivo

Transformar una demo en herramienta recurrente.

## Funciones

- proyectos recientes;
- historial;
- re-descarga;
- presets;
- duplicar configuración;
- procesar otra grabación con la misma configuración.

Presets:

```text
Solo Podcast
Remote Interview
Noisy Room
Voice Note
Studio Recording
```

## Entregable

```text
v1.6.0 — reusable workflows
```

---

# 26. Lo que se elimina del camino crítico

El esquema original incluía dos bloques interesantes desde el punto de vista académico, pero no deben ser fases principales del producto.

## FFT desde cero

Mover a:

```text
research/dsp_from_scratch/
```

Puede incluir:

- DFT;
- FFT radix-2;
- STFT propia;
- comparación con NumPy/SciPy.

Valor:

- aprendizaje;
- documentación;
- portfolio técnico.

No usar necesariamente en producción.

## Codec de compresión propio

Mover a:

```text
research/audio_codec/
```

No debe formar parte del MVP.

Para usuarios reales utilizar:

- WAV;
- FLAC;
- MP3;
- AAC;
- Opus cuando corresponda.

Un codec propio puede mantenerse como proyecto educativo adicional.

---

# 27. Orden de procesamiento inicial

Primer pipeline recomendado:

```text
decode
  ↓
channel handling
  ↓
DC removal
  ↓
high-pass / de-hum
  ↓
noise reduction
  ↓
de-plosive
  ↓
de-esser
  ↓
speech leveler
  ↓
compressor
  ↓
loudness normalization
  ↓
true-peak limiter
  ↓
output QC
  ↓
encode
```

El orden debe quedar configurable.

No asumir que todos los módulos se ejecutan siempre.

---

# 28. Sistema de presets

Crear los presets como datos y no como condicionales dispersos.

Ejemplo:

```yaml
balanced:
  denoise:
    enabled: auto
    strength: medium

  leveler:
    enabled: true
    strength: medium

  deesser:
    enabled: auto

  loudness:
    target_lufs: -16.0

  limiter:
    max_true_peak_dbtp: -1.0
```

Esto permitirá cambiar comportamiento sin modificar código.

---

# 29. Sistema de plugins

Para hacer la arquitectura llamativa en portfolio, los procesadores deben registrarse.

Ejemplo:

```python
registry.register("high_pass", HighPassProcessor)
registry.register("dehum", DeHumProcessor)
registry.register("spectral_gate", SpectralGateProcessor)
registry.register("wiener", WienerProcessor)
```

El Decision Engine devuelve nombres y parámetros.

El pipeline instancia los procesadores mediante el registry.

Ventaja:

```text
diagnóstico
   ↓
ProcessingPlan
   ↓
Processor Registry
   ↓
Pipeline
```

Esto permite incorporar nuevos algoritmos sin reescribir el motor.

---

# 30. Versionado del pipeline

Guardar:

```text
pipeline_version
analysis_version
preset_version
```

Ejemplo:

```json
{
  "pipeline_version": "1.3",
  "preset": "balanced",
  "preset_version": "2",
  "processors": []
}
```

Sirve para:

- reproducibilidad;
- debugging;
- comparar resultados;
- benchmark.

---

# 31. Logging

No registrar audio directamente.

Registrar:

```text
job_id
duration
sample_rate
diagnostics
processing_plan
processor durations
warnings
errors
```

Cada processor debe medir su duración.

---

# 32. Seguridad y privacidad

Antes de publicar la app:

- limitar tamaño;
- validar tipos;
- filenames aleatorios;
- evitar path traversal;
- limpiar temporales;
- no ejecutar argumentos FFmpeg procedentes directamente del usuario;
- timeouts;
- límites de CPU/memoria;
- política de borrado.

En UI indicar claramente cuánto tiempo se conserva un fichero.

---

# 33. Testing

## Unit tests

Para:

- métricas;
- detectores;
- filtros;
- decision rules;
- presets.

## Property tests

Ejemplos:

```text
silencio → silencio
zero input → no NaN
output length == input length
```

## Integration tests

```text
upload
→ analyze
→ process
→ export
```

## Regression tests

Pequeños clips de referencia con métricas esperadas.

## Golden files

Evitar exigir igualdad sample-by-sample cuando el algoritmo no la garantiza.

Comparar:

- duración;
- loudness;
- peak;
- métricas;
- tolerancias.

---

# 34. Requisitos de calidad para Codex

Codex debe seguir estas reglas durante todo el desarrollo.

## Código

- type hints;
- funciones pequeñas;
- docstrings en APIs públicas;
- no duplicación;
- separar dominio e infraestructura;
- no hardcodear paths;
- no hardcodear presets;
- no usar global state evitable.

## DSP

Cada algoritmo debe documentar:

- entrada;
- salida;
- unidades;
- rango;
- sample rate assumptions;
- bibliografía o referencia;
- limitaciones.

## Tests

Ninguna nueva funcionalidad se considera terminada sin tests.

## API

Todos los payloads mediante modelos Pydantic.

## Errores

Crear errores de dominio:

```python
UnsupportedAudioFormat
InvalidAudioFile
ProcessingFailed
AnalysisFailed
```

No devolver tracebacks al frontend.

---

# 35. Definition of Done de cada fase

Una fase termina únicamente cuando cumple:

```text
[ ] implementación
[ ] tests
[ ] documentación
[ ] ejemplo reproducible
[ ] integración con pipeline
[ ] API si corresponde
[ ] manejo de errores
[ ] métricas/logs
[ ] revisión de tipos
[ ] lint
```

---

# 36. Prompt operativo para Codex

Para implementar cada fase, utilizar instrucciones similares a:

```text
Implement Phase X from PROJECT_PLAN.md.

Requirements:
1. Read the phase completely before modifying code.
2. Inspect the existing architecture and reuse current abstractions.
3. Do not introduce a second architecture for the same problem.
4. Implement the smallest complete vertical slice.
5. Add unit tests.
6. Add integration tests when the API is affected.
7. Add type hints and public docstrings.
8. Run lint, type checks and tests.
9. Update documentation.
10. Summarize:
   - files changed;
   - architectural decisions;
   - tests added;
   - known limitations.

Do not implement later phases unless required by the current phase.
```

---

# 37. Estrategia de commits

Ejemplos:

```text
feat(audio): add FFmpeg ingestion
feat(analysis): add loudness metrics
feat(diagnostics): detect electrical hum
feat(vad): add energy-based speech activity detector
feat(denoise): implement spectral gate
feat(leveling): add speech gain envelope
feat(mastering): add loudness normalization
feat(pipeline): add explainable decision engine
feat(ui): add A/B comparison player
test(audio): add regression fixtures
```

---

# 38. Milestones recomendados

## Milestone A — Analyzer

Debe existir:

```text
upload
waveform
analysis
diagnostics
```

Demo posible aunque todavía no procese.

## Milestone B — Enhancer

Debe existir:

```text
diagnostics
processing plan
denoise
filters
```

## Milestone C — Podcast Master

Debe existir:

```text
leveler
loudness
limiter
A/B
export
```

Aquí aparece el primer producto realmente utilizable.

## Milestone D — Smart Audio Doctor

Debe existir:

```text
explainable automatic decisions
presets
quality control
reports
```

Este debe ser el principal punto del portfolio.

## Milestone E — Advanced

```text
de-esser
de-plosive
de-reverb
AI enhancement
multitrack
```

---

# 39. Demo que debe aparecer en GitHub

Preparar 4 ejemplos.

## Example 1

```text
Fan noise
```

Mostrar:

- original;
- diagnostic;
- processed;
- SNR estimate.

## Example 2

```text
50 Hz hum
```

Mostrar espectro antes/después.

## Example 3

```text
Uneven speakers
```

Mostrar gain curve.

## Example 4

```text
Complete podcast mastering
```

Mostrar:

```text
Original: -23 LUFS
Final:    -16 LUFS
```

y comparison A/B.

---

# 40. README final

El README debe empezar mostrando el producto, no la teoría.

Orden:

```text
1. Screenshot / GIF
2. What it does
3. Try it
4. Before / after
5. Features
6. How it works
7. Architecture
8. DSP pipeline
9. Benchmarks
10. Installation
11. API
12. Roadmap
```

Evitar comenzar con una explicación larga de FFT.

---

# 41. Métricas de producto

Además de métricas DSP, registrar:

- upload success rate;
- analysis duration;
- processing duration;
- real-time factor;
- job failures;
- porcentaje de diagnósticos activados;
- procesamiento usado;
- export success rate.

No registrar contenido privado del usuario.

---

# 42. Posibles elementos diferenciadores

Una vez el MVP funcione, priorizar uno o dos.

## A. Explainable enhancement

El usuario ve exactamente qué se ha aplicado y por qué.

Esta es la diferenciación recomendada.

## B. Automatic problem map

Timeline:

```text
0:00 ───────────────────────── 45:00

noise   ███████
plosive       ██
sibilance           ███
clipping                 █
```

## C. Per-segment processing

Aplicar algoritmos únicamente donde hacen falta.

Ejemplo:

```text
0:00–05:30  light denoise
05:30–07:10 strong denoise
07:10–20:00 light denoise
```

## D. Quality scorecard

En vez de un score opaco único, mostrar dimensiones:

```text
Noise       ●●●○
Loudness    ●●●●
Dynamics    ●●○○
Clipping    ●●●●
Hum         ●●●●
```

No esconder las métricas originales.

---

# 43. Funcionalidades que NO deben implementarse demasiado pronto

Evitar scope creep con:

- editor de vídeo;
- hosting de podcasts;
- RSS;
- generación automática de capítulos;
- transcripción completa;
- editor textual;
- colaboración;
- pagos;
- app móvil;
- grabación remota;
- marketplace de efectos.

Pueden estudiarse únicamente después de validar el núcleo.

---

# 44. Roadmap resumido

```text
PHASE 0
Project bootstrap
    │
PHASE 1
Audio ingestion
    │
PHASE 2
Analysis engine
    │
PHASE 3
Explainable diagnostics
    │
PHASE 4
VAD + noise profile
    │
PHASE 5
Corrective DSP
    │
PHASE 6
Noise reduction
    │
PHASE 7
Speech leveling
    │
PHASE 8
Podcast mastering
    │
PHASE 9
Decision engine
    │
PHASE 10
Usable web application
    │
    ├────────────► v1.0
    │
PHASE 11
Voice repair
    │
PHASE 12
AI / dereverb
    │
PHASE 13
Multitrack
    │
PHASE 14
Production jobs
    │
PHASE 15
Benchmarks
    │
PHASE 16
History / presets
```

---

# 45. MVP definitivo

El proyecto se considera un MVP real cuando un usuario puede:

```text
1. abrir la web;
2. subir un podcast;
3. reproducirlo;
4. recibir un diagnóstico;
5. entender qué problemas tiene;
6. aceptar una cadena recomendada;
7. procesarlo;
8. comparar A/B;
9. ver las métricas finales;
10. descargar un master.
```

El MVP NO requiere IA.

Un pipeline DSP clásico, bien implementado y bien presentado, ya constituye un proyecto técnicamente fuerte.

---

# 46. Resultado final buscado

El producto debe poder presentarse así:

> **Podcast Audio Doctor is an explainable automatic audio restoration and mastering system for spoken-word recordings. It analyzes recordings, detects common audio problems, builds an adaptive DSP processing chain, improves speech quality and loudness, and shows the user exactly what was changed and why.**

En términos técnicos:

```text
INGEST
   ↓
ANALYZE
   ↓
DIAGNOSE
   ↓
PLAN
   ↓
PROCESS
   ↓
MASTER
   ↓
VALIDATE
   ↓
COMPARE
   ↓
EXPORT
```

Este flujo debe ser el centro del repositorio, de la demo y de la explicación del proyecto en el CV.

---

# 47. Descripción breve para CV

Versión inicial:

> **Podcast Audio Doctor — Automatic Speech Enhancement & Mastering Platform**  
> Built an explainable audio-processing platform for podcasts using Python, FastAPI, React and DSP techniques including VAD, noise profiling, spectral denoising, adaptive speech leveling, loudness normalization and true-peak limiting. Designed an automatic decision engine that diagnoses recording issues and dynamically constructs the processing chain, with before/after waveform comparison and quantitative quality reports.

Actualizar esta descripción a medida que las funcionalidades estén realmente implementadas.
