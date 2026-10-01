# Nivelado de voz y compresión · v0.8.0

La fase 7 estabiliza el volumen hablado. Conserva duración, canales, muestreo y original. No establece un loudness final de exportación: ese objetivo, el limiter y el mastering pertenecen a la fase 8.

## Recorrido y decisiones

```text
DC → paso alto → de-hum → reducción de ruido → ganancia previa → nivelador de voz → compresor
                                                                          ↓
                                       informe + curvas de ganancia + comparación antes/después
```

[dynamics_plan.py](../backend/app/pipeline/dynamics_plan.py): `add_dynamics()` añade ambos pasos a la recomendación, con motivos y evidencia. Los umbrales están en `DYNAMICS_RULES`.

- Se considera voz disponible con al menos 0,3 s marcados por el VAD y RMS de voz superior a −50 dBFS y al fondo +9 dB.
- El nivelador se activa si el RMS hablado se aleja ≥4 dB de −24 dBFS o la diferencia entre los percentiles 90 y 10 de las ventanas de voz es ≥6 dB. Se excluyen ventanas cuyo centro queda fuera de la voz o demasiado cerca del fondo.
- El compresor se activa con voz disponible y variación ≥6 dB, poco margen de pico con confianza ≥0,55, o crest factor >18 dB. Los valores se estiman sobre el original; el resultado se vuelve a medir.
- El aumento global de `pre_gain` queda desactivado en la recomendación: puede subir las pausas, especialmente en una grabación solo de ruido. La reducción previa para dejar margen de pico sigue disponible. Un plan manual puede activar la ganancia global.

## Nivelador

[speech_leveler.py](../backend/app/processors/speech_leveler.py): `SpeechLevelerProcessor.validate()/open()` y `SpeechLevelerStream.process()`.

1. Promedia la potencia de los canales: ambos reciben la misma ganancia y conservan su balance.
2. Un detector RMS causal sigue el nivel medio; otro de 10 ms protege los comienzos fuertes y frena el refuerzo del fondo.
3. El objetivo RMS genera una petición de ganancia limitada. El VAD original limita dónde se puede aplicar; sus bordes llevan transiciones de coseno de 50 ms. La energía añade una transición suave de 6 dB por encima del suelo `max(−50, fondo+9)`.
4. Un filtro de primer orden suaviza la petición de ganancia en dB y la ponderación de actividad se aplica otra vez a la ganancia real. Fuera de voz la ganancia del nivelador es exactamente 0 dB, aunque su estado interno siga decayendo.

| Parámetro | Por defecto | Rango |
|---|---:|---:|
| `target_rms_dbfs` | −24 dBFS | −40…−12 |
| `max_boost_db` / `max_cut_db` | +8 / −12 dB | 0…12 / 0…24 |
| `window_ms` / `smoothing_ms` | 300 / 600 ms | 100…1000 / 100…2000 |
| `noise_floor_dbfs` | −60 dBFS sin perfil | −300…0 |
| `speech_starts_seconds` / `speech_ends_seconds` | Tramos del VAD | Ordenados, sin solapamiento, 0…1800 s |

La recomendación utiliza el RMS del perfil de fondo como suelo conservador. Tras reducción de ruido no lo rebaja. Una ganancia previa manual desplaza también ese suelo en el runner, para no confundir el fondo aumentado con voz.

## Compresor

[compressor.py](../backend/app/processors/compressor.py): `gain_reduction_db()` calcula la curva estática; `CompressorStream.process()` aplica detección de picos enlazada en estéreo, retención con liberación exponencial y suavizado de ataque en dB. Es causal, sin lookahead.

| Parámetro | Por defecto | Rango |
|---|---:|---:|
| `threshold_dbfs` | −18 dBFS | −60…0 |
| `ratio` | 2:1 | 1…20 |
| `knee_db` | 6 dB | 0…24 |
| `attack_ms` | 10 ms | 0,1…200 |
| `release_ms` | 150 ms | 10…2000 |
| `makeup_gain_db` | 0 dB | −12…12 |

La rodilla interpola de forma cuadrática entre el paso sin reducción y la relación de compresión. Véase la curva estática en [Giannoulis, Massberg y Reiss, JAES 2012](https://eecs.qmul.ac.uk/~josh/documents/2012/GiannoulisMassbergReiss-dynamicrangecompression-JAES2012.pdf). Esta implementación usa su propia combinación de liberación y ataque; no pretende reproducir un equipo analógico concreto. Los filtros conservan estado con [SciPy `lfilter`](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.lfilter.html).

## Curvas y seguridad

`gain_envelope()` de cada flujo devuelve la ganancia realmente aplicada, incluyendo compensación en el compresor. El runner la recoge después de vaciar la cola espectral. `ProcessingReport.gain_envelopes` guarda `processor`, `step_index`, `times_seconds` y `gain_db`.

Se conserva una muestra por 0,1 s y la última muestra del audio. La posición es `ceil(k·sample_rate/10)`: admite frecuencias nativas que no sean múltiplos de 10 sin exceder el límite de 18.002 puntos en 30 min. Los tiempos representan el frame real dividido por la frecuencia de muestreo. La curva es una representación temporal reducida, no permite reconstruir cada muestra de ganancia.

Las curvas están referidas a su propio paso. La posterior ganancia global `safety_gain_db` se registra aparte. Si hay sobrecarga, el runner conserva su reducción constante a −0,1 dBFS sin recortar; todavía no hay limiter de true peak. API y caché comprueban tiempos crecientes, valores finitos, longitud, duración, paso activo correspondiente y presencia de todas las curvas necesarias. La interfaz permite descargar el manifiesto completo en JSON.

## Ejemplo y pruebas

```powershell
.\.venv\Scripts\python.exe scripts/smoke_audio.py --dynamics-demo --write-sample data/qa/dynamics-demo.wav
.\.venv\Scripts\python.exe scripts/smoke_audio.py --base-url http://127.0.0.1:5173 --dynamics-demo
npm run check
```

La demo de 12 s contiene frases armónicas sintéticas: voz baja, voz alta y un cambio progresivo, con pausas y ruido conocido. El smoke exige reducir la diferencia de RMS entre frases comparables y guardar las curvas. Se prueba también en Docker en CI.

Pruebas: cambio de volumen entre dos voces, rampas, silencio, ruido con VAD falso y su cola, límites de ganancia, estéreo, frecuencias impares, 30 minutos, independencia de bloques y de curvas, alineación tras STFT, original intacto, caché corrupta y cambios de controles. El compresor comprueba transferencia, continuidad de rodilla y tiempos de ataque/liberación. Una prueba HTTP comprueba que la cadena recomendada nunca aumenta una grabación solo de ruido.

Límites: el nivelador mide RMS, no loudness perceptual. Depende del VAD heurístico y puede omitir una voz muy baja respecto a otra; no identifica hablantes ni corrige decisiones del VAD. Si el VAD mantiene un tramo continuo después de pasar bruscamente de voz a ruido, la puerta causal puede conservar ganancia unos 40 ms; las pruebas comprueban retorno a 0 dB desde 50 ms. Se ha validado con señales sintéticas, sin corpus real ni evaluación perceptual. Los indicadores de pérdida de energía de la fase 6 comparan la cadena entera y pueden reflejar una atenuación deliberada del nivelador o del compresor.
