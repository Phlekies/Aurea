# Estado del desarrollo

## Fase 0 — Bootstrap completada (v0.1.0)

Implementación: backend FastAPI, contrato `/health`, fábrica de aplicación, React/TypeScript/Vite, cliente HTTP validado con timeout y cancelación, pantalla inicial responsive y recuperación de conexión.

Infraestructura: Dockerfiles, health checks, Compose, `.env.example`, exclusión de temporales y dependencias, comandos de calidad comunes y GitHub Actions con smoke test de Docker.

Decisiones: proxy del mismo origen para evitar CORS innecesario; almacenamiento y DSP se añadirán en sus fases; dependencias bloqueadas; frontend Docker para desarrollo; tests de contratos HTTP y comportamiento visible.

Validación local: `npm run check` correcto (Ruff, formato, ESLint, mypy, TypeScript, 3 pruebas de backend, 9 de frontend y build). API directa y proxy `/health`: HTTP 200 con estado `ok`; frontend: HTTP 200. Interfaz revisada en navegador. Instalación reproducida con `npm ci`; npm audit sin alertas. Starlette emite un aviso de deprecación de su cliente httpx, que sigue siendo funcional.

Docker no está instalado en el equipo local. El arranque real en contenedores se ha verificado en [GitHub Actions](https://github.com/Phlekies/Aurea/actions/runs/36730439782): jobs `quality` y `docker` correctos; los servicios arrancan sanos y API, frontend y proxy devuelven HTTP 200. La interfaz también se ha revisado a 390 px de ancho.

Aceptación: implementación, pruebas, documentación, ejemplo reproducible, API, recuperación de errores de conexión, logs HTTP, revisión de tipos y lint completados. La integración DSP no aplica a esta fase porque el pipeline aún no existe.

## Siguiente fase

Fase 4 — Actividad de voz y perfil de ruido: segmentación temporal reproducible, máscara de voz/no voz y estimación de ruido para reforzar los diagnósticos.

Las fases posteriores siguen el orden del plan. No se considerará terminado un entregable sin pruebas, documentación y comprobaciones de calidad.

## Fase 1 — Ingesta y representación completada (v0.2.0)

Implementado: todos los formatos del plan, validación de MIME/extensión/contenido, límites HTTP previos al parser y límites posteriores a decodificación, preservación byte a byte del original, float32 nativo, metadatos persistentes, waveform por bloques, reproducción PCM de 16 bits con HTTP Range y limpieza de temporales por caducidad. Se han añadido `load_audio`, `save_audio`, `convert_to_float`, `to_mono` y `resample`, con pruebas.

Interfaz: selección y arrastre, validación con límites efectivos, estado de carga, cancelación de solicitud, mensajes de error, cambio de grabación, metadatos, waveform, play/pause, seek y velocidad. Verificada en navegador con un clip sintético: reproducción activa, posición de audio y slider sincronizados; vista móvil de 390 px revisada.

Arquitectura: modelos de dominio independientes, rutas Pydantic, servicio por aplicación, publicación atómica en carpetas UUID, originales excluidos de Git. FFmpeg se usa sin shell ni protocolos de red. La copia de reproducción cuantiza a 16 bits; no se aplica restauración, resampling ni mastering durante la ingesta. Los podcasts largos generan picos por bloques sin decodificación completa en el navegador.

Verificación local: 32 pruebas de backend y 19 de frontend correctas, tipos y lint correctos, build correcto, smoke HTTP a través de Vite correcto y npm audit sin alertas. Los casos incluyen silencio, audio no finito, tamaño decodificado y recuperación de capacidad tras errores de limpieza.

[CI de la fase 1](https://github.com/Phlekies/Aurea/actions/runs/36737166994) correcto: jobs `quality` y `docker` pasan, incluido el smoke que sube audio, recupera metadatos y waveform y comprueba reproducción parcial a través del proxy. La fase cumple implementación, pruebas, documentación, ejemplo reproducible, API, errores, logs, revisión de tipos y lint. Los activos de ingesta serán la entrada del motor de análisis de la fase 2.

Limitaciones: almacenamiento local sin cuentas ni historial de proyectos, 30 minutos/100 MiB por defecto, dos ingestas simultáneas por proceso y cancelación de cliente (no de worker). La retención exige que el proceso esté activo para purgar en el momento programado; al reiniciar se limpia lo pendiente. SoundFile/SciPy no incluyen todos los tipos necesarios: mypy mantiene modo estricto para código propio y excluye únicamente imports sin stubs de esas bibliotecas.

## Fase 2 — Motor de análisis básico completada (v0.3.0)

Paleta definitiva elegida por el usuario: Claridad Acústica. Fondo `#0B132B`, primario `#00E5FF`, secundario `#1DE9B6`, interfaz `#3A506B` y texto `#FFFFFF`; superficies derivadas oscuras, botones con texto oscuro y controles accesibles. Waveform y gráficas siguen los mismos colores.

Implementado: pico y RMS dBFS, crest factor, DC por canal, duración exacta, cruces por cero, energía por bandas, espectro/PSD promedio, dinámica temporal, porcentaje de silencio, loudness integrado y true peak. Cálculo por bloques, potencia de canales independiente, ventanas de silencio de 20 ms, dinámica de al menos 100 ms, Welch de 2048 muestras y loudness con puertas R128 de 400 ms. True peak con interpolación float64 a al menos 4×/192 kHz. Los dB no definidos se representan con `null` y se explican en la UI.

API: POST `/api/audio/{id}/analyze` y GET `/api/audio/{id}/analysis`; un cálculo simultáneo por proceso, caché versionada con validación de estructura/rangos/coherencia, publicación JSON atómica, recuperación tras reinicio, comprobación de caducidad y limpieza de copias temporales. Una caché dañada se puede recalcular. Integración vertical: ingesta → señal original decodificada → motor → servicio/API → informe visual y descarga JSON.

Interfaz: botón de análisis, estado de cálculo, recuperación de informe y errores reintentables, seis métricas principales, gráficas de PSD y pico/RMS, bandas y detalles técnicos, descarga completa. Cambiar de audio cancela la espera y aísla el estado del informe. La reproducción sigue disponible durante el cálculo.

Verificación local: `npm run check` correcto; **83 pruebas backend y 27 frontend** correctas (110 total), Ruff/formato, ESLint, mypy estricto, TypeScript y build. Nuevas pruebas analíticas y de referencia: seno/ganancia/DC/silencio, clips de una muestra, mono/estéreo en oposición, canales silenciosos, 8/44,1/48/96 kHz, loudness contra `loudnorm`, true peak entre muestras, determinismo, límites de puntos, cachés incoherentes, caducidad/capacidad/errores y recuperación UI. Smoke HTTP con carga, reproducción Range, cálculo, GET y reutilización de caché correcto. Informe revisado en navegador a tamaño escritorio y 390 px; descarga JSON encontrada y validada. Persisten avisos externos de Starlette/httpx, caché pytest Windows y anotaciones de Zod, sin fallos.

Documentación de métodos, unidades, referencias, rangos y límites: [analysis.md](analysis.md). Ejemplo reproducible ampliado en `scripts/smoke_audio.py` y ejecutado también por CI. [CI de la fase 2](https://github.com/Phlekies/Aurea/actions/runs/36742731656) correcto: `quality` y `docker` pasan, incluido el análisis y la caché a través del proxy. El siguiente entregable es la fase 3: diagnóstico; no se han implementado recomendaciones ni procesamiento de fases posteriores.

Limitaciones: loudness resuelve 0,1 LUFS; true peak es una estimación sin certificación de medidor; PSD omite la cola incompleta y la dinámica agrega intervalos en podcasts largos. El análisis necesita temporalmente otra copia del decodificado y usa ejecución síncrona en un worker con timeout para FFmpeg. Jobs/cancelación de worker pertenecen a la fase 14.

## Fase 3 — Diagnóstico explicable completada (v0.4.0)

Implementado: ocho detectores con el contrato `Detector.analyze(audio, context) -> Diagnostic` y orden estable: clipping, hum, rumble, nivel bajo, poco headroom, ruido estacionario, sibilancia y plosivas. Cada observación incluye `detected`, `severity`, `confidence`, mensaje comprensible, evidencia y parámetros, todos finitos y serializables. El clipping distingue crestas aplanadas y secuencias saturadas de picos sinusoidales aislados; el hum elige automáticamente 50 o 60 Hz comparando líneas con bins vecinos y exige la fundamental; rumble, ruido estacionario, sibilancia y plosivas usan candidatos internos de voz y baja actividad. Una comprobación sin datos suficientes se informa como tal, no como audio correcto.

Integración: el servicio de análisis añade el diagnóstico al mismo informe persistente (`diagnostics_version: "0.4.0"`), invalida cachés antiguas o incoherentes y convierte un fallo del diagnóstico en el error de dominio `analysis_failed` (503) sin tocar el original. El log registra versión y número de detecciones, sin contenido de audio. Interfaz: panel de diagnóstico con problemas ordenados por severidad, nivel de evidencia, comprobaciones sin detección o sin datos, y evidencia/parámetros desplegables con etiquetas y unidades en español.

Cierre de la fase: todas las claves de evidencia y parámetros tienen etiqueta en español (una prueba de backend impide que vuelvan a faltar) y se retiraron etiquetas obsoletas de SNR, que esta fase no presenta como medida. Una línea de 50/60 Hz aislada y persistente (≥20 dB sobre sus vecinos en ≥90 % de las ventanas de 1 s) se detecta ahora como zumbido con confianza reducida; antes exigía el 10 % de la energía total y la demo no mostraba su propio zumbido de 50 Hz. El smoke de la demo comprueba ya saturación y zumbido de 50 Hz.

Verificación local: `npm run check` correcto; **161 pruebas backend y 49 frontend** (210 total), Ruff/formato, ESLint, mypy estricto, TypeScript y build. Las pruebas cubren positivos y confusores de cada detector (seno a escala completa, voz grave de 70/100/120 Hz, ruido continuo, fondo variable, Nyquist de 4 kHz), estéreo en oposición, fronteras de bloque, determinismo, silencio y clips breves, entradas no válidas, fallos del detector y cachés obsoletas. Smoke HTTP normal y de demo a través del proxy de Vite correcto. Interfaz revisada en navegador con la demo (saturación, poco headroom y zumbido de 50 Hz detectados) a tamaño escritorio y 390 px sin desbordamiento horizontal.

Documentación de métodos, umbrales y límites: [diagnostics.md](diagnostics.md). CI de la fase 3: pendiente de ejecutar tras publicar los cambios.

Limitaciones: los scores son heurísticos, no probabilidades calibradas; los detectores de ruido, sibilancia y plosivas pueden confundir música, respiraciones o timbres poco habituales, y se han validado con señales sintéticas, no con un corpus de podcasts reales. Los candidatos de voz son internos; la segmentación voz/no voz y el perfil de ruido reutilizables llegan en la fase 4.
