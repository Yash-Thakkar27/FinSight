"""Charts. One restrained style everywhere: white background, one accent colour, greys for
context. No gradients, no decoration."""

import pandas as pd
import plotly.graph_objects as go

ACCENT = "#1f4e79"
GREY = "#8c8c8c"
LIGHT_GREY = "#d0d0d0"
NEGATIVE = "#9a3b3b"
# Distinguishable muted tones for multi-series charts; the first is the accent.
SERIES = [ACCENT, "#8c8c8c", "#b07a2a", "#4f7f6f", "#7a5c8e", "#9a3b3b", "#3f6f9f", "#a0a060"]
HEIGHT = 340


def base_layout(title: str = "", height: int = HEIGHT, **extra) -> dict:
    layout = dict(
        title=dict(text=title, font=dict(size=14, color="#222222"), x=0, xanchor="left"),
        template="simple_white", height=height, margin=dict(l=10, r=10, t=44, b=10),
        font=dict(family="Helvetica, Arial, sans-serif", size=12, color="#333333"),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1,
                    font=dict(size=11)),
        hovermode="x unified",
    )
    layout.update(extra)
    return layout


def line_chart(frame: pd.DataFrame, title: str, y_title: str = "", y_format: str | None = None,
               highlight: str | None = None, height: int = HEIGHT) -> go.Figure:
    """One line per column of `frame` (index = x). `highlight` is drawn in the accent colour."""
    figure = go.Figure()
    others = [c for c in frame.columns if c != highlight]
    palette = [c for c in SERIES if c != ACCENT] if highlight else SERIES
    for i, column in enumerate(others):
        figure.add_trace(go.Scatter(x=frame.index, y=frame[column], name=str(column), mode="lines",
                                    line=dict(width=1.4, color=palette[i % len(palette)])))
    if highlight:
        figure.add_trace(go.Scatter(x=frame.index, y=frame[highlight], name=str(highlight),
                                    mode="lines", line=dict(width=2.4, color=ACCENT)))
    figure.update_layout(**base_layout(title, height))
    figure.update_yaxes(title_text=y_title, tickformat=y_format, showgrid=True,
                        gridcolor="#eeeeee")
    return figure


def bar_chart(labels, values, title: str, y_title: str = "", y_format: str | None = None,
              text=None, height: int = HEIGHT) -> go.Figure:
    colours = [NEGATIVE if (v is not None and pd.notna(v) and v < 0) else ACCENT for v in values]
    figure = go.Figure(go.Bar(x=list(labels), y=list(values), marker_color=colours, text=text,
                              textposition="outside", cliponaxis=False))
    figure.update_layout(**base_layout(title, height, hovermode="closest"))
    figure.update_yaxes(title_text=y_title, tickformat=y_format, showgrid=True,
                        gridcolor="#eeeeee")
    return figure


def grouped_bars(frame: pd.DataFrame, title: str, y_title: str = "",
                 y_format: str | None = None) -> go.Figure:
    figure = go.Figure()
    for i, column in enumerate(frame.columns):
        figure.add_trace(go.Bar(x=list(frame.index), y=frame[column], name=str(column),
                                marker_color=SERIES[i % len(SERIES)]))
    figure.update_layout(**base_layout(title, barmode="group", hovermode="closest"))
    figure.update_yaxes(title_text=y_title, tickformat=y_format, showgrid=True,
                        gridcolor="#eeeeee")
    return figure


def histogram(series: dict, title: str, x_title: str = "", x_format: str | None = None,
              bins: int = 60) -> go.Figure:
    figure = go.Figure()
    for i, (name, values) in enumerate(series.items()):
        figure.add_trace(go.Histogram(x=values, name=name, nbinsx=bins, opacity=0.6,
                                      marker_color=SERIES[i % len(SERIES)],
                                      histnorm="probability density"))
    figure.update_layout(**base_layout(title, barmode="overlay", hovermode="closest"))
    figure.update_xaxes(title_text=x_title, tickformat=x_format)
    figure.update_yaxes(title_text="density")
    return figure


def heatmap(matrix: pd.DataFrame, title: str, zmin: float = -1, zmax: float = 1,
            height: int = 620) -> go.Figure:
    """Correlation heatmap: white at zero, the accent colour at +1."""
    figure = go.Figure(go.Heatmap(
        z=matrix.to_numpy(), x=list(matrix.columns), y=list(matrix.index), zmin=zmin, zmax=zmax,
        colorscale=[[0.0, NEGATIVE], [0.5, "#ffffff"], [1.0, ACCENT]],
        colorbar=dict(title="corr.", thickness=12),
        hovertemplate="%{y} / %{x}: %{z:.2f}<extra></extra>"))
    figure.update_layout(**base_layout(title, height, hovermode="closest"))
    figure.update_xaxes(tickangle=-60, tickfont=dict(size=9))
    figure.update_yaxes(tickfont=dict(size=9), autorange="reversed")
    return figure


def scatter(frame: pd.DataFrame, x: str, y: str, colour: str, symbol: str | None, text: str,
            title: str, x_title: str, y_title: str, height: int = 460) -> go.Figure:
    """Points coloured by one category and, optionally, shaped by another."""
    symbols = ["circle", "square", "diamond", "triangle-up", "x", "cross", "star", "hexagon"]
    symbol_map = {}
    if symbol:
        symbol_map = {value: symbols[i % len(symbols)]
                      for i, value in enumerate(sorted(frame[symbol].unique()))}
    figure = go.Figure()
    for i, (value, group) in enumerate(frame.groupby(colour)):
        figure.add_trace(go.Scatter(
            x=group[x], y=group[y], mode="markers+text", name=f"{colour} {value}",
            text=group[text], textposition="top center", textfont=dict(size=9),
            marker=dict(size=11, color=SERIES[i % len(SERIES)],
                        symbol=[symbol_map[s] for s in group[symbol]] if symbol else "circle",
                        line=dict(width=0.5, color="white")),
            hovertext=group[symbol] if symbol else None))
    figure.update_layout(**base_layout(title, height, hovermode="closest"))
    figure.update_xaxes(title_text=x_title, zeroline=True, zerolinecolor=LIGHT_GREY)
    figure.update_yaxes(title_text=y_title, zeroline=True, zerolinecolor=LIGHT_GREY)
    return figure


def position_chart(rows: pd.DataFrame, title: str) -> go.Figure:
    """Where the target sits within its peers' range for each metric.

    rows: label, low, high (peer min and max), median, target, scaled to the peer range so
    different metrics share one axis: 0 = peer minimum, 1 = peer maximum.
    """
    figure = go.Figure()
    figure.add_trace(go.Bar(y=rows["label"], x=[1] * len(rows), orientation="h",
                            marker_color="#eef1f4", hoverinfo="skip", showlegend=False))
    figure.add_trace(go.Scatter(y=rows["label"], x=rows["median_scaled"], mode="markers",
                                name="Peer median", hovertext=rows["median_text"],
                                marker=dict(symbol="line-ns", size=16, color=GREY,
                                            line=dict(width=2, color=GREY))))
    figure.add_trace(go.Scatter(y=rows["label"], x=rows["target_scaled"], mode="markers",
                                name="Target", hovertext=rows["target_text"],
                                marker=dict(size=12, color=ACCENT)))
    figure.update_layout(**base_layout(title, max(220, 34 * len(rows) + 90), hovermode="closest",
                                       barmode="overlay"))
    figure.update_xaxes(range=[-0.25, 1.25], tickvals=[0, 0.5, 1],
                        ticktext=["peer min", "", "peer max"])
    figure.update_yaxes(autorange="reversed")
    return figure
