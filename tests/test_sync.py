from __future__ import annotations

import numpy as np

from fitvid.sync.crosscorr import correlate_waveforms
from fitvid.sync.clap import find_transient_peaks


def test_correlate_known_lag():
    sr = 1000
    t = np.linspace(0, 2, 2 * sr, endpoint=False)
    signal = np.sin(2 * np.pi * 40 * t)
    lag = 0.25
    lag_samples = int(lag * sr)
    reference = np.concatenate([np.zeros(lag_samples), signal])
    target = np.concatenate([signal, np.zeros(lag_samples)])
    # target content starts earlier in its buffer relative to reference
    offset, confidence = correlate_waveforms(reference, target, sr)
    assert confidence > 0.1
    # offset should be near -lag (target leads) or +lag depending on convention
    assert abs(abs(offset) - lag) < 0.05


def test_transient_peaks_finds_clap():
    sr = 8000
    samples = np.zeros(sr, dtype=np.float32)
    samples[1000:1010] = 1.0
    peaks = find_transient_peaks(samples, sr, top_n=3)
    assert peaks
    assert abs(peaks[0][0] - 1000 / sr) < 0.05
