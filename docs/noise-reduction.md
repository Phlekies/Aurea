# Reducción de ruido DSP · v0.7.0

La fase 6 añade tres métodos intercambiables dentro de `NoiseReducer`, un procesador registrado como `noise_reduction`. La cadena es DC → paso alto → de-hum → reducción de ruido → ganancia previa. Cada renderización parte del original decodificado; cambiar método o intensidad vuelve a calcular desde esa señal, sin acumular procesados.

## Perfil, STFT y reconstrucción

Se reutiliza la PSD de fondo estimada por el VAD de la fase 4, en dBFS/Hz. La recomendación requiere ≥0,3 s/10 marcos de fondo; solo activa automáticamente el paso con diagnóstico de ruido estacionario y confianza ≥0,55. Sin perfil, la interfaz impide activar el paso. Con perfil pero sin diagnóstico suficiente, queda desactivado y el usuario puede probarlo.

El tamaño FFT es la potencia de dos más cercana a 32 ms, limitada a 256–4096 muestras. Ventanas sinusoidales de análisis y síntesis con 50 % de solapamiento: la suma de los cuadrados es uno. El relleno inicial de media ventana y el vaciado final conservan alineación, primera/última muestra, longitud, canales y frecuencia de muestreo. El runner admite esta salida temporalmente diferida y propaga la cola por los pasos posteriores. No se conserva un espectrograma completo.

La PSD se interpola en **potencia lineal**, se convierte a potencia FFT usando `fs * sum(window²)` y el factor del espectro unilateral. Si el FFT del perfil es impar (p. ej. 44,1 kHz), se añade su extremo Nyquist con la mitad de la densidad del último bin interior. Los filtros lineales anteriores propagan el perfil usando su respuesta de potencia: no se compara una señal filtrada con un fondo sin filtrar. La ganancia espectral es real y común a los canales, conservando su fase compleja y relación estéreo, también con canales en oposición.

## Métodos y presets

| Método | Ganancia antes de suavizado |
|---|---|
| Sustracción espectral | `sqrt(max(floor², 1 − alpha * noise / observed))`, sustracción en potencia con sobre-sustracción y suelo |
| Puerta espectral | Sigmoide continua de la relación observado/fondo en dB; transición de 3 dB, sin máscara binaria |
| Wiener | SNR a priori por *decision directed* (peso 0,92), posterior `observed/noise`, ganancia `xi / (xi + alpha)` con suelo |

| Intensidad | Sobre-sustracción alpha | Ganancia mínima (amplitud) | Umbral de puerta (potencia) |
|---|---:|---:|---:|
| `light` · Suave | 1 | 0,35 | 1,5 |
| `balanced` · Equilibrado | 1,5 | 0,18 | 2 |
| `strong` · Intenso | 2,5 | 0,08 | 3 |

Todos usan suavizado de tres bins en frecuencia, apertura de 12 ms y cierre de 80 ms. Los presets son parámetros de ingeniería versionados, no niveles de calidad garantizados. El método Wiener implementa esa ganancia; no es el estimador MMSE-STSA de Ephraim/Malah ni un modelo de IA.

