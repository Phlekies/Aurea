"""Load one versioned external policy with strict keys and validated ranges."""

import tomllib
from pathlib import Path

from pydantic import TypeAdapter

from app.domain.presets import ProcessingPreset


def load_processing_presets(path: Path | None = None) -> dict[str, ProcessingPreset]:
    data = tomllib.loads((path or Path(__file__).with_name("presets.toml")).read_text("utf-8"))
    if set(data) != {"version", "defaults", "presets"}:
        raise ValueError("Invalid processing configuration keys")
    defaults = data["defaults"]
    sections = {"corrective", "dynamics", "gating"}
    if set(defaults) != sections or set(data["presets"]) != {"natural", "balanced", "studio"}:
        raise ValueError("Missing processing preset or policy")
    adapter = TypeAdapter(ProcessingPreset)
    presets = {}
    for key, values in data["presets"].items():
        allowed = sections | {"name", "description", "noise_algorithm", "noise_strength"}
        if set(values) - allowed:
            raise ValueError("Unknown processing preset field")
        merged = {name: {**defaults[name], **values.get(name, {})} for name in sections}
        for name, fields in merged.items():
            record = ProcessingPreset.__annotations__[name]
            if set(fields) - record.__dataclass_fields__.keys():
                raise ValueError("Unknown decision threshold")
        presets[key] = adapter.validate_python(
            {"id": key, "version": data["version"], **values, **merged}
        )
    return presets
