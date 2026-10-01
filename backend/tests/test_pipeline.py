"""Registry, streaming runner guards and the corrective decision rules."""

import math
import re
import tempfile
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from numpy.typing import NDArray

from app.analysis.analyzer import analyze_audio
from app.diagnostics.engine import diagnose_audio
from app.domain.activity import NoiseProfile
from app.domain.analysis import AudioAnalysis, BandEnergy, Dynamics, Spectrum
from app.domain.diagnostics import DIAGNOSTIC_CODES, Diagnostic, EvidenceValue
from app.domain.errors import InvalidProcessingPlan, ProcessingFailed
from app.domain.processing import ParameterValue, ProcessingPlan, ProcessingStep
from app.pipeline import runner
from app.pipeline.decision_engine import RULES, recommend_corrective_plan
from app.pipeline.noise_plan import recommend_processing_plan
from app.pipeline.registry import ProcessorRegistry, default_registry
from app.pipeline.runner import prepare, run_plan
from app.processors.base import BaseProcessor, Block, BlockProcessor, Parameters
from app.processors.pregain import PreGainProcessor

RATE = 16000


def _write(path: Path, samples: NDArray[np.float64], rate: int = RATE) -> Path:
    sf.write(path, samples, rate, subtype="FLOAT")
    return path


def _step(processor: str, enabled: bool = True, **parameters: ParameterValue) -> ProcessingStep:
    return ProcessingStep(processor, enabled, dict(parameters), "prueba")


def _plan(*steps: ProcessingStep) -> ProcessingPlan:
    return ProcessingPlan("test", "0.6.0", list(steps))


def _run(tmp_path: Path, samples: NDArray[np.float64], plan: ProcessingPlan) -> NDArray[np.float64]:
    registry = default_registry()
    source = _write(tmp_path / "in.wav", samples)
    steps = prepare(plan, registry, RATE, 1 if samples.ndim == 1 else samples.shape[1])
    run_plan(source, tmp_path / "out.wav", steps, registry)
    rendered, rate = sf.read(tmp_path / "out.wav", dtype="float64", always_2d=True)
    assert rate == RATE
    return np.asarray(rendered)


def test_registry_creates_known_processors_and_rejects_duplicates() -> None:
    registry = default_registry()
    assert registry.names() == (
        "dc_removal",
        "high_pass",
        "dehum",
        "pre_gain",
        "noise_reduction",
        "speech_leveler",
        "compressor",
    )
    assert registry.create("pre_gain").name == "pre_gain"
    with pytest.raises(ValueError):
        registry.create("reverb")
    with pytest.raises(ValueError):
        registry.register("pre_gain", PreGainProcessor)
    with pytest.raises(ValueError):
        ProcessorRegistry().register("gain", PreGainProcessor)


class _Invert(BaseProcessor):
    name = "invert"

    def validate(
        self, params: Parameters, sample_rate: int, channels: int
    ) -> dict[str, ParameterValue]:
        return {}

    def open(self, params: Parameters, sample_rate: int, channels: int) -> BlockProcessor:
        class Stream:
            def process(self, block: Block) -> Block:
                return -block

        return Stream()


def test_new_processors_register_without_changing_the_runner(tmp_path: Path) -> None:
    registry = default_registry()
    registry.register("invert", _Invert)
    samples = 0.2 * np.sin(np.arange(RATE) / 10)
    source = _write(tmp_path / "in.wav", samples)
    steps = prepare(_plan(_step("invert")), registry, RATE, 1)
    run_plan(source, tmp_path / "out.wav", steps, registry)
    rendered = sf.read(tmp_path / "out.wav", dtype="float64")[0]
    np.testing.assert_allclose(rendered, -samples, atol=1e-7)


def test_runner_preserves_length_channels_and_skips_disabled_steps(tmp_path: Path) -> None:
    samples = np.random.default_rng(1).normal(0, 0.05, (RATE + 123, 2))
    rendered = _run(tmp_path, samples, _plan(_step("pre_gain", False, gain_db=12.0)))
    assert rendered.shape == samples.shape
    np.testing.assert_allclose(rendered, samples, atol=1e-7)


