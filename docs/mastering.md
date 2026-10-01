# Masterización para podcast — fase 8, v0.9.0

Después de aplicar las correcciones, elige el objetivo y pulsa **Crear máster**. Se genera una salida aparte; original y corregido conservan sus bytes. Solo un máster verificado permite escuchar y descargar el archivo final. La descarga es WAV PCM de **24 bits**, con la frecuencia, los canales y el número de muestras del audio corregido. El reproductor utiliza una copia PCM16 y picos precalculados. Otros formatos se añadirán en la fase 10.

## Objetivos configurables

`backend/app/mastering/presets.toml` define los presets; `load_presets()` valida sus límites al arrancar. TOML permite usar la biblioteca estándar de Python y empaquetar las reglas junto al backend, sin añadir dependencias. Reinicia el servicio al cambiar la configuración.

| Preset | Integrado | Techo true peak | LRA objetivo | Tolerancia de loudness |
|---|---:|---:|---:|---:|
| Podcast Standard | −16 LUFS | −1 dBTP | 11 LU | ±0,5 LU |
| Broadcast R128 | −23 LUFS | −1 dBTP | 11 LU | ±0,5 LU |

−16 LUFS es la política inicial de Aurea propuesta en el plan. Cada plataforma puede requerir otro objetivo. −23 LUFS corresponde a la recomendación de radiodifusión [EBU R128](https://tech.ebu.ch/files/live/sites/tech/files/shared/r/r128v5_0.pdf). Ambos presets miden los canales reales; no aplican compensación `dual_mono`. El LRA objetivo orienta la normalización dinámica; no es un control QC de igualdad exacta.

## Medición y renderizado

1. `MasteringService.master()` exige una corrección publicada, adquiere la capacidad compartida y copia el corregido a un temporal. Un hash del manifiesto identifica esa generación de correcciones.
2. `measure_loudness()` ejecuta `ebur128` de FFmpeg: integrado con puerta, momentáneo de 400 ms, corto plazo de 3 s y LRA. Se conservan puntos temporales y máximos M/S. Las ventanas incompletas y el silencio se representan con `null`. Los puntos se limitan a intervalos absolutos de 100 ms, también a frecuencias impares. Estas ventanas siguen [EBU Tech 3341](https://tech.ebu.ch/docs/tech/tech3341.pdf).
3. `master_audio()` utiliza [loudnorm a dos pasadas](https://ffmpeg.org/ffmpeg-filters.html#loudnorm): primera medición y segunda pasada con I/LRA/TP/umbral/offset medidos. Solicita ganancia lineal cuando las mediciones permiten cumplir techo y LRA; FFmpeg puede pasar a modo dinámico, también con LRA medido igual a cero. El modo dinámico incluye el limitador de true peak de FFmpeg, que trabaja a 192 kHz. La salida vuelve al muestreo nativo y se cuantiza a PCM24; el informe identifica el modo aplicado.
4. `inspect_pcm()` recorre el WAV **final** en bloques de 65.536 frames. Se vuelven a medir loudness y true peak sobre ese archivo cuantizado. El pico independiente usa interpolación de al menos 4× y al menos 192 kHz, 64 taps y relleno al final para incluir la cola del filtro; se conserva también el pico de muestra.
5. Se reserva inicialmente 0,3 dB bajo el techo configurado por posibles picos de remuestreo. Si QC falla, se ajusta techo/offset y se repite desde la misma fuente, hasta tres candidatos. La tolerancia numérica de true peak es 0,02 dB. La cola sobrante del remuestreo se recorta al número original de frames; una salida demasiado corta falla QC, no se rellena con silencio.
6. Solo con QC aprobado se crean waveform/reproducción/informe y se publica la carpeta `mastered/`. Un fallo restaura la publicación anterior. `download()` exige un informe válido, la generación correctiva vigente, formato PCM24 y SHA-256 coincidente con los bytes del WAV.

## Output QC obligatorio

| Control | Condición |
|---|---|
| Loudness | Integrado dentro de la tolerancia del preset |
| True peak | Como máximo el techo, con 0,02 dB de tolerancia numérica |
| Saturación de salida | Cero muestras a escala completa |
| Duración | Exactamente los mismos frames |
| Canales | Sin cambios |
| Muestreo | Sin cambios |
| NaN/Inf | Todas las muestras finitas |
| Silencio accidental | Loudness medible y potencia media >10⁻¹² |

Un candidato fallido devuelve `422 mastering_qc_failed` y no se publica. Un archivo alterado, informe incoherente o cambio de correcciones invalida descarga/reproducción (`404 mastering_not_found`). Un cambio de preset vuelve a renderizar; una petición igual puede reutilizar el resultado comprobado. La UI identifica el preset del resultado aunque se seleccione otro todavía sin aplicar.

## Límites y verificación

El LRA puede ser inestable antes de 60 s y no se informa antes de 3 s. Para normalizar hace falta integrado medible: al menos 400 ms y señal suficiente por encima de las puertas. La normalización dinámica puede modificar el balance temporal; compara escuchando. QC no repara una saturación ya grabada ni certifica calidad perceptual. La estimación de true peak y el motor de FFmpeg están comprobados con señales sintéticas; Aurea no declara certificación EBU ni validación sobre un corpus de podcasts.

La capacidad de renderizado, los límites de ingesta y la caducidad de 24 h siguen vigentes. Se eliminan temporales al terminar, y restos antiguos al arrancar/cada 60 s. La cancelación del navegador no detiene un worker iniciado; los jobs corresponden a fase 14. La sustitución de directorios puede interrumpir una lectura entre renombrados, como en el procesado correctivo.

`backend/tests/test_mastering.py` comprueba niveles de referencia, ventanas, picos entre muestras, limitación dinámica, forma/estéreo/muestreos impares, presets externos, ocho controles, caché, alteración, invalidación, fallos de publicación y capacidad. Las pruebas de interfaz cubren selección, QC rechazado, comparación, descarga y errores. Los cuatro casos de `scripts/smoke_audio.py` verifican también masterización y descarga PCM24 por HTTP, localmente y en Docker CI.

Comprobación local adicional: un tono sintético de 30 min a **8004 Hz** se genera por bloques y pasa la cadena completa en **37,8 s** (factor de tiempo real 0,021). Mantiene **14.407.200 frames**, 18.001 puntos de medida antes/después, alcanza −16,0 LUFS y supera todos los controles en el primer candidato. Es una medición de este equipo, no una garantía de tiempo para otros contenidos o sistemas.
