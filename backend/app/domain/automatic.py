"""One reviewed plan and the two independently measured output stages."""

from dataclasses import dataclass

from app.domain.mastering import MasteringReport
from app.domain.processing import ProcessingReport


@dataclass(frozen=True)
class AutomaticReport:
    processing: ProcessingReport
    mastering: MasteringReport
