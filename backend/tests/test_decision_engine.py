"""Confidence boundaries, reproducibility and externally configured intervention."""

import json
import re
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from test_pipeline import _analysis, _diagnostic, _with

from app.domain.activity import ActivitySegment, SpeechActivity
from app.domain.presets import CorrectiveRules, DynamicsRules, GateRules
from app.mastering.presets import load_presets
from app.pipeline.decision_engine import decide
from app.pipeline.presets import load_processing_presets
from app.pipeline.registry import default_registry
from app.pipeline.runner import prepare


@pytest.mark.parametrize(
    "preset_id,threshold", [("natural", 0.9), ("balanced", 0.85), ("studio", 0.8)]
)
@pytest.mark.parametrize("boundary", ["below_review", "review", "below_auto", "auto"])
def test_confidence_boundaries_never_apply_a_review_proposal(
    preset_id: str, threshold: float, boundary: str
) -> None:
    score = {
        "below_review": 0.549999,
        "review": 0.55,
        "below_auto": threshold - 0.000001,
        "auto": threshold,
    }[boundary]
    diagnostic = _diagnostic(
        "hum",
        True,
        score,
        base_frequency_hz=60,
        harmonic_frequencies_hz=[60.0, 120.0],
        harmonic_contrast_db=[18.0, 12.0],
    )
    analysis = _with(_analysis(), diagnostic)
    plan = decide(
        analysis,
        analysis.diagnostics,
        load_processing_presets()[preset_id],
        load_presets()["podcast_standard"],
    )
    hum = next(step for step in plan.steps if step.processor == "dehum")
    assert hum.enabled == (boundary == "auto")
    assert hum.decision == (
        "disabled"
        if boundary == "below_review"
        else "automatic"
        if boundary == "auto"
        else "recommended"
    )
    if boundary != "below_review":
        assert hum.parameters["fundamental_hz"] == 60
    assert hum.source_diagnostic == "hum" and hum.confidence == score


@pytest.mark.parametrize("preset_id", ["natural", "balanced", "studio"])
def test_full_plan_is_order_independent_serializable_and_executable(preset_id: str) -> None:
    analysis = _with(_analysis(dc_offset=[0.02]), _diagnostic("hum", True, 0.95))
    preset, target = load_processing_presets()[preset_id], load_presets()["broadcast_r128"]
    first = decide(analysis, analysis.diagnostics, preset, target)
    second = decide(analysis, list(reversed(analysis.diagnostics)), preset, target)
    assert json.dumps(asdict(first), allow_nan=False) == json.dumps(asdict(second), allow_nan=False)
    assert analysis.dc_offset == [0.02]  # no input mutation
    assert len(prepare(first, default_registry(), analysis.sample_rate, analysis.channels)) == 7
    assert [s.processor for s in first.mastering_steps] == [
        "loudness_normalization",
        "true_peak_limiter",
    ]
    assert first.mastering_steps[0].parameters["target_lufs"] == -23
    assert first.mastering_steps[1].parameters["max_true_peak_dbtp"] == -1
    assert all(s.enabled and s.decision == "automatic" for s in first.mastering_steps)


def test_intensity_presets_change_parameters_without_enabling_weak_evidence() -> None:
    analysis = _with(_analysis(), _diagnostic("hum", True, 0.52))
    plans = {
        key: decide(analysis, analysis.diagnostics, policy, load_presets()["podcast_standard"])
        for key, policy in load_processing_presets().items()
    }
    for plan in plans.values():
        assert not next(s for s in plan.steps if s.processor == "dehum").enabled
    assert [
        next(s for s in plans[key].steps if s.processor == "compressor").parameters["ratio"]
        for key in ("natural", "balanced", "studio")
    ] == [1.6, 2, 3]
    assert [
        next(s for s in plans[key].steps if s.processor == "noise_reduction").parameters["strength"]
        for key in ("natural", "balanced", "studio")
    ] == ["light", "balanced", "strong"]


def test_severity_gate_and_missing_data_do_not_mistake_confidence_for_a_problem() -> None:
    diagnostic = replace(_diagnostic("hum", True, 0.95), severity=0.01)
    analysis = _with(_analysis(), diagnostic)
    plan = decide(
        analysis,
        analysis.diagnostics,
        load_processing_presets()["studio"],
        load_presets()["podcast_standard"],
    )
    assert not next(s for s in plan.steps if s.processor == "dehum").enabled
    assert not next(s for s in plan.steps if s.processor == "noise_reduction").enabled
    assert not next(s for s in plan.steps if s.processor == "speech_leveler").enabled
    with pytest.raises(ValueError, match="Duplicate"):
        decide(
            analysis,
            [diagnostic, diagnostic],
            load_processing_presets()["studio"],
            load_presets()["podcast_standard"],
        )


