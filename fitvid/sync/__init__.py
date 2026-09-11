from .clap import detect_clap_offset, match_clap_to_fit_event
from .config import (
    FitGeneratorSync,
    RecordingDevice,
    SyncConfig,
    apply_media_offsets,
    load_sync_config,
    shift_activity,
)
from .crosscorr import correlate_audio_files, extract_audio_waveform
from .drift import apply_drift_offset, compute_drift_ppm, interpolate_offset

__all__ = [
    "correlate_audio_files",
    "extract_audio_waveform",
    "detect_clap_offset",
    "match_clap_to_fit_event",
    "compute_drift_ppm",
    "interpolate_offset",
    "apply_drift_offset",
    "FitGeneratorSync",
    "RecordingDevice",
    "SyncConfig",
    "load_sync_config",
    "shift_activity",
    "apply_media_offsets",
]
