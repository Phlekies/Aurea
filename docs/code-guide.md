# Guía rápida del código de Aurea

**Base: fase 9, v1.0.0-alpha.** React/TypeScript dibuja la interfaz; FastAPI recibe las peticiones; NumPy/SciPy procesan el audio y FFmpeg lo decodifica, mide y masteriza. El original se conserva y los resultados se guardan aparte.

## Sigue este recorrido

```text
AudioWorkspace → API → AudioService.ingest
                       ↓
              AnalysisService.analyze
                       ↓
       analyze_audio → diagnose_audio → VAD + perfil de fondo
                       ↓
        decide + presets.toml → ProcessingPlan completo
                       ↓
       AutomaticService.process (una reserva para ambas etapas)
                       ↓
        ProcessingService.process → prepare → run_plan
                       ↓
       volver a medir → guardar informe → escuchar y comparar
                       ↓
        MasteringService.master → master_audio → Output QC
                       ↓
             escuchar máster → descargar WAV verificado
```

## Dónde mirar

| Parte | Archivo y funciones principales |
|---|---|
| Arranque y conexión de servicios | [backend/app/main.py](../backend/app/main.py): `create_app()`; configuración en `config.py` |
| Pantalla, carga y estado del archivo | [AudioWorkspace.tsx](../frontend/src/features/audio/AudioWorkspace.tsx): `AudioWorkspace()`; llamadas HTTP en `frontend/src/api/` |
| Rutas HTTP | `backend/app/api/audio.py`, `analysis.py`, `processing.py`, `mastering.py`: traducen peticiones a métodos de servicio |
| Ingesta y almacenamiento | [services/ingestion.py](../backend/app/services/ingestion.py): `AudioService.ingest()`, `get()`, `cleanup()` |
| Decodificación y reproducción | [audio/ffmpeg.py](../backend/app/audio/ffmpeg.py): `probe_audio()`, `decode_audio()`, `create_playback()`; `waveform.py`: `create_waveform()` |
| Métricas y caché de análisis | [analysis/analyzer.py](../backend/app/analysis/analyzer.py): `analyze_audio()`; `services/analysis.py`: `AnalysisService.analyze()` y `get()` |
| Diagnósticos | [diagnostics/engine.py](../backend/app/diagnostics/engine.py): `diagnose_audio()`; las ocho reglas están en `detectors.py` |
| Voz y ruido de fondo | `analysis/frames.py`: ventanas; [vad.py](../backend/app/analysis/vad.py): `EnergyVoiceActivityDetector.detect()`; `noise.py`: `measure_noise()`, `noise_profile()`, `estimate_snr_db()` |
| Elegir ajustes y objetivo final | [pipeline/decision_engine.py](../backend/app/pipeline/decision_engine.py): `decide()` centraliza correcciones, ruido, dinámica, confianza y `mastering_decisions()`; `noise_plan.py` y `dynamics_plan.py` son fachadas compatibles |
| Configurar intensidad | `pipeline/presets.toml`: Natural/Balanced/Studio; `presets.py`: `load_processing_presets()`; `domain/presets.py`: umbrales comunes y validación |
| Ejecutar en una acción | [services/automatic.py](../backend/app/services/automatic.py): `AutomaticService.process()` valida el plan y reserva capacidad para corregir y masterizar sin mezclar generaciones |
| Ejecutar y guardar | [services/processing.py](../backend/app/services/processing.py): `ProcessingService.process()`; `pipeline/runner.py`: `prepare()` valida y `run_plan()` ejecuta por bloques |
| Algoritmos DSP | `backend/app/processors/`: DC, paso alto, de-hum, ganancia, `NoiseReducer`, `SpeechLevelerProcessor` y `CompressorProcessor`; `pipeline/registry.py`: `default_registry()` los registra por nombre |
| Ganancia aplicada | `SpeechLevelerStream.gain_envelope()` y `CompressorStream.gain_envelope()`; `runner.py` guarda ambas en `ProcessingReport.gain_envelopes`, descargables en la interfaz |
| Masterizar y comprobar | [mastering/engine.py](../backend/app/mastering/engine.py): `master_audio()` normaliza y limita; `meter.py`: `measure_loudness()`, `inspect_pcm()`; `qc.py`: `output_qc()` valida el WAV final |
| Objetivos y descarga final | `mastering/presets.toml` configura LUFS/true peak; [services/mastering.py](../backend/app/services/mastering.py): `MasteringService.master()`, `report()`, `download()` publica y verifica SHA-256; `MasteringPanel.tsx` muestra el resultado |
| Contratos de datos | `backend/app/domain/`: `AudioAsset`, `AudioAnalysis`, `Diagnostic`, `SpeechActivity`, `NoiseProfile`, `ProcessingPlan`, `ProcessingReport`, `MasteringReport` |
| Mostrar los resultados | `AnalysisPanel`, `DiagnosticsPanel`, `ActivityPanel`, [CorrectionsPanel.tsx](../frontend/src/features/audio/CorrectionsPanel.tsx); `AudioPlayer.tsx`: `WavePlayer()` |

## Para cambiar algo

- **Una decisión o umbral:** empieza por `pipeline/presets.toml`, los valores comunes de `domain/presets.py` y `pipeline/decision_engine.py`.
- **Un objetivo de publicación:** edita `mastering/presets.toml`; no hace falta modificar el algoritmo.
- **Un algoritmo:** modifica/añade un procesador con `validate()`, `open()` y `process()`, y regístralo en `registry.py`.
- **Un campo de la API:** actualiza el modelo de `domain/`, el esquema Zod de `frontend/src/api/` y su presentación.
- **Una pantalla o color:** `frontend/src/features/audio/` y `styles.css`. La paleta es Claridad Acústica.

Los audios y temporales viven en la carpeta de almacenamiento configurada (`data/audio` por defecto) y no se suben a Git. Pruebas: `backend/tests/` y archivos `*.test.ts(x)` del frontend. **`npm run check`** ejecuta lint, tipos, pruebas y build; `scripts/smoke_audio.py` prueba el flujo HTTP completo. Los detalles de cada fase están en [progress.md](progress.md).