@pytest.mark.parametrize("seconds,lufs", [(10.0, None), (0.2, -23.0)])
def test_silence_and_short_clips_have_no_automatic_master(
    seconds: float, lufs: float | None
) -> None:
    analysis = _analysis(duration_seconds=seconds, integrated_lufs=lufs)
    plan = decide(
        analysis,
        analysis.diagnostics,
        load_processing_presets()["balanced"],
        load_presets()["podcast_standard"],
    )
    assert all(not s.enabled and s.decision == "disabled" for s in plan.mastering_steps)


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("corrective", "dc_threshold", float("nan")),
        ("corrective", "highpass_presets_hz", (80.0, 60.0)),
        ("dynamics", "max_boost_db", 13.0),
        ("dynamics", "compressor_ratio", float("inf")),
        ("gating", "automatic_confidence", 0.5),
        ("gating", "minimum_background_seconds", 0.01),
    ],
)
def test_invalid_external_thresholds_fail_at_configuration_load(
    section: str, key: str, value: object
) -> None:
    cls = {"corrective": CorrectiveRules, "dynamics": DynamicsRules, "gating": GateRules}[section]
    with pytest.raises(ValueError):
        cls(**{key: value})


@pytest.mark.parametrize("damage", ["unknown_field", "missing_preset", "unknown_threshold"])
def test_configuration_rejects_typographical_errors(tmp_path: Path, damage: str) -> None:
    source = Path(__file__).parents[1] / "app/pipeline/presets.toml"
    config = source.read_text("utf-8")
    if damage == "unknown_field":
        config = config.replace('noise_strength = "light"', 'noise_strenght = "light"')
    elif damage == "missing_preset":
        config = config.replace("presets.studio", "presets.experimental")
    else:
        config = config.replace("max_boost_db = 4.0", "max_bost_db = 4.0")
    target = tmp_path / "bad.toml"
    target.write_text(config, "utf-8")
    with pytest.raises(ValueError):
        load_processing_presets(target)


def test_every_decision_parameter_and_evidence_has_a_spanish_ui_label() -> None:
    ui = (Path(__file__).parents[2] / "frontend/src/features/audio/Measurements.tsx").read_text(
        "utf-8"
    )
    labels = set(re.findall(r"^  (\w+): \{ label:", ui, re.MULTILINE))
    analysis = _analysis()
    plan = decide(
        analysis,
        analysis.diagnostics,
        load_processing_presets()["balanced"],
        load_presets()["podcast_standard"],
    )
    keys = {
        key for s in [*plan.steps, *plan.mastering_steps] for key in [*s.parameters, *s.evidence]
    }
    assert not keys - labels


def test_voice_evidence_does_not_override_a_medium_confidence_headroom_trigger() -> None:
    activity = SpeechActivity(
        "energy",
        "0.5.0",
        0.03,
        5,
        5,
        0,
        50,
        -24,
        [ActivitySegment("noise", 0, 5), ActivitySegment("speech", 5, 10)],
        {},
    )
    analysis = _with(
        _analysis(speech_activity=activity, true_peak_dbtp=0),
        _diagnostic("low_headroom", True, 0.6),
    )
    plan = decide(
        analysis,
        analysis.diagnostics,
        load_processing_presets()["balanced"],
        load_presets()["podcast_standard"],
    )
    compressor = next(s for s in plan.steps if s.processor == "compressor")
    assert compressor.source_diagnostic == "low_headroom"
    assert compressor.confidence == 0.6
    assert compressor.decision == "recommended" and not compressor.enabled
    # The independently measured peak margin can still justify reducing input gain.
    gain = next(s for s in plan.steps if s.processor == "pre_gain")
    assert gain.enabled and gain.evidence["direct_measurement_rule"] is True


def test_natural_manual_fallback_does_not_exceed_its_dehum_depth_limit() -> None:
    analysis = _analysis()
    policy = load_processing_presets()["natural"]
    plan = decide(analysis, analysis.diagnostics, policy, load_presets()["podcast_standard"])
    step = next(s for s in plan.steps if s.processor == "dehum")
    assert not step.enabled and step.parameters["attenuation_db"] == 24
