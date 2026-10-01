"""Every publishable master must pass checks measured on the final quantized WAV."""

from app.domain.mastering import LoudnessMeasurements, MasteringPreset, OutputQC, QualityCheck
from app.mastering.meter import PcmFacts


def output_qc(
    source: PcmFacts, output: PcmFacts, metrics: LoudnessMeasurements, preset: MasteringPreset
) -> OutputQC:
    level, peak = metrics.integrated_lufs, metrics.true_peak_dbtp
    checks = [
        QualityCheck(
            "loudness",
            level is not None and abs(level - preset.target_lufs) <= preset.loudness_tolerance_lu,
            level,
            f"{preset.target_lufs:g} LUFS ±{preset.loudness_tolerance_lu:g} LU",
        ),
        QualityCheck(
            "true_peak",
            peak is not None and peak <= preset.max_true_peak_dbtp + 0.02,
            peak,
            f"≤{preset.max_true_peak_dbtp:g} dBTP (tolerancia 0,02 dB)",
        ),
        QualityCheck(
            "clipping", output.clipped_samples == 0, output.clipped_samples, "0 muestras saturadas"
        ),
        QualityCheck(
            "duration", output.frames == source.frames, output.frames, f"{source.frames} frames"
        ),
        QualityCheck(
            "channels",
            output.channels == source.channels,
            output.channels,
            f"{source.channels} canales",
        ),
        QualityCheck(
            "sample_rate",
            output.sample_rate == source.sample_rate,
            output.sample_rate,
            f"{source.sample_rate} Hz",
        ),
        QualityCheck("finite", output.finite, output.finite, "Todas las muestras finitas"),
        QualityCheck(
            "not_silent",
            output.power > 1e-12 and level is not None,
            metrics.rms_dbfs,
            "Señal audible con loudness medible",
        ),
    ]
    return OutputQC(all(check.passed for check in checks), checks)
