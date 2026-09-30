# Aurea · Podcast Audio Doctor

Restauración y mastering explicable para podcasts. El objetivo es **analizar → diagnosticar → recomendar → procesar → comparar → exportar**, conservando siempre el audio original.

**Estado: fase 0 implementada (v0.1.0).** La pantalla inicial conecta con la API y muestra su disponibilidad. La carga, el análisis y el procesamiento de audio llegarán en las siguientes fases; todavía no están disponibles.

## Inicio local

Requisitos: Python 3.12 o superior con `pip` y Node.js 22.12 o superior. Ejecuta los comandos desde la raíz del repositorio.

```sh
python -m venv .venv
```

En Windows:

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e backend
npm --prefix frontend ci
```

En macOS/Linux:

```sh
.venv/bin/python -m pip install -r backend/requirements.lock
.venv/bin/python -m pip install --no-deps -e backend
npm --prefix frontend ci
```

Si `python` apunta a una instalación sin pip, usa la ruta de tu instalación CPython con pip para crear el entorno. No hace falta activar `.venv`: los comandos comunes lo encuentran automáticamente.

Abre dos terminales:

```sh
npm run dev:backend
npm run dev:frontend
```

Estudio: http://localhost:5173 · API: http://localhost:8000/health · documentación interactiva: http://localhost:8000/docs.

## Docker

Instala Docker con Compose v2 y ejecuta:

```sh
docker compose up --build --wait
```

Usa las mismas direcciones locales. El frontend espera a que el backend esté sano y dirige `/health` y `/api` al servicio backend. Esta configuración es para desarrollo; el servidor Vite no es un despliegue de producción.

Opcionalmente copia `.env.example` a `.env` para ajustar los puertos. `API_PROXY_TARGET` configura el proxy local y `VITE_API_BASE_URL` permite usar un origen explícito cuando se configura CORS en el backend. El valor por defecto usa el proxy y no necesita CORS.

```sh
docker compose down
```

## Calidad

```sh
npm test                 # pytest + Vitest
npm run lint             # Ruff, formato Python y ESLint
npm run typecheck        # mypy estricto + TypeScript estricto
npm run build            # build de frontend
npm run check            # todas las comprobaciones anteriores
```

Las dependencias resueltas se guardan en `backend/requirements.lock` y `frontend/package-lock.json`. GitHub Actions ejecuta calidad y un arranque real de ambos contenedores, incluyendo el proxy HTTP.

Para actualizar dependencias del frontend usa npm 11.6.2 o superior: npm 10 puede fallar al resolver los peers opcionales de Vitest. La instalación normal con `npm ci` funciona con el lockfile incluido. Detén Vite antes de reinstalar en Windows para liberar el ejecutable de esbuild.

## Arquitectura

```text
frontend/src/       React + TypeScript, cliente HTTP y pruebas
backend/app/api/   Contratos y rutas HTTP (FastAPI/Pydantic)
backend/app/main.py Fábrica de aplicación
backend/tests/     Pruebas de integración
scripts/           Comandos comunes para Windows/macOS/Linux
docs/              Estado de fases y decisiones
```

La fábrica crea instancias independientes. El dominio DSP se añadirá conforme se implemente: no hay motores simulados ni estructuras vacías. Los errores de conexión se muestran sin detalles internos y permiten reintentar. Uvicorn genera los logs de acceso HTTP; no se registra audio.

## API actual

`GET /health` devuelve HTTP 200:

```json
{"status":"ok","service":"aurea","version":"0.1.0"}
```

El endpoint indica disponibilidad HTTP. No valida todavía FFmpeg, almacenamiento ni procesamiento DSP.

## Roadmap

Consulta el [plan completo](podcast_audio_doctor_project_plan.md) y el [estado de implementación](docs/progress.md). El siguiente entregable es la fase 1: ingesta y representación de audio (`v0.2.0`).

Fuentes de implementación: [Vite](https://vite.dev/guide/), [testing de FastAPI](https://fastapi.tiangolo.com/tutorial/testing/) y [Vitest](https://vitest.dev/guide/).
