# Aurea · Podcast Audio Doctor

Restauración y mastering explicable para podcasts. El objetivo es **analizar → diagnosticar → recomendar → procesar → comparar → exportar**, conservando siempre el audio original.

**Estado: fase 8 implementada (v0.9.0).** Sube WAV, FLAC, MP3, M4A u OGG, pulsa **Analizar grabación** y aplica las correcciones recomendadas: DC, paso alto, de-hum, reducción de ruido, nivelado y compresión. Después elige un objetivo en **Masterización** y pulsa **Crear máster**. Podcast Standard busca −16 LUFS y true peak ≤−1 dBTP; Broadcast R128, −23 LUFS. Puedes escuchar, comparar integrado/momentáneo/corto plazo/LRA y descargar el WAV PCM de 24 bits solo tras superar ocho controles del archivo final. Los informes JSON conservan métricas, decisiones, curvas y comprobaciones. El original siempre se conserva. Métodos, presets y límites: [mastering.md](docs/mastering.md).

Para orientarte por los archivos y las funciones principales, empieza por la [guía rápida del código](docs/code-guide.md).

La interfaz utiliza la paleta definitiva **Claridad Acústica**: azul noche `#0B132B`, cian `#00E5FF`, turquesa `#1DE9B6`, gris azulado `#3A506B` y texto blanco `#FFFFFF`.

## Inicio local

