import numpy as np
import pandas as pd
import pytest

from monitoring.drift_detector import FeatureDriftDetector


def test_compute_psi_identical_distributions_is_near_zero():
    detector = FeatureDriftDetector(model_name="test")
    ref = pd.Series(np.random.normal(size=500))
    cur = ref.copy()

    psi = detector.compute_psi(ref, cur)

    assert psi == pytest.approx(0.0, abs=1e-2)


def test_compute_psi_shifted_distribution_is_critical():
    detector = FeatureDriftDetector(model_name="test")
    ref = pd.Series(np.random.normal(0, 1, 500))
    cur = pd.Series(np.random.normal(3, 1, 500))

    psi = detector.compute_psi(ref, cur)

    assert psi > 0.25


def test_detect_returns_critical_severity_for_large_shift():
    detector = FeatureDriftDetector(model_name="test")
    reference_df = pd.DataFrame({"feature_a": np.random.normal(0, 1, 500)})
    current_df = pd.DataFrame({"feature_a": np.random.normal(3, 1, 500)})

    report = detector.detect("BTC/USDT", reference_df, current_df)

    assert report.severity == "critical"
    assert report.recommended_action == "halt_trading"
    assert report.drift_detected is True


def test_detect_returns_none_severity_for_similar_distributions():
    detector = FeatureDriftDetector(model_name="test")
    reference_df = pd.DataFrame({"feature_a": np.random.normal(0, 1, 500)})
    current_df = reference_df.copy()

    report = detector.detect("BTC/USDT", reference_df, current_df)

    assert report.severity == "none"
    assert report.recommended_action == "continue"
    assert report.drift_detected is False
