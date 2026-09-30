# Actividad de voz y perfil de ruido · v0.5.0

La fase 4 separa la grabación en voz, fondo y silencio digital, estima el ruido de fondo a partir de las pausas y calcula una SNR aproximada. No modifica el audio. La misma segmentación alimenta los diagnósticos de la fase 3, la línea temporal de la interfaz y el perfil de ruido publicado.

```text
decodificado ──► ventanas de 30 ms ──► VAD ──► voz / fondo / silencio
                                          │
                                          └──► ventanas de fondo ──► perfil de ruido ──► SNR aproximada
                                                                      └──► diagnósticos
```

## Interfaz intercambiable

Todo detector implementa `VoiceActivityDetector.detect(audio: ActivityInput) -> SpeechActivity` (`backend/app/analysis/vad.py`). `ActivityInput` contiene la ruta del WAV decodificado de confianza, su frecuencia de muestreo, canales, duración y las características de ventana ya calculadas. Un backend basado en ventanas reutiliza esas características sin volver a leer el audio; uno basado en muestras (WebRTC, Silero) puede leer la ruta por bloques. Los backends se registran en `VAD_BACKENDS` y se crean con `create_vad(nombre)`. `diagnose_audio(..., detector=...)` acepta cualquier implementación, y las pruebas verifican que diagnóstico y perfil siguen al backend inyectado.

El único backend actual es `energy`.

## Ventanas compartidas

`backend/app/analysis/frames.py` divide el audio en ventanas contiguas de 30 ms (la última puede ser más corta) y guarda solo escalares por ventana: potencia AC (media cuadrática tras quitar DC por canal), energía por bandas 20–80 / 80–4000 / 4000–10000 Hz (periodograma Hann de una cara, promedio de potencia entre canales nativos), planitud espectral por encima de 20 Hz y tasa de cruces por cero por canal. No se mezcla el estéreo: canales en oposición no se cancelan. La memoria depende del número de ventanas (60 000 como máximo con el límite de 30 minutos), no de las muestras.

## VAD por energía (v0.5.0)

1. **Niveles.** Nivel de cada ventana en dBFS, limitado a −100 dB. El percentil 10 es el *nivel de fondo* y el percentil 90 el *nivel activo*, sobre todas las ventanas.
2. **Histéresis.** Una ventana **entra** en voz si su nivel alcanza `max(−60 dBFS, activo − 14 dB, fondo + 6 dB)` y su espectro parece sonoro: ≥ 12 % de la energía de banda entre 80 y 4000 Hz, planitud < 0,6 y ZCR < 0,45. **Permanece** en voz mientras el nivel sea ≥ `max(entrada − 6 dB, fondo + 3 dB)`. Así, consonantes sordas y finales de palabra siguen siendo voz tras un inicio sonoro.
3. **Suavizado temporal.** Se unen pausas de menos de 0,21 s entre tramos de voz, se eliminan tramos de voz de menos de 0,09 s y se amplía cada tramo 0,06 s antes y 0,12 s después (*hangover*), para que las colas de la voz no contaminen el fondo.
4. **Etiquetas.** El resto es `silence` si la ventana está a −90 dBFS o menos y `noise` en otro caso.

La salida `speech_activity` contiene segmentos contiguos y fusionados `{label, start_seconds, end_seconds}` que cubren toda la grabación (la máscara temporal), las duraciones de cada etiqueta, el porcentaje de voz, el nivel RMS medio de la voz y todos los umbrales aplicados en `parameters`. Los umbrales en dB son `null` si la grabación es silencio digital.

Referencias: L. R. Rabiner y M. R. Sambur, *An algorithm for determining the endpoints of isolated utterances*, Bell System Technical Journal 54(2), 1975 (umbrales de energía y ZCR con histéresis); M. H. Moattar y M. M. Homayounpour, *A simple but efficient real-time voice activity detection algorithm*, EUSIPCO 2009 (energía, planitud espectral y duraciones mínimas).

## Perfil de ruido

`backend/app/analysis/noise.py` selecciona las ventanas `noise` de al menos media ventana cuya potencia no supera 4× (+6 dB) la mediana de las ventanas de fondo. Esto excluye golpes, toses o clics aislados que no forman parte del ruido de fondo. Una segunda lectura por bloques promedia el periodograma de esas ventanas (media tipo Welch de ventanas Hann de 30 ms).