Requisitos: Python 3.12 o superior con `pip`, Node.js 22.12 o superior y **FFmpeg + ffprobe en PATH**. Ejecuta los comandos desde la raíz del repositorio. Los contenedores ya incluyen FFmpeg y libsndfile.

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
frontend/src/features/audio/ Carga, metadatos, reproducción e informe visual
frontend/src/api/            Cliente HTTP con validación de contratos
backend/app/domain/         AudioBuffer, AudioAsset y errores, independientes de HTTP
backend/app/audio/          FFmpeg, PCM I/O, conversión y waveform
backend/app/analysis/       Métricas, ventanas de 30 ms, VAD intercambiable y perfil de ruido
backend/app/diagnostics/    Evidencias de señal, ocho detectores explicables y motor de diagnóstico
backend/app/processors/     DC, paso alto, de-hum, ganancia, reducción de ruido y dinámica de voz
backend/app/pipeline/       Registro, reglas de decisión centralizadas y runner por bloques
backend/app/mastering/      Medición R128, presets TOML, loudnorm a dos pasadas y Output QC
backend/app/services/       Ingesta, análisis, caché y almacenamiento temporal
backend/app/api/            Contratos Pydantic y rutas HTTP
backend/tests/              Pruebas unitarias y de integración con FFmpeg real
scripts/                    Calidad y ejemplo reproducible de ingesta
docs/                       Decisiones y estado de fases
```

La fábrica crea servicios independientes por aplicación. El dominio usa dataclasses; Pydantic valida los límites HTTP y la persistencia. Uvicorn registra acceso HTTP; ingesta y análisis registran ID y tiempo de ejecución, sin contenido de audio. El análisis trabaja por bloques sobre una copia del decodificado y guarda métricas y diagnósticos de forma atómica. Consulta [métodos del analizador](docs/analysis.md), [métodos y límites del diagnóstico](docs/diagnostics.md) , [actividad de voz y perfil de ruido](docs/activity.md) y [procesamiento correctivo](docs/processing.md).

FFmpeg se ejecuta sin shell, con argumentos fijos, protocolo local y formatos limitados. La ingesta conserva los bytes originales, decodifica WAV float32 `(frames, channels)` a la frecuencia nativa, genera picos por bloques y crea una copia WAV de 16 bits compatible con navegadores. La reproducción utiliza HTTP Range y picos precalculados: no decodifica todo el podcast en memoria del navegador.

## Límites y conservación

Valores por defecto: **100 MiB**, **30 minutos**, **mono/estéreo**, **8–96 kHz**, decodificado máximo **1536 MiB** y **24 horas** de conservación. Se validan extensión, MIME y contenido real. El MIME genérico `application/octet-stream` se admite después de verificar el contenido. Se rechazan archivos vacíos, corruptos, con varias pistas, muestras no finitas o fuera del rango PCM, y límites excedidos.

La app publica el ID solo cuando original, decodificado, reproducción, waveform y manifiesto están listos. Una carga fallida elimina su carpeta parcial. Los activos sobreviven reinicios; la limpieza se ejecuta al arrancar y cada 60 segundos mientras el backend está activo. La API impide acceder a un archivo desde su caducidad, aunque la limpieza física se retrase hasta el próximo arranque. En Docker se usa el volumen `audio-data`.

Configura variables `AUREA_*` antes de iniciar el backend. `.env.example` documenta las principales; Compose las lee desde `.env`. El backend local lee variables del entorno, no carga `.env` automáticamente. `AUREA_STORAGE_DIR`, `AUREA_MIN_SAMPLE_RATE`, `AUREA_MAX_SAMPLE_RATE`, `AUREA_MAX_DECODED_BYTES` y `AUREA_COMMAND_TIMEOUT_SECONDS` permiten ajustar almacenamiento y límites. La UI consulta los límites efectivos en la API.

Se admiten dos ingestas y un análisis simultáneo por proceso; exceder esa capacidad devuelve HTTP 503 y permite reintentar. Cancelar la carga o cambiar de grabación interrumpe la solicitud del navegador: el backend puede terminar el trabajo iniciado. El sistema de jobs y cancelación de workers pertenece a la fase 14. El análisis necesita temporalmente espacio adicional de hasta el tamaño del decodificado; la copia se elimina al terminar y los restos de procesos interrumpidos se purgan por antigüedad.

## API actual

`GET /health` devuelve HTTP 200:

```json
{"status":"ok","service":"aurea","version":"0.9.0"}
```

El endpoint indica disponibilidad HTTP. No valida todavía FFmpeg, almacenamiento ni procesamiento DSP.

| Ruta | Resultado |
|---|---|
| `GET /api/audio/config` | Formatos y límites efectivos |
| `POST /api/audio` | Multipart con campo `file`; devuelve metadatos e ID (201) |
| `GET /api/audio/{id}` | Metadatos, fechas de creación/caducidad |
| `GET /api/audio/{id}/waveform` | Hasta 2048 picos por canal |
| `GET /api/audio/{id}/stream` | WAV de reproducción; admite Range (206) |
| `POST /api/audio/{id}/analyze` | Calcula métricas, diagnósticos, actividad de voz, perfil de ruido y SNR aproximada, o recupera la caché válida (200) |
| `GET /api/audio/{id}/analysis` | Recupera el informe ya calculado (200; 404 si falta) |
| `GET /api/audio/{id}/processing/plan` | Cadena correctiva recomendada con motivos y evidencia |
| `POST /api/audio/{id}/process` | Renderiza el plan recomendado o el enviado (`{"plan": …}`); devuelve el manifiesto |
| `GET /api/audio/{id}/processing` | Último manifiesto: pasos, tiempos, avisos, métricas antes/después y curvas de ganancia |
| `GET /api/audio/{id}/processed/waveform` | Picos de la versión corregida |
| `GET /api/audio/{id}/processed/stream` | Versión corregida; admite Range (206) |
| `GET /api/audio/mastering/presets` | Objetivos configurables de publicación |
| `POST /api/audio/{id}/master` | Masteriza el corregido; preset opcional (`{"preset":"podcast_standard"}`); QC obligatorio |
| `GET /api/audio/{id}/mastering` | Informe del máster verificado de las correcciones actuales |
| `GET /api/audio/{id}/mastered/waveform` | Picos del máster |
| `GET /api/audio/{id}/mastered/stream` | Reproducción del máster en PCM16; admite Range |
| `GET /api/audio/{id}/mastered/download` | WAV PCM24 con duración/muestreo/canales nativos; exige QC y verifica SHA-256 |

Errores de dominio: `{ "code": "invalid_audio_file", "message": "…" }`. Estados: 413 para tamaño/duración, 415 para formato/MIME, 422 para audio no válido, 404 para ID ausente, 410 para caducado antes de limpieza y 503 para decodificador/capacidad. Los errores del parser HTTP usan el contrato `detail` de FastAPI.

## Ejemplo reproducible

Con ambos servidores arrancados, en Windows:

```powershell
.\.venv\Scripts\python.exe scripts/smoke_audio.py --base-url http://127.0.0.1:5173
.\.venv\Scripts\python.exe scripts/smoke_audio.py --write-sample data/qa/episode-synthetic.wav
.\.venv\Scripts\python.exe scripts/smoke_audio.py --diagnostic-demo --write-sample data/qa/diagnostic-demo.wav
.\.venv\Scripts\python.exe scripts/smoke_audio.py --activity-demo --write-sample data/qa/activity-demo.wav
.\.venv\Scripts\python.exe scripts/smoke_audio.py --dynamics-demo --write-sample data/qa/dynamics-demo.wav
```

En macOS/Linux sustituye el intérprete por `.venv/bin/python`. El primer comando comprueba carga, reproducción, análisis, diagnósticos, actividad, perfil de ruido, correcciones, masterización, ocho controles QC y descarga PCM24 a través del proxy. Los demás crean clips sintéticos de 12 s para cargar en la interfaz y pulsar **Analizar grabación**: saturación y zumbido, frases con ruido o cambios de volumen. No contienen voz privada ni audio con licencia. Usa `--diagnostic-demo`, `--activity-demo` o `--dynamics-demo` sin `--write-sample` para ejecutar el smoke correspondiente. Los cuatro casos se comprueban contra Docker en CI.

Los informes anteriores a v0.6.0 se recalculan al pulsar **Analizar grabación**. Las grabaciones conservan su ID y el original; una caché antigua no se presenta como un diagnóstico actualizado.

Las correcciones de versiones anteriores se vuelven a renderizar con la cadena v0.9.0. Cambiar las correcciones invalida el máster anterior; pulsa **Crear máster** de nuevo. Corrección y masterización comparten una única capacidad de renderizado por proceso. MP3/FLAC y opciones adicionales de exportación pertenecen a la fase 10.

## Roadmap

Consulta el [plan completo](podcast_audio_doctor_project_plan.md) y el [estado de implementación](docs/progress.md). El siguiente entregable es la fase 8: loudness, masterización y limiter.

Fuentes de implementación: [Vite](https://vite.dev/guide/), [testing de FastAPI](https://fastapi.tiangolo.com/tutorial/testing/) y [Vitest](https://vitest.dev/guide/).
Audio: [archivos en FastAPI](https://fastapi.tiangolo.com/tutorial/request-files/), [protocolos FFmpeg](https://ffmpeg.org/ffmpeg-protocols.html), [selección de pistas FFmpeg](https://ffmpeg.org/ffmpeg.html), [SoundFile](https://python-soundfile.readthedocs.io/) y [WaveSurfer](https://wavesurfer.xyz/).

Métodos, presets, benchmark y límites de reducción de ruido: [noise-reduction.md](docs/noise-reduction.md).

Métodos, controles, curvas y límites del nivelador y compresor: [voice-leveling.md](docs/voice-leveling.md). La demo `--dynamics-demo` también ejecuta una prueba HTTP de 12 s con cambios de volumen, pausas y curvas; CI comprueba los cuatro smoke.
