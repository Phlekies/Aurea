# Motor de análisis · v0.3.0

Flujo disponible: carga → audio float32 nativo → análisis → diagnóstico → informe persistente → gráficas y descarga JSON. No se modifica el original ni se aplica restauración. La fase 3 añade [diagnósticos explicables](diagnostics.md); la recomendación automática de una cadena de procesamiento pertenece a una fase posterior.

## Métodos y unidades

| Medida | Método |
|---|---|
| Pico, dBFS | `20 log10(max(abs(x)))`, máximo de todos los canales |
| RMS, dBFS | `10 log10(mean(x²))`, energía promedio de tiempo y canales |
| Crest factor, dB | Pico dBFS menos RMS dBFS |
| DC offset | Media de las muestras por canal, en amplitud relativa a full scale |
| Cruces por cero | Fracción de cambios de signo entre muestras adyacentes, promedio por canal; cero es no negativo |
| Silencio, % | Duración con RMS ≤ −60 dBFS en todos los canales, en ventanas de 20 ms; incluye la última ventana parcial |
| Bandas | Integral de la PSD en intervalos de frecuencia; potencia absoluta y porcentaje de la potencia espectral total |
| Espectro promedio / PSD | Welch de 2048 muestras, Hann periódica, 50 % de solapamiento, densidad unilateral en dBFS/Hz |
| Dinámica temporal | Pico y RMS en ventanas de al menos 100 ms, agregadas en amplitud/potencia hasta 2000 intervalos |
| Loudness integrado, LUFS | FFmpeg `ebur128`: ponderación K, bloques de 400 ms, solapamiento 75 %, puertas absoluta −70 LUFS y relativa −10 LU |
| True peak, dBTP | Interpolación libswresample float64 de 64 taps a `max(192000, 4 × sample_rate)` Hz, medida con `astats`; nunca inferior al pico de muestra |

Se conserva mono/estéreo: no se suman los canales antes de calcular energía, espectro o loudness. Una señal estéreo en oposición de fase conserva su potencia. La duración se calcula a partir de muestras y frecuencia, sin redondearla al segundo.

## Valores no definidos

El silencio absoluto tiene pico, RMS, crest factor, loudness y true peak `null`; las densidades espectrales de potencia cero también son `null`. Un clip menor de 400 ms o sin bloques que superen la puerta no tiene loudness integrado. La interfaz muestra «—» y explica el motivo. No se envían NaN, Infinity ni un suelo artificial presentado como medición.

## Resolución y límites

El resumen de loudness de FFmpeg resuelve 0,1 LUFS. True peak es una estimación por interpolación; este entregable no afirma certificación de un medidor ITU. La tasa de interpolación cubre al menos 4× incluso para entradas de 96 kHz. Solo su rama de medición añade 128 muestras de cero para vaciar el interpolador y admitir clips de una muestra; no se guarda ni se altera la grabación.

La PSD usa todas las ventanas completas y omite la cola incompleta, como Welch. Para clips de menos de 2048 muestras utiliza ventana rectangular sobre todas las muestras y zero padding. Las bandas cubren DC–Nyquist y asignan cada bin una sola vez; las bandas sobre Nyquist se omiten. Su energía puede diferir ligeramente del RMS de todo el archivo debido a la ventana espectral y la cola omitida. La UI representa frecuencia logarítmica desde 20 Hz; DC permanece en los datos descargados.

La memoria del motor depende del bloque de lectura (65536 muestras), no de la duración. Los resultados tienen 1025 bins espectrales y hasta 2000 puntos temporales. Al aumentar la ventana temporal se conservan el máximo de pico y la energía RMS exacta del intervalo. Las gráficas recortan su vista, no las métricas guardadas.

## API, caché y errores

`POST /api/audio/{id}/analyze` calcula el informe sin bloquear el event loop y devuelve el objeto `AudioAnalysis`. Un POST repetido reutiliza un informe finito con las mismas versiones de análisis/diagnóstico y los mismos metadatos. `GET /api/audio/{id}/analysis` solo lee; devuelve `analysis_not_found` (404) si todavía no hay informe válido. Desde v0.4.0 el informe incluye ocho observaciones en `diagnostics` y `diagnostics_version`; en v0.5.0 añade `speech_activity`, `noise_profile` y `estimated_snr_db` ([actividad de voz y perfil de ruido](activity.md)); en v0.6.0 el perfil incluye un espectro grave de 4 Hz. El procesado se describe en [procesamiento correctivo](processing.md). El motor de métricas mantiene su versión `0.3.0` porque sus métodos no han cambiado.

El servicio limita el cálculo a uno simultáneo por proceso. Se analiza una copia temporal del decodificado para que la limpieza de una grabación caducada no invalide una lectura en curso. Se vuelve a comprobar la caducidad antes de publicar y devolver el resultado. El JSON se publica mediante reemplazo atómico; sobrevive al reinicio y caduca con la grabación.

Capacidad, dependencia de medición o almacenamiento no disponibles devuelven 503 con un mensaje seguro. Los IDs ausentes o inválidos devuelven 404 y los caducados, 410 antes de la limpieza. El cliente puede reintentar. Cambiar de audio cancela la espera del cliente; la cancelación del worker se incorporará con los jobs de la fase 14.

## Ejemplo reproducible

Con API y frontend arrancados:

```powershell
.\.venv\Scripts\python.exe scripts/smoke_audio.py --base-url http://127.0.0.1:5173
.\.venv\Scripts\python.exe scripts/smoke_audio.py --write-sample data/qa/episode-synthetic.wav
```

El primer comando comprueba el contrato completo con un clip sintético. El segundo prepara audio para subir en el estudio y pulsar **Analizar grabación**. No requiere audio privado ni con licencia.

Referencias: [ITU-R BS.1770](https://www.itu.int/rec/R-REC-BS.1770-5-202311-I/en), [FFmpeg ebur128](https://ffmpeg.org/ffmpeg-filters.html#ebur128), [FFmpeg aresample](https://ffmpeg.org/ffmpeg-filters.html#aresample), [FFmpeg astats](https://ffmpeg.org/ffmpeg-filters.html#astats), [SciPy Welch](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.welch.html).
