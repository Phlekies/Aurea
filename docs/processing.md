# Procesamiento correctivo · v0.6.0

La fase 5 añade los primeros procesadores que modifican el audio: eliminación de DC, filtro paso alto adaptativo, eliminación de zumbido y ajuste de nivel previo. El original nunca se toca: cada renderización crea una versión corregida aparte, con su manifiesto, y se mide antes y después.

```text
análisis + diagnóstico ──► decision_engine ──► ProcessingPlan ──► registry ──► runner ──► versión corregida
                                                     ▲                                        │
                                          usuario (activar/desactivar)          medición después + informe
```

## Contrato de procesadores

Cada procesador cumple la interfaz del plan, `AudioProcessor.process(audio: AudioBuffer, params) -> AudioBuffer`, y además `validate(params, sample_rate, channels)` y `open(params, sample_rate, channels)`, que devuelve un procesador de bloques con estado (`backend/app/processors/base.py`). `process` es literalmente ese flujo aplicado a un único bloque, así que las dos entradas no pueden divergir. Los filtros IIR conservan su estado entre bloques: el resultado no depende del tamaño de bloque, y las pruebas lo comprueban.

Ningún procesador cambia la frecuencia de muestreo, la duración ni los canales. El *runner* comprueba después de cada paso que las muestras son finitas y tienen la misma forma. Si el pico renderizado supera la escala completa, aplica una única ganancia de seguridad en una segunda pasada para dejarlo en −0,1 dBFS. Así nunca recorta, y la ganancia aplicada queda como aviso en el informe.

Los procesadores se registran por nombre (`backend/app/pipeline/registry.py`). El plan contiene nombres y parámetros, y el *runner* instancia los procesadores a través del registro, de modo que un algoritmo nuevo se añade sin modificar el motor.

| Procesador | Parámetros | Método |
|---|---|---|
| `dc_removal` | `offsets`: uno por canal, \|x\| ≤ 0,25 | Resta la media medida en la fase 2. Exacto, de fase cero y sin transitorio; no sigue un DC que varíe |
| `high_pass` | `cutoff_hz` 20–300 (< 0,45·fs), `order` 2/4/6 | Butterworth en secciones de segundo orden: −3 dB en el corte, 6 dB/oct por orden. Causal |
| `dehum` | `fundamental_hz` 40–70, `harmonics` 1–10, `q` 5–100, `attenuation_db` 3–60 | Un biquad de pico RBJ con ganancia −atenuación por línea. Profundidad exacta en el centro y mismo ancho en Hz para todos los armónicos (Q·k). Omite líneas ≥ 0,45·fs |
| `pre_gain` | `gain_db` −24…+24 | Ganancia constante |

Medidas en las pruebas: el paso alto de orden 4 atenúa 24,1 dB una octava por debajo del corte y menos de 0,01 dB dos octavas por encima. El de-hum atenúa −30 dB en cada línea y menos de 0,35 dB entre armónicos.

Referencias: S. Butterworth, *On the theory of filter amplifiers*, Wireless Engineer 7, 1930; R. Bristow-Johnson, *Cookbook formulae for audio EQ biquad filter coefficients* (filtro de pico).

## Reglas de decisión

Las reglas están centralizadas en `backend/app/pipeline/decision_engine.py`, con todos los umbrales en `CorrectiveRules`. El plan siempre contiene los cuatro pasos, en orden DC → paso alto → de-hum → ganancia, con `enabled`, `reason`, `source_diagnostic`, `confidence` y `evidence`. Un diagnóstico con confianza < 0,55 nunca activa un paso.

- **DC.** Se activa si algún canal tiene |DC| ≥ 0,001 y resta el valor medido de cada canal.
- **Paso alto: el corte mínimo razonable.** Se activa si hay ruido grave detectado. Para cada corte candidato (60, 70, 80 y 100 Hz) se calcula cuánto reduce un Butterworth de orden 4 la energía del fondo entre 20 y 80 Hz, y se elige el corte más bajo que consigue ≥ 10 dB. Si ninguno lo logra, se usa 100 Hz y se indica la reducción real. La evidencia muestra la reducción de cada candidato y la energía total estimada que se elimina.
- **Espectro del fondo.** La elección usa un espectro nuevo del perfil de ruido: 0–300 Hz con bins de 4 Hz, a partir de ventanas de 0,25 s de fondo ininterrumpido. Con los bins de 33 Hz de las ventanas de 30 ms, la fuga espectral sobreestimaba el corte necesario en un preset (por ejemplo, ruido a 25 Hz pedía 70 Hz). Con la resolución fina coincide con la teoría:

  | Ruido grave | Reducción estimada / teórica | Corte elegido |
  |---|---|---|
  | 25 Hz | 29,4 / 30,4 dB (con 60 Hz) | 60 Hz |
  | 40 Hz | 13,9 / 14,2 dB (con 60 Hz) | 60 Hz |
  | 55 Hz | 13,05 / 13,2 dB (con 80 Hz) | 80 Hz |
  | 70 Hz | 12,5 / 12,6 dB (con 100 Hz) | 100 Hz |

  Sin ventanas largas de fondo se recurre al perfil de 30 ms, y la evidencia indica qué ventana se usó.