def test_runner_output_is_independent_of_block_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    samples = np.random.default_rng(2).normal(0, 0.05, RATE * 2) + 0.01
    plan = _plan(
        _step("dc_removal", offsets=[0.01]),
        _step("high_pass", cutoff_hz=80.0),
        _step("dehum", fundamental_hz=50.0, harmonics=3),
        _step("pre_gain", gain_db=3.0),
    )
    first = _run(tmp_path, samples, plan)
    monkeypatch.setattr(runner, "BLOCK_FRAMES", 1021)
    (tmp_path / "out.wav").unlink()
    registry = default_registry()
    steps = prepare(plan, registry, RATE, 1)
    run_plan(tmp_path / "in.wav", tmp_path / "out.wav", steps, registry, block_frames=1021)
    second = sf.read(tmp_path / "out.wav", dtype="float64", always_2d=True)[0]
    np.testing.assert_allclose(second, first, atol=1e-7)


def test_overshoot_is_trimmed_to_ceiling_without_clipping(tmp_path: Path) -> None:
    samples = 0.9 * np.sin(2 * np.pi * 440 * np.arange(RATE) / RATE)
    registry = default_registry()
    source = _write(tmp_path / "in.wav", samples)
    steps = prepare(_plan(_step("pre_gain", gain_db=6.0)), registry, RATE, 1)
    result = run_plan(source, tmp_path / "out.wav", steps, registry)
    rendered = sf.read(tmp_path / "out.wav", dtype="float64")[0]
    assert result.rendered_peak == pytest.approx(0.9 * 10 ** (6 / 20), rel=1e-4)
    assert float(np.max(np.abs(rendered))) == pytest.approx(10 ** (-0.1 / 20), rel=1e-5)
    assert result.safety_gain_db == pytest.approx(-0.1 - 20 * math.log10(0.9) - 6, abs=1e-3)
    # A constant gain keeps the waveform shape: no sample was clipped.
    np.testing.assert_allclose(rendered / np.max(np.abs(rendered)), samples / 0.9, atol=1e-6)
    assert not (tmp_path / "out.raw.wav").exists()


class _Broken(BaseProcessor):
    name = "broken"

    def validate(
        self, params: Parameters, sample_rate: int, channels: int
    ) -> dict[str, ParameterValue]:
        return {}

    def open(self, params: Parameters, sample_rate: int, channels: int) -> BlockProcessor:
        class Stream:
            def process(self, block: Block) -> Block:
                return block * np.nan

        return Stream()


def test_non_finite_processor_output_fails_safely(tmp_path: Path) -> None:
    registry = default_registry()
    registry.register("broken", _Broken)
    source = _write(tmp_path / "in.wav", np.full(RATE, 0.1))
    steps = prepare(_plan(_step("broken")), registry, RATE, 1)
    with pytest.raises(ProcessingFailed):
        run_plan(source, tmp_path / "out.wav", steps, registry)


def test_invalid_plans_name_the_offending_step() -> None:
    with pytest.raises(InvalidProcessingPlan, match="Paso 2"):
        prepare(_plan(_step("pre_gain", gain_db=1.0), _step("echo")), default_registry(), RATE, 1)
    with pytest.raises(InvalidProcessingPlan, match="offsets"):
        prepare(_plan(_step("dc_removal", offsets=[0.0])), default_registry(), RATE, 2)


def _diagnostic(
    code: str, detected: bool = False, confidence: float = 0.8, **evidence: EvidenceValue
) -> Diagnostic:
    observed: dict[str, EvidenceValue] = dict(evidence) or {"x": 1}
    return Diagnostic(code, detected, 0.5 if detected else 0.0, confidence, "m", observed, {"p": 1})


