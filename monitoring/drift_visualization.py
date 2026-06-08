"""Plotly visualizations for feature drift reports in Raf3nd ML4T."""

from __future__ import annotations

import structlog
import plotly.graph_objects as go

from monitoring.drift_detector import DriftReport

logger = structlog.get_logger(__name__)

# Per-feature drift threshold used by FeatureDriftDetector.
DRIFT_THRESHOLD = 0.15

SEVERITY_COLORS = {
    "none": "#2ca02c",
    "warning": "#ff7f0e",
    "critical": "#d62728",
    "unknown": "#7f7f7f",
}

# Neutral color for bars that are below the per-feature drift threshold.
NEUTRAL_COLOR = "#9ecae1"


def severity_color(severity: str) -> str:
    """Return a hex color for a severity, defaulting to the grey "unknown" color."""
    return SEVERITY_COLORS.get(severity, SEVERITY_COLORS["unknown"])


def psi_bar_figure(report: DriftReport) -> go.Figure:
    """Build a horizontal bar chart of per-feature PSI scores from a DriftReport.

    One bar per feature in ``report.raw_psi_scores``, sorted descending by PSI.
    Bars above the 0.15 drift threshold are colored by ``report.severity``;
    bars at or below the threshold use a neutral color. A dashed reference line
    at the 0.15 threshold is annotated on the chart.
    """
    # Sort descending by PSI so the most-drifted features are at the top.
    sorted_items = sorted(
        report.raw_psi_scores.items(), key=lambda item: item[1], reverse=True
    )
    features = [name for name, _ in sorted_items]
    scores = [value for _, value in sorted_items]

    drifted_color = severity_color(report.severity)
    colors = [
        drifted_color if value > DRIFT_THRESHOLD else NEUTRAL_COLOR
        for value in scores
    ]

    figure = go.Figure(
        data=[
            go.Bar(
                x=scores,
                y=features,
                orientation="h",
                marker_color=colors,
                hovertemplate="%{y}: PSI=%{x:.4f}<extra></extra>",
            )
        ]
    )

    # Plotly draws the first category at the bottom; reverse so the largest PSI
    # appears at the top of a horizontal bar chart.
    figure.update_yaxes(autorange="reversed")

    title = (
        f"PSI drift — {report.symbol} "
        f"[{report.severity}] max_psi={report.max_psi:.4f}"
    )
    figure.update_layout(
        title=title,
        xaxis_title="PSI",
        yaxis_title="Feature",
        showlegend=False,
    )

    # Dashed reference line at the drift threshold.
    figure.add_vline(
        x=DRIFT_THRESHOLD,
        line_dash="dash",
        line_color=SEVERITY_COLORS["critical"],
        annotation_text=f"drift threshold = {DRIFT_THRESHOLD}",
        annotation_position="top",
    )

    return figure


def psi_bar_html(report: DriftReport) -> str:
    """Return an embeddable HTML ``<div>`` for the PSI bar chart.

    Uses ``include_plotlyjs="cdn"`` and ``full_html=False`` so the result can be
    dropped into a Flask template via ``|safe``.
    """
    figure = psi_bar_figure(report)
    return figure.to_html(include_plotlyjs="cdn", full_html=False)
