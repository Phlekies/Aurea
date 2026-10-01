"""Execute corrections and verified mastering under one rendering reservation."""

from app.domain.automatic import AutomaticReport
from app.domain.errors import AudioServiceUnavailable, InvalidProcessingPlan
from app.domain.processing import ProcessingPlan
from app.pipeline.decision_engine import PLAN_VERSION, mastering_decisions
from app.pipeline.runner import prepare
from app.services.mastering import MasteringService
from app.services.processing import ProcessingService


class AutomaticService:
    def __init__(self, processing: ProcessingService, mastering: MasteringService) -> None:
        self.processing = processing
        self.mastering = mastering

    def process(
        self,
        asset_id: str,
        plan: ProcessingPlan | None = None,
        preset: str = "balanced",
        mastering_preset: str = "podcast_standard",
    ) -> AutomaticReport:
        requested = plan or self.processing.recommend(
            asset_id, preset=preset, mastering_preset=mastering_preset
        )
        asset = self.processing._asset(asset_id)
        analysis = self.processing.analysis_service.get(asset_id)
        policy = self.processing.presets.get(requested.preset)
        target = self.mastering.presets.get(requested.mastering_preset)
        if (
            policy is None
            or target is None
            or requested.version != PLAN_VERSION
            or requested.preset_version != policy.version
        ):
            raise InvalidProcessingPlan("El plan está desactualizado. Vuelve a elegir un preset.")
        terminal = mastering_decisions(analysis, target)
        # Mandatory terminal decisions may not bypass output QC or change the named target.
        if requested.mastering_steps != terminal or not all(step.enabled for step in terminal):
            raise InvalidProcessingPlan(
                "El plan necesita un objetivo de masterización válido y loudness medible."
            )
        # Validate every correction before publishing anything. Manual checkbox/parameter
        # choices are respected; the engine does not silently re-enable recommendations.
        prepare(requested, self.processing.registry, asset.sample_rate, asset.channels)
        if not self.processing.capacity.acquire(blocking=False):
            raise AudioServiceUnavailable("El procesador está ocupado. Reintenta en un momento.")
        try:
            corrected = self.processing._process(asset_id, requested, capacity_reserved=True)
            mastered = self.mastering._master(
                asset_id, requested.mastering_preset, capacity_reserved=True
            )
            return AutomaticReport(corrected, mastered)
        finally:
            self.processing.capacity.release()