- **De-hum.** Se activa si hay zumbido detectado. Usa la fundamental detectada (50/60 Hz), tantas líneas como el armónico más alto destacado, Q = 30 (≈ 1,7 Hz de ancho a 50 Hz) y una atenuación igual al contraste de la línea más fuerte + 6 dB, limitada a 12–48 dB.
- **Ganancia previa.**
  - Con nivel bajo detectado, sube hacia −24 LUFS sin que el true peak supere −3 dBTP.
  - Con poco margen de pico, baja el true peak a −3 dBTP para dejar margen al procesado.
  - Solo se aplica si el ajuste es de 1 dB o más.

Los motivos se escriben en español con coma decimal y se muestran en la interfaz bajo «¿Por qué?».

## Servicio, API y manifiesto

| Ruta | Resultado |
|---|---|
| `GET /api/audio/{id}/processing/plan` | Plan recomendado; requiere un análisis (404 `analysis_not_found` si falta) |
| `POST /api/audio/{id}/process` | Cuerpo opcional `{"plan": …}`; sin cuerpo usa el plan recomendado. Devuelve el informe (200) |
| `GET /api/audio/{id}/processing` | Último informe publicado (404 `processing_not_found` si no hay) |
| `GET /api/audio/{id}/processed/waveform` | Picos de la versión corregida |
| `GET /api/audio/{id}/processed/stream` | WAV de 16 bits de la versión corregida, con HTTP Range |

Validación de un plan enviado:

- Nombres de procesador desconocidos, parámetros fuera de rango, parámetros no definidos o campos extra devuelven 422 (`invalid_processing_plan`, que indica el paso) antes de procesar.
- No se ejecuta nada que venga del usuario salvo procesadores registrados con parámetros numéricos validados.

Publicación:

- La versión corregida se genera en un directorio temporal y se publica renombrando el directorio `processed/` completo. Contiene `processed.wav` (float32), `playback.wav`, `waveform.json` y `report.json`, el manifiesto de procesado.
- Un plan con los mismos pasos ejecutables reutiliza el resultado publicado, y uno distinto lo sustituye.
- Un fallo devuelve 503 `processing_failed`, limpia los temporales y conserva el original.

El informe registra:

- el plan aplicado, los parámetros validados y la duración de cada paso;
- la versión del pipeline, el tiempo total y el factor de tiempo real;
- los avisos, incluida la ganancia de seguridad;
- las métricas antes y después: pico, true peak, loudness, RMS, DC, % de subgraves, ruido de fondo, SNR aproximada y problemas detectados. El «después» vuelve a pasar por el analizador y el diagnóstico completos.

**Daño no reparado.** Reducir el nivel puede alejar las crestas recortadas de la escala completa y ocultarlas al detector de saturación, pero no las repara. Por eso, si había saturación antes, el «después» la sigue indicando y el informe añade un aviso explícito, hasta que exista un procesador de *declipping* (fase 11).

El log `audio_processed` registra la duración, el factor de tiempo real, la versión, los pasos activos, los segundos por paso y la ganancia de seguridad, sin contenido de audio.

## Rendimiento

Renderizar 10 minutos de audio estéreo a 48 kHz con los cuatro procesadores tarda 1,5 s (factor de tiempo real 0,0024). Por paso: DC 0,13 s, paso alto 0,31 s, de-hum de 8 líneas 0,46 s y ganancia 0,06 s. La medición posterior (análisis y diagnóstico del resultado) domina el tiempo total. La memoria depende del bloque (65 536 muestras) y del estado de los filtros, no de la duración.

## Límites conocidos

- Los filtros son causales: el paso alto gira la fase de los graves y puede cambiar ligeramente la forma de onda y el pico. En las pruebas, +1,6 dB en una voz sintética; por eso existe la protección de pico.
- El DC se resta como constante: una deriva lenta la elimina el paso alto, no este paso.
- El de-hum solo actúa sobre armónicos de una fundamental estable. Un zumbido que deriva más de ±1 Hz escapa parcialmente de cortes tan estrechos.
- El detector de zumbido de la fase 3 compara cada línea con bins situados a 5–10 Hz. Un ruido grave tonal justo en esa zona (por ejemplo, 40 Hz frente a 50 Hz) puede enmascarar el zumbido y dejar el de-hum sin activar.
- La reducción de ruido, el nivelado y el mastering llegan en las fases 6–8. El modo manual con edición de parámetros no forma parte de esta fase: la interfaz permite activar y desactivar pasos (modo asistido).

## Ejemplo reproducible

```powershell
.\.venv\Scripts\python.exe scripts/smoke_audio.py --base-url http://127.0.0.1:5173 --diagnostic-demo
```

Cada smoke pide el plan recomendado, lo envía, comprueba el manifiesto y la reproducción parcial de la versión corregida y verifica la caché. Con `--diagnostic-demo` exige además que el zumbido de 50 Hz detectado antes haya desaparecido después.

En la interfaz, la demo de diagnóstico muestra:

- **De-hum** a 50 Hz con −35 dB en una línea.
- **Ganancia** de −3 dB por falta de margen de pico.
- **Resultado:** el zumbido y la falta de margen desaparecen. La saturación sigue indicada, con el aviso de que no se repara.
