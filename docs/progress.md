# Estado del desarrollo

## Fase 0 — Bootstrap completada (v0.1.0)

Implementación: backend FastAPI, contrato `/health`, fábrica de aplicación, React/TypeScript/Vite, cliente HTTP validado con timeout y cancelación, pantalla inicial responsive y recuperación de conexión.

Infraestructura: Dockerfiles, health checks, Compose, `.env.example`, exclusión de temporales y dependencias, comandos de calidad comunes y GitHub Actions con smoke test de Docker.

Decisiones: proxy del mismo origen para evitar CORS innecesario; almacenamiento y DSP se añadirán en sus fases; dependencias bloqueadas; frontend Docker para desarrollo; tests de contratos HTTP y comportamiento visible.

Validación local: `npm run check` correcto (Ruff, formato, ESLint, mypy, TypeScript, 3 pruebas de backend, 9 de frontend y build). API directa y proxy `/health`: HTTP 200 con estado `ok`; frontend: HTTP 200. Interfaz revisada en navegador. Instalación reproducida con `npm ci`; npm audit sin alertas. Starlette emite un aviso de deprecación de su cliente httpx, que sigue siendo funcional.

Docker no está instalado en el equipo local. El arranque real en contenedores se ha verificado en [GitHub Actions](https://github.com/Phlekies/Aurea/actions/runs/36730439782): jobs `quality` y `docker` correctos; los servicios arrancan sanos y API, frontend y proxy devuelven HTTP 200. La interfaz también se ha revisado a 390 px de ancho.

Aceptación: implementación, pruebas, documentación, ejemplo reproducible, API, recuperación de errores de conexión, logs HTTP, revisión de tipos y lint completados. La integración DSP no aplica a esta fase porque el pipeline aún no existe.

## Siguiente fase

Fase 1 — Ingesta y representación: validar y decodificar WAV/FLAC/MP3/M4A/OGG, conservar originales, extraer metadatos, generar waveform y reproducir audio.

Las fases posteriores siguen el orden del plan. No se considerará terminado un entregable sin pruebas, documentación y comprobaciones de calidad.