Referencias primarias: [STFT y reconstrucción de SciPy](https://docs.scipy.org/doc/scipy/tutorial/signal.html#short-time-fourier-transform), [Ephraim y Malah, 1984, estimación decision-directed](https://malah.net.technion.ac.il/files/2017/08/Ephraim_Speech_Enhancement_ASSP84.pdf). Las fórmulas y la implementación concreta anterior definen esta versión.

## Controles y API

La sección Correcciones permite elegir método e intensidad y activar/desactivar la reducción. «Aplicar correcciones» produce una versión aparte y muestra el reproductor y las medidas antes/después. La paleta Claridad Acústica se mantiene.

`GET /api/audio/{id}/processing/plan?algorithm=wiener&strength=balanced` entrega la cadena recomendada. Alternativas de algoritmo: `spectral_subtraction`, `spectral_gate`. Intensidades: `light`, `balanced`, `strong`; valores desconocidos devuelven 422. `POST /api/audio/{id}/process` conserva el contrato de plan opcional.

El paso contiene el perfil (`noise_frequencies_hz`, `noise_psd_dbfs_per_hz`), algoritmo, intensidad, motivo y evidencia. Se validan longitud (2–4097 bins), orden, extremos 0/Nyquist, finitud y PSD −300…0 dBFS/Hz. Solo se admite un reductor por cadena. El manifiesto conserva los parámetros de entrada; la propagación por filtros es determinista a partir de los pasos anteriores. La versión del pipeline y del plan es 0.7.0; los análisis conservan sus versiones independientes.

La caché comprueba versión, formato, duración, parámetros ejecutables, correspondencia plan/pasos, tiempos y presencia de indicadores cuando se aplicó reducción. Los resultados de la fase 5 se vuelven a renderizar. Al fallar la sustitución de una versión publicada se restaura la anterior. El cambio de directorio consta de dos renombrados: no garantiza lectura ininterrumpida entre ambos, pero no publica un render parcial ni altera el original.

## Indicadores de artefactos

El informe añade `artifacts` (null sin reducción activa). Se comparan las mismas regiones de la máscara VAD **original** y los archivos alineados, evitando que un VAD recalculado cambie los intervalos de comparación.

- `background_reduction_db`: diferencia de energía del fondo; puede ser negativa si otro paso sube el nivel.
- `speech_energy_loss_db`: diferencia de energía en regiones de voz, que contienen también fondo; no es pérdida de voz limpia medida. Un descenso >6 dB genera aviso.
- `total_reduction_db`: descenso total; >18 dB genera aviso de posible reducción excesiva.
- `musical_noise_score`: aumento del porcentaje de energía concentrado en picos aislados, frente a su vecindad de nueve bins, en ventanas Hann de 2048 muestras de fondo ininterrumpido. Un aumento >0,08 genera aviso. Es un proxy, no una medida perceptual ni un detector fiable de todos los artefactos.

Las medidas sin regiones utilizables son null. La medición incluye la cadena completa y su ganancia de seguridad. Un resultado sin avisos no garantiza ausencia de distorsión; la comparación auditiva sigue siendo necesaria.

## Benchmark reproducible

```powershell
.\.venv\Scripts\python.exe scripts/benchmark_noise.py --output docs/benchmarks/noise-v0.7.0.json
.\.venv\Scripts\python.exe scripts/smoke_audio.py --base-url http://127.0.0.1:5173 --activity-demo
.\.venv\Scripts\python.exe scripts/smoke_audio.py --activity-demo --write-sample data/qa/noise-demo.wav
```

Resultados completos: [noise-v0.7.0.json](benchmarks/noise-v0.7.0.json). Semilla 2026, 4 s mono a 16 kHz, voz armónica conocida y fondo gaussiano blanco o coloreado. Perfil ideal Welch calculado sobre ruido solo; las pruebas API usan el perfil estimado por VAD. Tres ejecuciones por combinación y mediana de tiempo, sin contabilizar análisis ni decodificación.

| Fondo / equilibrado | Reducción en pausas | Mejora del error respecto a voz limpia |
|---|---:|---:|
| Blanco / sustracción | 8,16 dB | 5,25 dB |
| Blanco / puerta | 6,22 dB | 4,43 dB |
| Blanco / Wiener | 14,75 dB | 5,16 dB |
| Coloreado / sustracción | 7,84 dB | 2,61 dB |
| Coloreado / puerta | 6,06 dB | 2,38 dB |
| Coloreado / Wiener | 14,30 dB | 1,20 dB |

La mayor eliminación de fondo no asegura menor distorsión: Wiener elimina más ruido en este caso coloreado, pero la sustracción conserva mejor la referencia. El tiempo está alrededor de 0,01 s para 4 s de audio en este equipo; depende de CPU y señal. Todos trabajan localmente en CPU, sin modelo, llamadas externas ni coste de API. El benchmark no mide MOS/PESQ ni demuestra calidad en podcasts reales.

## Límites

El perfil es estacionario, no se adapta a cambios de ambiente durante el podcast. Un VAD que confunda música/respiraciones con fondo puede retirar contenido útil. La interpolación entre resoluciones, el suelo y el suavizado reducen artefactos, pero no los eliminan. La ganancia conserva la fase original y no recupera fase limpia. La calidad se ha comprobado con señales sintéticas y comparación de referencia; falta una evaluación auditiva con un corpus real. Nivelado, compresión y mastering siguen en las fases 7–8.
