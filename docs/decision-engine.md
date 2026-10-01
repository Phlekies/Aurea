# Motor de decisiones — fase 9

**Producto y cadena: v1.0.0-alpha. Configuración: 1.0.0-alpha.1.** La versión Python se normaliza como `1.0.0a0`; análisis y diagnóstico conservan sus versiones independientes. La masterización pasa a `0.9.1` por la corrección de reintentos descrita abajo.

## Recorrido

`decide(AudioAnalysis, list[Diagnostic], ProcessingPreset, MasteringPreset) -> ProcessingPlan` es una función pura de `backend/app/pipeline/decision_engine.py`. Recibe mediciones y configuración, sin leer archivos ni ejecutar DSP. Los diagnósticos duplicados se rechazan; cambiar su orden no cambia el JSON del plan.

El plan contiene siete correcciones ordenadas en `steps`: DC → paso alto → de-hum → reducción de ruido → ganancia previa → nivelado de voz → compresión. `mastering_steps` añade normalización de loudness y protección de true peak. Estas dos decisiones se ejecutan juntas mediante el motor de masterización existente, con medición posterior del WAV PCM24 y ocho controles obligatorios.

Cada decisión guarda `enabled`, `parameters`, `reason`, `source_diagnostic`, `confidence`, `evidence` y `decision`:

| Estado | Comportamiento |
|---|---|
| `automatic` | Activo: evidencia suficiente y una corrección justificada |
| `recommended` | Inactivo: propuesta con parámetros preparados, pendiente de revisión |
| `disabled` | Inactivo: sin problema suficiente, evidencia baja o datos insuficientes |
| `manual` | Elección del usuario al cambiar una casilla o parámetro |

El renderizado respeta las elecciones manuales; no reactiva una recomendación al pulsar «Procesar y crear máster». Cambiar el preset prepara una propuesta nueva y sustituye los ajustes; cambiar solo el objetivo final conserva las correcciones revisadas. El resultado identifica el preset y los parámetros realmente aplicados.

## Configuración y confianza

`pipeline/presets.toml` define los tres presets; `domain/presets.py` contiene los valores comunes y valida rangos y números finitos. `pipeline/presets.py` mezcla valores comunes y sobrescrituras y rechaza nombres desconocidos, presets ausentes y errores de escritura. Al modificar la política, aumenta su `version`.

| Parámetro | Natural | Balanced (predeterminado) | Studio |
|---|---:|---:|---:|
| Evidencia para aplicación automática | ≥0,90 | ≥0,85 | ≥0,80 |
| Evidencia mínima para recomendar | ≥0,55 | ≥0,55 | ≥0,55 |
| Severidad mínima del diagnóstico | 0,10 | 0,05 | 0,03 |
| Reducción de ruido | Wiener suave | Wiener equilibrado | Wiener intenso |
| Corte máximo de paso alto | 80 Hz | 100 Hz | 100 Hz |
| Atenuación máxima del de-hum | 24 dB | 48 dB | 48 dB |
| Referencia RMS de voz | −26 dBFS | −24 dBFS | −22 dBFS |
| Refuerzo máximo de voz | 4 dB | 8 dB | 8 dB |
| Suavizado del nivelado | 800 ms | 600 ms | 400 ms |
| Umbral / relación de compresión | −16 dBFS / 1,6:1 | −18 dBFS / 2:1 | −20 dBFS / 3:1 |

Los scores son heurísticos, no probabilidades calibradas. Los detectores actuales asignan 0,70 al retumbo y al ruido estacionario: estos quedan pendientes de revisión con los tres presets. Una detección no garantiza una aplicación automática. Studio permite más intervención y acepta más evidencia que Natural, pero tampoco activa evidencia baja.

Para nivelado/compresión se exige voz suficiente y al menos 9 dB de contraste sobre el fondo. El score combina una base de 0,55, hasta 0,20 por contraste adicional, 0,10 por duración de voz y 0,10 por duración del perfil de fondo; se limita a 0,95. Si el compresor depende únicamente de un diagnóstico de poco margen, se utiliza el menor score entre este y el de voz. Una voz fiable no convierte un diagnóstico incierto en una aplicación automática.

