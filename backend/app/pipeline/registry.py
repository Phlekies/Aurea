"""Processor registry: plans name processors, the pipeline instantiates them here."""

from collections.abc import Callable

from app.processors.base import StreamingProcessor
from app.processors.compressor import CompressorProcessor
from app.processors.dc_removal import DcRemovalProcessor
from app.processors.dehum import DeHumProcessor
from app.processors.highpass import HighPassProcessor
from app.processors.noise_reduction import NoiseReducer
from app.processors.pregain import PreGainProcessor
from app.processors.speech_leveler import SpeechLevelerProcessor


class ProcessorRegistry:
    """Name -> factory map; new algorithms register without changing the runner."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[[], StreamingProcessor]] = {}

    def register(self, name: str, factory: Callable[[], StreamingProcessor]) -> None:
        """Add a processor; names are unique and must match the instance name."""
        if name in self._factories:
            raise ValueError(f"Processor already registered: {name}")
        if factory().name != name:
            raise ValueError(f"Processor factory does not create {name}")
        self._factories[name] = factory

    def create(self, name: str) -> StreamingProcessor:
        """Instantiate a registered processor or raise ValueError."""
        try:
            return self._factories[name]()
        except KeyError as error:
            raise ValueError(f"Procesador desconocido: {name}") from error

    def names(self) -> tuple[str, ...]:
        """Registered names in registration order."""
        return tuple(self._factories)


def default_registry() -> ProcessorRegistry:
    """Corrective, spectral and voice dynamics processors in pipeline version 0.8.0."""
    registry = ProcessorRegistry()
    registry.register("dc_removal", DcRemovalProcessor)
    registry.register("high_pass", HighPassProcessor)
    registry.register("dehum", DeHumProcessor)
    registry.register("pre_gain", PreGainProcessor)
    registry.register("noise_reduction", NoiseReducer)
    registry.register("speech_leveler", SpeechLevelerProcessor)
    registry.register("compressor", CompressorProcessor)
    return registry
