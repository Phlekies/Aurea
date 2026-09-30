# Estado del desarrollo

## Fase 0 — Bootstrap completada (v0.1.0)

Implementación: backend FastAPI, contrato `/health`, fábrica de aplicación, React/TypeScript/Vite, cliente HTTP validado con timeout y cancelación, pantalla inicial responsive y recuperación de conexión.

Infraestructura: Dockerfiles, health checks, Compose, `.env.example`, exclusión de temporales y dependencias, comandos de calidad comunes y GitHub Actions con smoke test de Docker.

Decisiones: proxy del mismo origen para evitar CORS innecesario; almacenamiento y DSP se añadirán en sus fases; dependencias bloqueadas; frontend Docker para desarrollo; tests de contratos HTTP y comportamiento visible.

Validación local: `npm run check` correcto (Ruff, formato, ESLint, mypy, TypeScript, 3 pruebas de backend, 9 de frontend y build). API directa y proxy `/health`: HTTP 200 con estado `ok`; frontend: HTTP 200. Interfaz revisada en navegador. Instalación reproducida con `npm ci`; npm audit sin alertas. Starlette emite un aviso de deprecación de su cliente httpx, que sigue siendo funcional.

Docker no está instalado en el equipo local. El arranque real en contenedores se ha verificado en [GitHub Actions](https://github.com/Phlekies/Aurea/actions/runs/36730439782): jobs `quality` y `docker` correctos; los servicios arrancan sanos y API, frontend y proxy devuelven HTTP 200. La interfaz también se ha revisado a 390 px de ancho.

Aceptación: implementación, pruebas, documentación, ejemplo reproducible, API, recuperación de errores de conexión, logs HTTP, revisión de tipos y lint completados. La integración DSP no aplica a esta fase porque el pipeline aún no existe.

## Siguiente fase

Fase 2 — Motor de análisis básico: peak/RMS dBFS, crest factor, DC offset, bandas/espectro/PSD, dinámica y silencio, loudness y true peak; pruebas con señales sintéticas y contratos de análisis.

Las fases posteriores siguen el orden del plan. No se considerará terminado un entregable sin pruebas, documentación y comprobaciones de calidad.

## Fase 1 — Ingesta y representación completada (v0.2.0)

Implementado: todos los formatos del plan, validación de MIME/extensión/contenido, límites HTTP previos al parser y límites posteriores a decodificación, preservación byte a byte del original, float32 nativo, metadatos persistentes, waveform por bloques, reproducción PCM de 16 bits con HTTP Range y limpieza de temporales por caducidad. Se han añadido `load_audio`, `save_audio`, `convert_to_float`, `to_mono` y `resample`, con pruebas.

Interfaz: selección y arrastre, validación con límites efectivos, estado de carga, cancelación de solicitud, mensajes de error, cambio de grabación, metadatos, waveform, play/pause, seek y velocidad. Verificada en navegador con un clip sintético: reproducción activa, posición de audio y slider sincronizados; vista móvil de 390 px revisada.

Arquitectura: modelos de dominio independientes, rutas Pydantic, servicio por aplicación, publicación atómica en carpetas UUID, originales excluidos de Git. FFmpeg se usa sin shell ni protocolos de red. La copia de reproducción cuantiza a 16 bits; no se aplica restauración, resampling ni mastering durante la ingesta. Los podcasts largos generan picos por bloques sin decodificación completa en el navegador.

Verificación local: 32 pruebas de backend y 19 de frontend correctas, tipos y lint correctos, build correcto, smoke HTTP a través de Vite correcto y npm audit sin alertas. Los casos incluyen silencio, audio no finito, tamaño decodificado y recuperación de capacidad tras errores de limpieza.

[CI de la fase 1](https://github.com/Phlekies/Aurea/actions/runs/36737166994) correcto: jobs `quality` y `docker` pasan, incluido el smoke que sube audio, recupera metadatos y waveform y comprueba reproducción parcial a través del proxy. La fase cumple implementación, pruebas, documentación, ejemplo reproducible, API, errores, logs, revisión de tipos y lint. Los activos de ingesta serán la entrada del motor de análisis de la fase 2.

Limitaciones: almacenamiento local sin cuentas ni historial de proyectos, 30 minutos/100 MiB por defecto, dos ingestas simultáneas por proceso y cancelación de cliente (no de worker). La retención exige que el proceso esté activo para purgar en el momento programado; al reiniciar se limpia lo pendiente. SoundFile/SciPy no incluyen todos los tipos necesarios: mypy mantiene modo estricto para código propio y excluye únicamente imports sin stubs de esas bibliotecas.
