import numpy as np
import pandas as pd
import plotly.graph_objects as go

from monitoring.drift_detector import FeatureDriftDetector
from monitoring.drift_visualization import (
    SEVERITY_COLORS,
    psi_bar_figure,
    psi_bar_html,
    severity_color,
)


def _build_report():
    detector = FeatureDriftDetector(model_name="test")
    reference_df = pd.DataFrame(
        {
            "feature_a": np.random.normal(0, 1, 500),
            "feature_b": np.random.normal(0, 1, 500),
            "feature_c": np.random.normal(0, 1, 500),
        }
    )
    current_df = pd.DataFrame(
        {
            "feature_a": np.random.normal(3, 1, 500),  # large shift -> drift
            "feature_b": np.random.normal(0, 1, 500),  # no shift
            "feature_c": np.random.normal(0.2, 1, 500),  # small shift
        }
    )
    return detector.detect("BTC/USDT", reference_df, current_df)


def test_severity_color_returns_correct_colors():
    assert severity_color("none") == SEVERITY_COLORS["none"]
    assert severity_color("warning") == SEVERITY_COLORS["warning"]
    assert severity_color("critical") == SEVERITY_COLORS["critical"]


def test_severity_color_defaults_to_grey_for_unknown():
    assert severity_color("not-a-real-severity") == SEVERITY_COLORS["unknown"]
    assert severity_color("") == SEVERITY_COLORS["unknown"]


def test_psi_bar_figure_returns_figure_with_one_trace_per_feature():
    report = _build_report()
    figure = psi_bar_figure(report)

    assert isinstance(figure, go.Figure)
    assert len(figure.data) == 1

    bar = figure.data[0]
    n_features = len(report.raw_psi_scores)
    assert len(bar.x) == n_features
    assert len(bar.y) == n_features


def test_psi_bar_html_is_non_empty_str_containing_plotly():
    report = _build_report()
    html = psi_bar_html(report)

    assert isinstance(html, str)
    assert html
    assert "plotly" in html