| Campo | Significado | Unidad / rango |
|---|---|---|
| `frame_count`, `duration_seconds` | Ventanas y tiempo de fondo usados | ventanas, s |
| `rms_dbfs` | Nivel medio del fondo (potencia media ponderada por duración) | dBFS |
| `floor_dbfs` | Percentil 10 del nivel de las ventanas de fondo | dBFS |
| `spectral_flatness` | Planitud media: 1 ruido blanco, 0 tonal | 0–1 |
| `relative_power_std` | Coeficiente de variación de la potencia entre ventanas | ≥ 0 |
| `spectral_stability` | Similitud coseno media entre PSD de grupos consecutivos de 10 ventanas | 0–1 |
| `frequencies_hz`, `psd_dbfs_per_hz` | PSD media del fondo, resolución ≈ 33 Hz | Hz, dBFS/Hz |
| `low_window_count`, `low_frequencies_hz`, `low_psd_dbfs_per_hz` | Desde v0.6.0: PSD 0–300 Hz con bins de 4 Hz, de ventanas Hann de 0,25 s tomadas solo de tramos de fondo ininterrumpido | ventanas, Hz, dBFS/Hz |

El espectro grave fino resuelve ruido por debajo de 100 Hz que los bins de 33 Hz mezclan por fuga espectral. Lo usa el filtro paso alto adaptativo ([procesamiento correctivo](processing.md)). Está vacío si no hay 0,25 s seguidos de fondo. El perfil queda vacío (estadísticas `null`, listas vacías) cuando no hay fondo seleccionable. Es determinista e independiente del tamaño de bloque de lectura; las pruebas lo comprueban.

## SNR aproximada

`estimated_snr_db = 10·log10((P_voz − P_fondo) / P_fondo)`, donde `P_voz` es la potencia media de las ventanas de voz y `P_fondo` la del perfil. Restar el fondo corrige que las ventanas de voz también contienen ruido. Se exigen al menos 0,3 s de voz y de fondo. Es `null` cuando falta alguno o la voz no supera al fondo.

No es una medida exacta: no existe señal limpia de referencia. El *hangover* incluye fondo en las ventanas de voz y sesga la estimación ligeramente a la baja: con frases de 0,6 s, unos −1,2 dB en las señales sintéticas de prueba, de forma constante entre 13 y 33 dB de SNR real. Un fondo no estacionario, música o voces superpuestas hacen la estimación menos representativa.

## Integración con diagnósticos

Las ventanas de voz del VAD son la evidencia de voz de los detectores de rumble, sibilancia y plosivas. Las ventanas del perfil son su evidencia de fondo. El ruido estacionario usa el nivel, la planitud y la estabilidad del perfil y exige una SNR aproximada ≤ 25 dB. Su evidencia publica `estimated_snr_db` con la indicación de que es una estimación.

## Límites conocidos

- Sin contraste de nivel (ruido continuo o un tono constante) no se detecta voz: la puerta relativa al fondo lo impide a propósito. Una grabación hablada sin pausas en al menos el 10 % del tiempo puede perder sus sílabas más débiles al principio de cada tramo.
- Un ruido coloreado más fuerte que `activo − 14 dB` y `fondo + 6 dB` puede entrar en voz, porque la puerta espectral solo descarta ruido de tipo blanco.
- Música, risas o respiraciones fuertes pueden etiquetarse como voz. El VAD se ha validado con señales sintéticas, no con un corpus anotado de podcasts reales; la evaluación con datos reales pertenece a la fase de benchmarks.
- La resolución temporal es de 30 ms y la espectral del perfil ≈ 33 Hz (4 Hz en el espectro grave, solo con pausas de 0,25 s o más). La PSD del fondo no separa líneas de 50/60 Hz de forma fiable: para eso sigue el análisis de zumbido con ventanas de 1 s.

## API y caché

El informe de `POST /api/audio/{id}/analyze` y `GET /api/audio/{id}/analysis` añade `speech_activity`, `noise_profile` y `estimated_snr_db`. Un informe en caché sin estos campos, con otra versión del VAD, segmentos que no cubren exactamente la grabación, etiquetas desconocidas, PSD incoherente o SNR no finita se considera ausente y se recalcula. El log de análisis registra backend VAD, porcentaje de voz y SNR aproximada, sin contenido de audio.

## Ejemplo reproducible

```powershell
.\.venv\Scripts\python.exe scripts/smoke_audio.py --base-url http://127.0.0.1:5173 --activity-demo
.\.venv\Scripts\python.exe scripts/smoke_audio.py --activity-demo --write-sample data/qa/activity-demo.wav
```

La demo sintetiza frases armónicas de 0,6 s por segundo sobre un siseo gaussiano de −44,4 dBFS (semilla fija). El smoke exige voz y fondo en la línea temporal, voz entre el 40 y el 90 %, el nivel del fondo a ±2 dB del generado y una SNR aproximada entre 10 y 40 dB. En la interfaz, el clip de 12 s muestra 12 tramos de voz, fondo a −44,5 dBFS, SNR aproximada de 21,7 dB y el perfil del fondo superpuesto al espectro.
