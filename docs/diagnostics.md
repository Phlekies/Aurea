# Diagnóstico explicable · v0.4.0

La fase 3 interpreta las mediciones y examina la señal original decodificada. No modifica el audio. El servicio compone métricas y ocho diagnósticos en el mismo informe, con persistencia atómica y conservación del original.

## Contrato

Cada detector implementa `Detector.analyze(audio, context) -> Diagnostic`. `audio` reúne evidencias de señal; `context` aporta las métricas de la fase 2. El motor ejecuta los detectores en un orden estable:

```text
clipping, hum, rumble, low_level, low_headroom,
stationary_noise, sibilance, plosives
```

Cada observación contiene `code`, `detected`, `severity`, `confidence`, `message`, `evidence` y `parameters`. Los scores están en [0, 1]; toda evidencia numérica es finita, y una medida ausente se representa con `null`. Se incluyen los parámetros relevantes para interpretar y reproducir la decisión. La UI muestra primero las señales detectadas por severidad y permite revisar todas las comprobaciones.

`confidence` expresa la fuerza de la evidencia según la heurística: no es una probabilidad estadística calibrada. Un resultado no detectado con evidencia insuficiente se distingue de una comprobación con datos utilizables. Ausencia de detección no garantiza ausencia de problemas.

## Evidencias y métodos

| Detector | Evidencia utilizada |
|---|---|
| Clipping | Muestras a escala completa, concentración cercana a ±1 y secuencias consecutivas saturadas por canal |
| Hum | Picos de 50 o 60 Hz y sus armónicos frente a bins vecinos, con persistencia temporal; siempre exige la fundamental |
| Ruido grave | Distribución de energía grave, estabilidad temporal y relación con actividad candidata de voz |
| Nivel bajo | Loudness integrado y/o nivel RMS, con tratamiento específico de silencio y clips sin loudness |
| Poco headroom | Pico de muestra y true peak; margen hasta la escala completa |
| Ruido estacionario | Densidad espectral y estabilidad en marcos con baja evidencia de voz |
| Sibilancia | Proporción de energía de alta frecuencia durante marcos candidatos de voz |
| Plosivas | Bursts breves de energía grave cerca de un inicio candidato de voz |

El clipping se mide directamente en las muestras nativas; no se infiere solo por un pico alto. El análisis de hum utiliza ventanas de un segundo con resolución aproximada de 1 Hz, porque la PSD de 2048 muestras de la fase 2 no separa bien 50 y 60 Hz a todas las frecuencias de muestreo. Se elimina DC para esta comparación. Con menos de un segundo se informa falta de evidencia espectral adecuada. Se detecta zumbido con la fundamental y al menos otro armónico destacados (≥10 dB sobre los bins vecinos en ≥60 % de las ventanas), o con una fundamental aislada de ≥20 dB presente en ≥90 % de las ventanas; en ambos casos debe reunir al menos el 1 % de la energía. Una línea aislada recibe menos confianza. Exigir la fundamental evita confundir los armónicos de una voz grave de 100 o 120 Hz con zumbido de red; la persistencia casi constante distingue una línea eléctrica de voz o música intermitentes.

La energía de canales se conserva independientemente, evitando la cancelación de estéreo en oposición. Las bandas se ajustan a Nyquist. Un archivo de 8 kHz no permite observar toda la banda de sibilancia; esa comprobación informa su limitación.

Los candidatos internos de voz y baja actividad se obtienen de energía por bandas, forma espectral y evolución temporal. Solo sirven de contexto heurístico para esta fase; no constituyen el VAD completo ni una máscara de voz/no voz. La fase 4 incorporará segmentación y un perfil de ruido explícitos, reutilizables y reproducibles.

## Límites

Los detectores de ruido, sibilancia y plosivas son heurísticos. Pueden confundir música, respiraciones, sonidos ambientales o voces con timbres poco habituales. El detector de rumble reduce falsos positivos en voz grave usando contexto temporal y espectral, pero no dispone de una referencia limpia. No se presenta una SNR exacta ni una identificación inequívoca de voz.

El silencio y clips demasiado breves no generan un certificado de audio correcto. Se conservan sus métricas y se indican las comprobaciones sin datos suficientes. La confianza se reduce cuando hay poco contexto de voz/no voz o una banda no es observable.

La lectura utiliza bloques y conserva resúmenes temporales en lugar del audio completo. El uso de memoria de esos resúmenes queda limitado por los límites de ingesta de 30 minutos y 96 kHz; las muestras y espectros por marco no se acumulan para todo el podcast. El cálculo comparte la capacidad de un análisis simultáneo y la copia temporal de la fase 2.

## API y recuperación

Se mantienen POST `/api/audio/{id}/analyze` y GET `/api/audio/{id}/analysis`. El informe añade `diagnostics_version: "0.4.0"` y los ocho diagnósticos. Una caché sin diagnóstico, con versión antigua, códigos duplicados o scores/evidencia inválidos se considera ausente y se puede recalcular. La publicación incluye métricas y diagnóstico completos; no se guarda un informe parcial.

Un fallo durante el diagnóstico devuelve un error de dominio seguro `analysis_failed` (503), libera la capacidad y limpia la copia temporal. El original permanece intacto. El cliente puede reintentar. Caducidad, IDs y cancelación de cliente siguen las reglas de la fase 2.

## Ejemplo reproducible

```powershell
.\.venv\Scripts\python.exe scripts/smoke_audio.py --base-url http://127.0.0.1:5173
.\.venv\Scripts\python.exe scripts/smoke_audio.py --base-url http://127.0.0.1:5173 --diagnostic-demo
.\.venv\Scripts\python.exe scripts/smoke_audio.py --diagnostic-demo --write-sample data/qa/diagnostic-demo.wav
```

La demo sintetiza una señal armónica modulada, un tono de 50 Hz y clipping deliberado. El smoke exige que se detecten saturación y zumbido de 50 Hz; también aparece poco headroom por la propia saturación. Sirve para comprobar problemas conocidos sin audio privado. No sustituye una evaluación con podcasts reales ni un benchmark de precisión; esa evaluación se ampliará en las fases previstas.
