"""Configuration owns target decisions; the DSP receives a validated preset."""

import tomllib
from pathlib import Path

from pydantic import TypeAdapter

from app.domain.mastering import MasteringPreset


def load_presets(path: Path | None = None) -> dict[str, MasteringPreset]:
    data = tomllib.loads((path or Path(__file__).with_name("presets.toml")).read_text("utf-8"))
    adapter = TypeAdapter(MasteringPreset)
    presets = {key: adapter.validate_python({"id": key, **values}) for key, values in data.items()}
    if not presets or any(
        set(values)
        - {"name", "target_lufs", "max_true_peak_dbtp", "target_lra_lu", "loudness_tolerance_lu"}
        for values in data.values()
    ):
        raise ValueError("Invalid mastering preset configuration")
    return presets