def _analysis(**changes: object) -> AudioAnalysis:
    base = AudioAnalysis(
        audio_id="a" * 32,
        analyzer_version="0.3.0",
        sample_rate=48000,
        channels=1,
        duration_seconds=10.0,
        peak_dbfs=-6.0,
        rms_dbfs=-20.0,
        crest_factor_db=14.0,
        integrated_lufs=-18.0,
        true_peak_dbtp=-5.5,
        dc_offset=[0.0],
        zero_crossing_rate=0.05,
        silence_percent=0.0,
        silence_threshold_dbfs=-60.0,
        bands=[BandEnergy("Subgraves", 0, 80, 0.001, 1.0)],
        spectrum=Spectrum([0.0, 100.0, 1000.0], [None, -60.0, -70.0]),
        dynamics=Dynamics(100.0, []),
        diagnostics_version="0.6.0",
        diagnostics=[_diagnostic(code) for code in DIAGNOSTIC_CODES],
        noise_profile=NoiseProfile(10, 1.0, -60.0, -62.0, 0.5, 0.1, 0.9, [], []),
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def _with(analysis: AudioAnalysis, diagnostic: Diagnostic) -> AudioAnalysis:
    return replace(
        analysis,
        diagnostics=[diagnostic if d.code == diagnostic.code else d for d in analysis.diagnostics],
    )


def _steps(plan: ProcessingPlan) -> dict[str, ProcessingStep]:
    return {step.processor: step for step in plan.steps}


def test_clean_recording_gets_a_visible_but_inactive_plan() -> None:
    plan = recommend_corrective_plan(_analysis())
    assert [step.processor for step in plan.steps] == [
        "dc_removal",
        "high_pass",
        "dehum",
        "pre_gain",
    ]
    assert not any(step.enabled for step in plan.steps)
    assert all(step.reason for step in plan.steps)
    assert recommend_corrective_plan(_analysis()) == plan


def test_dc_removal_uses_measured_offsets_above_threshold() -> None:
    steps = _steps(recommend_corrective_plan(_analysis(channels=2, dc_offset=[0.02, -0.0005])))
    assert steps["dc_removal"].enabled
    assert steps["dc_removal"].parameters["offsets"] == [0.02, -0.0005]
    small = _steps(recommend_corrective_plan(_analysis(dc_offset=[0.0005])))
    assert not small["dc_removal"].enabled


def test_dehum_follows_detected_base_harmonics_and_contrast() -> None:
    hum = _diagnostic(
        "hum",
        True,
        0.82,
        base_frequency_hz=60,
        harmonic_frequencies_hz=[60.0, 120.0, 180.0],
        harmonic_contrast_db=[18.0, 12.0, 11.0],
    )
    step = _steps(recommend_corrective_plan(_with(_analysis(), hum)))["dehum"]
    assert step.enabled and step.source_diagnostic == "hum" and step.confidence == 0.82
    assert step.parameters == {
        "fundamental_hz": 60.0,
        "harmonics": 3,
        "q": 30.0,
        "attenuation_db": 24.0,
    }


def test_low_confidence_diagnostics_never_enable_a_step() -> None:
    hum = _diagnostic("hum", True, RULES.min_confidence - 0.01, base_frequency_hz=50)
    rumble = _diagnostic("rumble", True, RULES.min_confidence - 0.01)
    plan = recommend_corrective_plan(_with(_with(_analysis(), hum), rumble))
    assert not _steps(plan)["dehum"].enabled and not _steps(plan)["high_pass"].enabled


def test_pre_gain_raises_quiet_audio_without_exceeding_the_peak_ceiling() -> None:
    quiet = _with(
        _analysis(integrated_lufs=-40.0, true_peak_dbtp=-20.0), _diagnostic("low_level", True)
    )
    assert _steps(recommend_corrective_plan(quiet))["pre_gain"].parameters == {"gain_db": 16.0}
    peaky = _with(
        _analysis(integrated_lufs=-40.0, true_peak_dbtp=-8.0), _diagnostic("low_level", True)
    )
    assert _steps(recommend_corrective_plan(peaky))["pre_gain"].parameters == {"gain_db": 5.0}
    hot = _with(_analysis(true_peak_dbtp=0.2), _diagnostic("low_headroom", True))
    step = _steps(recommend_corrective_plan(hot))["pre_gain"]
    assert step.enabled and step.parameters == {"gain_db": -3.2}
    assert step.source_diagnostic == "low_headroom"


def _real_analysis(samples: NDArray[np.float64], rate: int) -> AudioAnalysis:
    path = _write(Path(tempfile.mkdtemp()) / "x.wav", samples, rate)
    measured = analyze_audio(path, "b" * 32)
    diagnosis = diagnose_audio(path, measured)
    return replace(
        measured,
        diagnostics=diagnosis.diagnostics,
        noise_profile=diagnosis.noise_profile,
        speech_activity=diagnosis.speech_activity,
        estimated_snr_db=diagnosis.estimated_snr_db,
    )


@pytest.mark.parametrize(("rumble_hz", "cutoff"), [(25, 60.0), (40, 60.0), (55, 80.0), (70, 100.0)])
def test_high_pass_picks_the_lowest_preset_that_removes_the_rumble(
    rumble_hz: float, cutoff: float
) -> None:
    rate = 48000
    time = np.arange(rate * 6) / rate
    voice = ((time % 1.0) < 0.6) * sum(
        0.1 / h * np.sin(2 * np.pi * 140 * h * time) for h in range(1, 9)
    )
    background = np.random.default_rng(1).normal(0, 0.002, len(time))
    rumble = 0.05 * np.sin(2 * np.pi * rumble_hz * time)
    step = _steps(recommend_corrective_plan(_real_analysis(voice + background + rumble, rate)))[
        "high_pass"
    ]
    assert step.enabled and step.parameters["cutoff_hz"] == cutoff
    assert step.evidence["background_spectrum_window"] == "0.25 s"
    reductions = step.evidence["rumble_reduction_db"]
    assert isinstance(reductions, list)
    chosen = RULES.highpass_presets_hz.index(cutoff)
    assert reductions[chosen] >= RULES.rumble_reduction_db
    assert all(value < RULES.rumble_reduction_db for value in reductions[:chosen])


def test_studio_labels_every_plan_parameter_and_evidence_key() -> None:
    labels_file = Path(__file__).parents[2] / "frontend/src/features/audio/Measurements.tsx"
    if not labels_file.exists():
        pytest.skip("frontend sources are not available")
    labels = set(re.findall(r"^\s+([a-z0-9_]+): \{ label:", labels_file.read_text("utf-8"), re.M))
    hum = _diagnostic("hum", True, 0.9, base_frequency_hz=50, harmonic_frequencies_hz=[50.0])
    plans = [
        recommend_corrective_plan(_analysis()),
        recommend_corrective_plan(_with(_analysis(dc_offset=[0.1]), hum)),
    ]
    published = {
        key for plan in plans for step in plan.steps for key in (*step.parameters, *step.evidence)
    }
    assert published <= labels, f"Missing studio labels: {sorted(published - labels)}"


def test_spectral_tail_reaches_dynamics_and_curves_independent_of_blocks(tmp_path: Path) -> None:
    samples = np.random.default_rng(12).normal(0, 0.001, (RATE * 3 + 57, 2))
    time = np.arange(len(samples)) / RATE
    voice = 0.1 * np.sin(2 * np.pi * 150 * time)
    samples += voice[:, None]
    registry = default_registry()
    plan = _plan(
        _step(
            "noise_reduction",
            noise_frequencies_hz=[0.0, RATE / 2],
            noise_psd_dbfs_per_hz=[-99.0, -99.0],
        ),
        _step("speech_leveler", speech_starts_seconds=[0.0], speech_ends_seconds=[4.0]),
        _step("compressor"),
    )
    source = _write(tmp_path / "input.wav", samples)
    steps = prepare(plan, registry, RATE, 2)
    outputs, histories = [], []
    for size in (117, 65536):
        target = tmp_path / f"render-{size}.wav"
        result = run_plan(source, target, steps, registry, size)
        outputs.append(sf.read(target, always_2d=True)[0])
        histories.append(result.gain_envelopes)
        assert all(curve.times_seconds[-1] == (len(samples) - 1) / RATE for curve in histories[-1])
    np.testing.assert_allclose(outputs[0], outputs[1], atol=1e-7)
    for first, second in zip(*histories, strict=True):
        assert first.times_seconds == second.times_seconds
        np.testing.assert_allclose(first.gain_db, second.gain_db, atol=1e-9)


def test_manual_pre_gain_moves_leveler_noise_guard(tmp_path: Path) -> None:
    samples = np.random.default_rng(9).normal(0, 0.003, RATE * 2)
    plan = _plan(
        _step("pre_gain", gain_db=20.0),
        _step(
            "speech_leveler",
            speech_starts_seconds=[0.0],
            speech_ends_seconds=[2.0],
            noise_floor_dbfs=20 * math.log10(0.003),
            target_rms_dbfs=-12.0,
        ),
    )
    rendered = _run(tmp_path, samples, plan)
    np.testing.assert_allclose(rendered[:, 0], samples * 10, atol=2e-8)


def test_missing_voice_has_explainable_inactive_dynamics() -> None:
    plan = recommend_processing_plan(_analysis())
    for step in plan.steps[-2:]:
        assert not step.enabled and step.evidence["speech_available"] is False
        assert step.reason
