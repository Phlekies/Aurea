"""Safe, stable ingestion failures suitable for presentation to users."""


class AudioError(Exception):
    """Base domain error; public messages must not expose internal paths or commands."""

    code = "audio_error"
    status_code = 422


class UnsupportedAudioFormat(AudioError):
    """Unsupported extension, MIME type, or decoded container."""

    code = "unsupported_audio_format"
    status_code = 415


class InvalidAudioFile(AudioError):
    """Corrupt, empty, non-finite, or unsupported audio representation."""

    code = "invalid_audio_file"


class AudioTooLarge(AudioError):
    """Upload exceeds the configured byte or duration budget."""

    code = "audio_too_large"
    status_code = 413


class AudioNotFound(AudioError):
    """No audio asset exists for the supplied opaque identifier."""

    code = "audio_not_found"
    status_code = 404


class AudioExpired(AudioError):
    """The temporary recording has reached its retention deadline."""

    code = "audio_expired"
    status_code = 410


class AudioServiceUnavailable(AudioError):
    """Decoder unavailable, timed out, or concurrent ingestion capacity reached."""

    code = "audio_service_unavailable"
    status_code = 503


class AnalysisFailed(AudioError):
    """A diagnostic calculation failed; retry is safe and the original is retained."""

    code = "analysis_failed"
    status_code = 503


class ProcessingFailed(AudioError):
    """A processor produced invalid samples or rendering failed; the original is kept."""

    code = "processing_failed"
    status_code = 503


class InvalidProcessingPlan(AudioError):
    """A requested processor or parameter is unknown or outside its supported range."""

    code = "invalid_processing_plan"
    status_code = 422