DC y reducción de ganancia previa utilizan mediciones directas, sin exigir confianza de un clasificador. El aumento global previo sigue desactivado para conservar las pausas. La normalización final sí cambia el nivel global hacia el objetivo elegido, incluido el fondo; sin loudness medible o en clips de menos de 0,4 s queda desactivada.

## API y ejecución

- `GET /api/audio/processing/presets`: configuración de Natural, Balanced y Studio.
- `GET /api/audio/{id}/processing/plan?preset=natural&mastering_preset=podcast_standard`: plan completo. `algorithm` y `strength` son opciones adicionales de reducción; si se omiten se usan las del preset.
- `POST /api/audio/{id}/auto-process`: cuerpo opcional `{"preset":"balanced","mastering_preset":"podcast_standard"}` o `{"plan": …}` revisado. Devuelve `{"processing": …, "mastering": …}`.
- `POST /api/audio/{id}/process` y `POST /api/audio/{id}/master` conservan la ejecución por separado.

`AutomaticService.process()` valida versión, preset, objetivo, parámetros y decisiones finales antes de publicar nada. Mantiene una única reserva de capacidad durante ambas etapas para que el máster corresponda a la misma generación de correcciones. El control de calidad y el SHA-256 siguen siendo obligatorios para descargar.

La caché de correcciones compara tanto ejecución como plan completo: dos resultados con parámetros iguales pero distinta política o explicación no se presentan como el mismo preset. Un plan automático desactualizado, terminal alterado o procesador desconocido devuelve 422; capacidad ocupada devuelve 503. Si falla la masterización, las correcciones publicadas se conservan y la interfaz las recupera para escuchar y reintentar; no ofrece un máster sin verificar.

## Validación reproducible

`scripts/benchmark_decisions.py` audita seis señales sintéticas de 6 s a 16 kHz: voz estable, zumbido débil, ruido estacionario, cambio de volumen, ruido solo y silencio. Ejecuta los tres presets, comprueba determinismo, formato y original intacto, y masteriza los casos medibles con QC independiente:

```powershell
.\.venv\Scripts\python.exe scripts/benchmark_decisions.py --output data/qa/decisions.json
```

Resultado local: **15/15 másteres medibles aprobados**, entre −16,2 y −15,7 LUFS, con true peak por debajo de −1 dBTP. Los tres silencios dejan la masterización desactivada. El ruido solo no activa nivelado, compresión ni aumento previo. Los ruidos con confianza moderada permanecen propuestos para revisión; el benchmark no los acepta silenciosamente.

La auditoría detectó un caso de voz variable con Studio cuyo primer candidato lineal quedaba en −16,9 LUFS. El reintento anterior modificaba `offset`, pero la inicialización lineal de FFmpeg lo sustituye por `I − measured_I` ([implementación oficial de loudnorm](https://github.com/FFmpeg/FFmpeg/blob/n8.0/libavfilter/af_loudnorm.c#L735-L749)). Ahora se corrige el objetivo interno de ese candidato a partir del WAV remedido, conservando el objetivo de publicación y sus ocho controles. El resultado llega a −16,0 LUFS y −2,64 dBTP; una regresión cubre este caso.

Los cuatro smoke HTTP ejecutan el plan completo, aceptación manual en las demos que la necesitan, QC, descarga PCM24 y reutilización. En la demo de dinámica de 12 s, Balanced automático reduce la diferencia RMS de las frases de 18,6 a 11,1 dB. La interfaz muestra −15,9 LUFS y −4,6 dBTP en el máster, y se verifica la reproducción hasta el final.

## Límites

Validación sintética de reglas y conservación de señal; falta calibración con un corpus de podcasts anotado y evaluación perceptual. El score de voz puede aceptar música o un ruido coloreado; no identifica hablantes. Natural no garantiza menos pasos activos: sus objetivos y límites pueden justificar correcciones diferentes. El control de salida comprueba el formato y niveles, no que hayan desaparecido todos los problemas ni que se haya reparado clipping del original. La exportación multiformato y la experiencia de proyectos siguen en la fase 10.
