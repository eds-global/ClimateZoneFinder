"""Interactive psychrometric chart & bioclimatic strategy analysis.

Andrew Marsh-style fully interactive psychrometric chart built with Plotly:
  • Saturation curve, RH curves, wet-bulb lines, enthalpy labels
  • 8760 hourly state points (colour by month / DBT / RH / frequency)
  • ASHRAE comfort zone + Givoni bioclimatic strategy zones
  • Month-by-month animation with play/pause
  • 3D frequency surface (DBT x humidity-ratio x hours)
  • Passive-strategy effectiveness ranking (Climate-Consultant style)

Psychrometric relations follow ASHRAE Fundamentals (2017), Ch. 1.
Strategy zone boundaries are simplified Givoni/Milne bioclimatic-chart
approximations, documented next to each polygon definition.
"""

from __future__ import annotations

import calendar

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from .st_compat import st

from .config import P_ATM

# ── Chart bounds ───────────────────────────────────────────────────────────────
T_MIN, T_MAX = -10.0, 50.0     # dry-bulb axis (°C)
W_MIN, W_MAX = 0.0, 30.0       # humidity ratio axis (g/kg dry air)

MONTH_COLORS = [
    "#1f77b4", "#4a90d9", "#17becf", "#2ca02c", "#8fbc45", "#bcbd22",
    "#ffbf00", "#ff7f0e", "#e05c2a", "#d62728", "#9467bd", "#5c6bc0",
]

BRAND = "#a85c42"


# ── Psychrometric relations (ASHRAE Fundamentals 2017, Ch.1) ──────────────────

def sat_vapor_pressure(t_c):
    """Saturation vapour pressure (Pa) over liquid water, Magnus form."""
    t_c = np.asarray(t_c, dtype=float)
    return 610.94 * np.exp(17.625 * t_c / (t_c + 243.04))


def humidity_ratio(t_c, rh_pct, pressure=P_ATM):
    """Humidity ratio (kg/kg dry air) from DBT (°C) and RH (%)."""
    pv = np.clip(rh_pct, 0, 100) / 100.0 * sat_vapor_pressure(t_c)
    pv = np.minimum(pv, pressure * 0.99)
    return 0.621945 * pv / (pressure - pv)


def rh_from_w(t_c, w, pressure=P_ATM):
    """Relative humidity (%) from DBT (°C) and humidity ratio (kg/kg)."""
    pv = w * pressure / (0.621945 + w)
    return np.clip(100.0 * pv / sat_vapor_pressure(t_c), 0, 100)


def enthalpy(t_c, w):
    """Moist-air specific enthalpy (kJ/kg dry air)."""
    return 1.006 * t_c + w * (2501.0 + 1.86 * t_c)


def wet_bulb_stull(t_c, rh_pct):
    """Wet-bulb temperature (°C) — Stull (2011) approximation."""
    t = np.asarray(t_c, dtype=float)
    rh = np.clip(np.asarray(rh_pct, dtype=float), 1, 100)
    return (
        t * np.arctan(0.151977 * np.sqrt(rh + 8.313659))
        + np.arctan(t + rh) - np.arctan(rh - 1.676331)
        + 0.00391838 * rh ** 1.5 * np.arctan(0.023101 * rh)
        - 4.686035
    )


def w_on_wb_line(t_c, twb, pressure=P_ATM):
    """Humidity ratio along a constant wet-bulb line (ASHRAE eq. 35)."""
    ws_wb = humidity_ratio(twb, 100.0, pressure)
    num = (2501.0 - 2.326 * twb) * ws_wb - 1.006 * (t_c - twb)
    den = 2501.0 + 1.86 * t_c - 4.186 * twb
    return num / den


def dew_point(t_c, rh_pct):
    """Dew-point temperature (°C), inverse Magnus."""
    rh = np.clip(np.asarray(rh_pct, dtype=float), 1, 100)
    gamma = np.log(rh / 100.0) + 17.625 * t_c / (t_c + 243.04)
    return 243.04 * gamma / (17.625 - gamma)


# ── Strategy zones (simplified Givoni bioclimatic chart) ──────────────────────
# Each zone is a polygon in (DBT °C, w g/kg) space. Boundaries follow the
# Givoni building bioclimatic chart as popularised by Climate Consultant;
# they are climate-design screening bands, not compliance limits.

def _rh_curve_pts(rh, t0, t1, n=25):
    ts = np.linspace(t0, t1, n)
    return list(zip(ts, humidity_ratio(ts, rh) * 1000.0))


def comfort_zone_polygon():
    """ASHRAE-style still-air comfort zone: 20–26.5 °C, 20 % RH ≤ φ, w ≤ 12 g/kg."""
    left = _rh_curve_pts(100, 20.0, 20.0, 1)  # placeholder replaced below
    top_w = 12.0
    pts = []
    # left edge: 20 °C from RH 20 % up to w=12 or saturation
    w20_lo = humidity_ratio(20.0, 20.0) * 1000
    w20_hi = min(top_w, humidity_ratio(20.0, 100.0) * 1000)
    pts.append((20.0, w20_lo))
    pts.append((20.0, w20_hi))
    # top edge: w = 12 g/kg from 20 → 26.5 °C
    pts.append((26.5, top_w))
    # right edge: 26.5 °C down to RH 20 %
    pts.append((26.5, humidity_ratio(26.5, 20.0) * 1000))
    # bottom edge: RH 20 % curve back to 20 °C
    pts += [(t, w) for t, w in reversed(_rh_curve_pts(20.0, 20.0, 26.5))]
    return pts


STRATEGY_DEFS = {
    "Comfort (still air)": {
        "color": "rgba(46,160,67,0.85)",
        "poly": "comfort",
        "desc": "Inside the still-air comfort zone — no intervention needed.",
    },
    "Natural Ventilation": {
        # Air movement ≈1.5 m/s extends the upper comfort limit to ~32 °C,
        # humidity acceptable up to ~90 % RH / 17 g/kg.
        "color": "rgba(23,162,184,0.75)",
        "desc": "Elevated air speed (fans / cross-ventilation) restores comfort up to ≈32 °C.",
    },
    "Evaporative Cooling": {
        # Direct evap cooling follows constant wet-bulb lines; effective while
        # Twb ≤ 22 °C and DBT ≤ 42 °C.
        "color": "rgba(255,193,7,0.75)",
        "desc": "Dry heat (low wet-bulb) — adiabatic cooling can reach the comfort zone.",
    },
    "High Thermal Mass + Night Flush": {
        # Mass with night purge handles up to ~36 °C in dry climates (w ≤ 14).
        "color": "rgba(156,39,176,0.65)",
        "desc": "Heavy construction + night-time purge ventilation damps the daily peak.",
    },
    "Passive Solar Heating": {
        # Solar gains can close the gap when 10 ≤ DBT < 20 °C.
        "color": "rgba(255,87,34,0.65)",
        "desc": "Under-heated but mild — orientation, glazing and gains can heat passively.",
    },
    "Humidification": {
        "color": "rgba(3,169,244,0.6)",
        "desc": "Comfortable temperature but very dry air (w < 4 g/kg).",
    },
    "Mechanical Cooling": {
        "color": "rgba(183,28,28,0.7)",
        "desc": "Hot and/or humid beyond all passive strategies — active cooling needed.",
    },
    "Mechanical Heating": {
        "color": "rgba(21,101,192,0.7)",
        "desc": "Below 10 °C — active heating (or high-performance envelope) required.",
    },
}


def classify_strategies(df: pd.DataFrame) -> pd.DataFrame:
    """Classify every hour into applicable passive strategies.

    Returns df with boolean columns per strategy plus `best_strategy`
    (highest-priority applicable strategy).
    """
    t = df["dry_bulb_temperature"].to_numpy(dtype=float)
    rh = df["relative_humidity"].to_numpy(dtype=float)
    w = humidity_ratio(t, rh) * 1000.0  # g/kg
    twb = wet_bulb_stull(t, rh)

    out = df.copy()
    out["w_gkg"] = w
    out["twb"] = twb
    out["h_kjkg"] = enthalpy(t, w / 1000.0)
    out["tdp"] = dew_point(t, rh)

    comfort = (t >= 20.0) & (t <= 26.5) & (w <= 12.0) & (rh >= 20.0)
    nv = (t > 26.5) & (t <= 32.0) & (w <= 17.0) & ~comfort
    evap = (t > 26.5) & (t <= 42.0) & (twb <= 22.0) & ~comfort
    mass = (t > 26.5) & (t <= 36.0) & (w <= 14.0) & ~comfort
    solar = (t >= 10.0) & (t < 20.0)
    humid = (t >= 20.0) & (t <= 26.5) & (w < 4.0)
    heat = t < 10.0
    cool = (t > 26.5) & ~(nv | evap | mass)

    out["Comfort (still air)"] = comfort
    out["Natural Ventilation"] = nv
    out["Evaporative Cooling"] = evap
    out["High Thermal Mass + Night Flush"] = mass
    out["Passive Solar Heating"] = solar
    out["Humidification"] = humid
    out["Mechanical Cooling"] = cool
    out["Mechanical Heating"] = heat

    priority = [
        "Comfort (still air)", "Natural Ventilation", "Evaporative Cooling",
        "High Thermal Mass + Night Flush", "Passive Solar Heating",
        "Humidification", "Mechanical Cooling", "Mechanical Heating",
    ]
    best = np.full(len(out), "Comfort (still air)", dtype=object)
    assigned = np.zeros(len(out), dtype=bool)
    for name in priority:
        mask = out[name].to_numpy() & ~assigned
        best[mask] = name
        assigned |= mask
    best[~assigned] = "Comfort (still air)"
    out["best_strategy"] = best
    return out


# ── Chart scaffolding ─────────────────────────────────────────────────────────

def _grid_traces(show_wb: bool, show_enthalpy: bool):
    """Static psychrometric grid: saturation + RH curves + wet-bulb lines."""
    traces = []
    ts = np.linspace(T_MIN, T_MAX, 140)

    # RH curves 10..90 %
    for rh in range(10, 100, 10):
        w = humidity_ratio(ts, rh) * 1000.0
        m = w <= W_MAX
        traces.append(go.Scatter(
            x=ts[m], y=w[m], mode="lines",
            line=dict(color="rgba(120,140,160,0.35)", width=1),
            hoverinfo="skip", showlegend=False,
        ))
        # in-line label at the top of each curve
        if m.any():
            xi = ts[m][-1]
            yi = w[m][-1]
            traces.append(go.Scatter(
                x=[xi], y=[yi], mode="text", text=[f"{rh}%"],
                textposition="top left",
                textfont=dict(size=9, color="rgba(120,140,160,0.8)"),
                hoverinfo="skip", showlegend=False,
            ))

    # Saturation curve (100 % RH)
    w_sat = humidity_ratio(ts, 100.0) * 1000.0
    m = w_sat <= W_MAX
    traces.append(go.Scatter(
        x=ts[m], y=w_sat[m], mode="lines",
        line=dict(color="rgba(70,90,110,0.9)", width=2.2),
        name="Saturation (100% RH)", hoverinfo="skip", showlegend=False,
    ))

    # Wet-bulb lines
    if show_wb:
        for twb in range(-5, 40, 5):
            t_line = np.linspace(twb, T_MAX, 60)
            w_line = w_on_wb_line(t_line, float(twb)) * 1000.0
            m = (w_line >= W_MIN) & (w_line <= W_MAX)
            if not m.any():
                continue
            traces.append(go.Scatter(
                x=t_line[m], y=w_line[m], mode="lines",
                line=dict(color="rgba(46,134,171,0.30)", width=1, dash="dot"),
                hoverinfo="skip", showlegend=False,
            ))
            traces.append(go.Scatter(
                x=[t_line[m][0]], y=[w_line[m][0]], mode="text",
                text=[f"{twb}°WB"], textposition="top right",
                textfont=dict(size=8, color="rgba(46,134,171,0.65)"),
                hoverinfo="skip", showlegend=False,
            ))

    # Enthalpy labels along saturation curve
    if show_enthalpy:
        for h in range(10, 110, 20):
            # solve T on saturation curve where enthalpy = h (bisect on grid)
            hs = enthalpy(ts, humidity_ratio(ts, 100.0))
            idx = int(np.argmin(np.abs(hs - h)))
            if w_sat[idx] <= W_MAX:
                traces.append(go.Scatter(
                    x=[ts[idx]], y=[w_sat[idx]], mode="text",
                    text=[f"{h} kJ/kg"], textposition="top left",
                    textfont=dict(size=8, color="rgba(160,90,60,0.75)"),
                    hoverinfo="skip", showlegend=False,
                ))
    return traces


def _zone_trace(pts, name, color, visible=True):
    xs = [p[0] for p in pts] + [pts[0][0]]
    ys = [p[1] for p in pts] + [pts[0][1]]
    return go.Scatter(
        x=xs, y=ys, mode="lines", name=name,
        line=dict(color=color, width=2),
        fill="toself",
        fillcolor=color.replace("0.85", "0.18").replace("0.75", "0.15")
                       .replace("0.7", "0.13").replace("0.65", "0.12")
                       .replace("0.6", "0.10"),
        hoverinfo="name", visible=visible, legendgroup="zones",
    )


def strategy_zone_traces(selected: list[str]):
    """Polygon traces for the selected strategy zones."""
    traces = []
    if "Comfort (still air)" in selected:
        traces.append(_zone_trace(
            comfort_zone_polygon(), "Comfort zone",
            STRATEGY_DEFS["Comfort (still air)"]["color"]))
    if "Natural Ventilation" in selected:
        pts = ([(26.5, humidity_ratio(26.5, 20.0) * 1000), (26.5, 12.0),
                (26.5, 17.0), (32.0, 17.0)]
               + [(32.0, humidity_ratio(32.0, 20.0) * 1000)]
               + [(t, w) for t, w in reversed(_rh_curve_pts(20.0, 26.5, 32.0))])
        traces.append(_zone_trace(pts, "Natural ventilation",
                                  STRATEGY_DEFS["Natural Ventilation"]["color"]))
    if "Evaporative Cooling" in selected:
        # region between comfort right edge and Twb=22 line up to 42 °C
        t_line = np.linspace(26.5, 42.0, 40)
        w_top = w_on_wb_line(t_line, 22.0) * 1000.0
        pts = ([(t, w) for t, w in zip(t_line, w_top)]
               + [(42.0, 0.5), (26.5, 0.5)])
        traces.append(_zone_trace(pts, "Evaporative cooling",
                                  STRATEGY_DEFS["Evaporative Cooling"]["color"]))
    if "High Thermal Mass + Night Flush" in selected:
        pts = [(26.5, 1.0), (26.5, 14.0), (36.0, 14.0), (36.0, 1.0)]
        traces.append(_zone_trace(pts, "Thermal mass + night flush",
                                  STRATEGY_DEFS["High Thermal Mass + Night Flush"]["color"]))
    if "Passive Solar Heating" in selected:
        w10 = humidity_ratio(10.0, 100.0) * 1000
        w20 = min(12.0, humidity_ratio(20.0, 100.0) * 1000)
        pts = [(10.0, 0.5), (10.0, w10), (20.0, w20), (20.0, 0.5)]
        traces.append(_zone_trace(pts, "Passive solar heating",
                                  STRATEGY_DEFS["Passive Solar Heating"]["color"]))
    return traces


HOVER_TMPL = (
    "<b>%{customdata[0]}</b><br>"
    "DBT: %{x:.1f} °C<br>"
    "Humidity ratio: %{y:.1f} g/kg<br>"
    "RH: %{customdata[1]:.0f} %<br>"
    "Wet-bulb: %{customdata[2]:.1f} °C<br>"
    "Dew point: %{customdata[3]:.1f} °C<br>"
    "Enthalpy: %{customdata[4]:.1f} kJ/kg<br>"
    "Strategy: %{customdata[5]}<extra></extra>"
)


def _point_customdata(d: pd.DataFrame) -> np.ndarray:
    labels = d["datetime"].dt.strftime("%d %b, %H:%M")
    return np.stack([
        labels, d["relative_humidity"], d["twb"], d["tdp"], d["h_kjkg"],
        d["best_strategy"],
    ], axis=1)


def build_psychro_figure(pdf: pd.DataFrame, color_mode: str,
                         zones: list[str], show_wb: bool,
                         show_enthalpy: bool, animate: bool) -> go.Figure:
    """Full interactive psychrometric chart."""
    fig = go.Figure()
    for tr in _grid_traces(show_wb, show_enthalpy):
        fig.add_trace(tr)
    for tr in strategy_zone_traces(zones):
        fig.add_trace(tr)

    if color_mode == "Frequency":
        fig.add_trace(go.Histogram2d(
            x=pdf["dry_bulb_temperature"], y=pdf["w_gkg"],
            xbins=dict(start=T_MIN, end=T_MAX, size=1.0),
            ybins=dict(start=W_MIN, end=W_MAX, size=0.5),
            colorscale=[[0, "rgba(255,255,255,0)"], [0.08, "#c6dbef"],
                        [0.3, "#6baed6"], [0.6, "#2171b5"], [1.0, "#08306b"]],
            colorbar=dict(title="hours", thickness=12, len=0.5, y=0.75),
            zmin=0, name="Hours",
        ))
    elif animate:
        frames = []
        for m in range(1, 13):
            d = pdf[pdf["month"] == m]
            frames.append(go.Frame(
                name=calendar.month_abbr[m],
                data=[go.Scattergl(
                    x=d["dry_bulb_temperature"], y=d["w_gkg"],
                    mode="markers",
                    marker=dict(size=5, color=MONTH_COLORS[m - 1], opacity=0.75),
                    customdata=_point_customdata(d),
                    hovertemplate=HOVER_TMPL, name=calendar.month_abbr[m],
                )],
            ))
        d0 = pdf[pdf["month"] == 1]
        fig.add_trace(go.Scattergl(
            x=d0["dry_bulb_temperature"], y=d0["w_gkg"], mode="markers",
            marker=dict(size=5, color=MONTH_COLORS[0], opacity=0.75),
            customdata=_point_customdata(d0),
            hovertemplate=HOVER_TMPL, name="Jan",
        ))
        fig.frames = frames
        fig.update_layout(
            updatemenus=[dict(
                type="buttons", direction="left", x=0.0, y=1.14,
                buttons=[
                    dict(label="▶ Play", method="animate",
                         args=[None, dict(frame=dict(duration=650, redraw=True),
                                          transition=dict(duration=250),
                                          fromcurrent=True)]),
                    dict(label="⏸ Pause", method="animate",
                         args=[[None], dict(frame=dict(duration=0, redraw=False),
                                            mode="immediate")]),
                ],
            )],
            sliders=[dict(
                y=1.09, x=0.18, len=0.8, pad=dict(t=0),
                currentvalue=dict(prefix="Month: ", font=dict(size=13, color=BRAND)),
                steps=[dict(label=calendar.month_abbr[m], method="animate",
                            args=[[calendar.month_abbr[m]],
                                  dict(frame=dict(duration=0, redraw=True),
                                       mode="immediate")])
                       for m in range(1, 13)],
            )],
        )
    else:
        if color_mode == "Month":
            for m in sorted(pdf["month"].unique()):
                d = pdf[pdf["month"] == m]
                fig.add_trace(go.Scattergl(
                    x=d["dry_bulb_temperature"], y=d["w_gkg"], mode="markers",
                    marker=dict(size=4, color=MONTH_COLORS[int(m) - 1], opacity=0.65),
                    customdata=_point_customdata(d),
                    hovertemplate=HOVER_TMPL,
                    name=calendar.month_abbr[int(m)], legendgroup="months",
                ))
        else:
            cfg = {
                "Temperature": ("dry_bulb_temperature", "RdYlBu_r", "°C"),
                "Relative Humidity": ("relative_humidity", "Blues", "%"),
                "Strategy": (None, None, None),
            }
            if color_mode == "Strategy":
                for name, meta in STRATEGY_DEFS.items():
                    d = pdf[pdf["best_strategy"] == name]
                    if d.empty:
                        continue
                    fig.add_trace(go.Scattergl(
                        x=d["dry_bulb_temperature"], y=d["w_gkg"], mode="markers",
                        marker=dict(size=4, color=meta["color"], opacity=0.8),
                        customdata=_point_customdata(d),
                        hovertemplate=HOVER_TMPL, name=name, legendgroup="strat",
                    ))
            else:
                col, scale, unit = cfg[color_mode]
                fig.add_trace(go.Scattergl(
                    x=pdf["dry_bulb_temperature"], y=pdf["w_gkg"], mode="markers",
                    marker=dict(size=4, color=pdf[col], colorscale=scale,
                                opacity=0.7,
                                colorbar=dict(title=unit, thickness=12, len=0.5, y=0.75)),
                    customdata=_point_customdata(pdf),
                    hovertemplate=HOVER_TMPL, name="Hours", showlegend=False,
                ))

    fig.update_layout(
        height=650,
        xaxis=dict(title="Dry-bulb temperature (°C)", range=[T_MIN, T_MAX],
                   dtick=5, showgrid=True, gridcolor="rgba(200,200,200,0.25)",
                   zeroline=False),
        yaxis=dict(title="Humidity ratio (g/kg dry air)", range=[W_MIN, W_MAX],
                   dtick=2, side="right", showgrid=True,
                   gridcolor="rgba(200,200,200,0.25)", zeroline=False),
        plot_bgcolor="white",
        legend=dict(orientation="h", yanchor="bottom", y=-0.22, x=0,
                    font=dict(size=10)),
        margin=dict(l=30, r=60, t=70, b=30),
        title=dict(text="Psychrometric Chart — hourly climate states",
                   font=dict(size=16, color="#2c3e50")),
    )
    return fig


def build_3d_frequency_figure(pdf: pd.DataFrame) -> go.Figure:
    """3D surface: hours binned over (DBT, humidity ratio)."""
    t_edges = np.arange(T_MIN, T_MAX + 1, 1.0)
    w_edges = np.arange(W_MIN, W_MAX + 0.5, 0.5)
    H, _, _ = np.histogram2d(pdf["dry_bulb_temperature"], pdf["w_gkg"],
                             bins=[t_edges, w_edges])
    tc = (t_edges[:-1] + t_edges[1:]) / 2
    wc = (w_edges[:-1] + w_edges[1:]) / 2
    fig = go.Figure(go.Surface(
        x=tc, y=wc, z=H.T,
        colorscale="Viridis",
        colorbar=dict(title="hours", thickness=14, len=0.6),
        hovertemplate=("DBT: %{x:.0f} °C<br>w: %{y:.1f} g/kg<br>"
                       "Hours: %{z:.0f}<extra></extra>"),
        contours=dict(z=dict(show=True, usecolormap=True,
                             highlightcolor="white", project_z=True)),
    ))
    fig.update_layout(
        height=560,
        scene=dict(
            xaxis_title="Dry-bulb (°C)", yaxis_title="w (g/kg)",
            zaxis_title="Hours",
            camera=dict(eye=dict(x=1.6, y=-1.6, z=0.9)),
            aspectratio=dict(x=1.4, y=1.0, z=0.6),
        ),
        margin=dict(l=0, r=0, t=30, b=0),
        title=dict(text="Climate frequency landscape (hours per state bin)",
                   font=dict(size=15, color="#2c3e50")),
    )
    return fig


def build_strategy_bars(pdf: pd.DataFrame) -> tuple[go.Figure, pd.DataFrame]:
    """Ranked strategy effectiveness + monthly stacked best-strategy chart."""
    n = len(pdf)
    rows = []
    for name in STRATEGY_DEFS:
        cnt = int(pdf[name].sum())
        rows.append({"Strategy": name, "Hours": cnt, "Share": 100.0 * cnt / max(n, 1)})
    rank = pd.DataFrame(rows)

    monthly = (
        pdf.groupby(["month", "best_strategy"]).size().rename("hours").reset_index()
    )
    fig = go.Figure()
    for name, meta in STRATEGY_DEFS.items():
        d = monthly[monthly["best_strategy"] == name]
        if d.empty:
            continue
        full = pd.DataFrame({"month": range(1, 13)}).merge(d, on="month", how="left").fillna(0)
        fig.add_trace(go.Bar(
            x=[calendar.month_abbr[m] for m in full["month"]],
            y=full["hours"], name=name, marker_color=meta["color"],
            hovertemplate="%{y:.0f} h<extra>" + name + "</extra>",
        ))
    fig.update_layout(
        barmode="stack", height=380,
        yaxis_title="Hours", plot_bgcolor="white",
        legend=dict(orientation="h", y=-0.25, font=dict(size=10)),
        margin=dict(l=30, r=10, t=40, b=10),
        title=dict(text="Best-fit design strategy, month by month",
                   font=dict(size=15, color="#2c3e50")),
    )
    return fig, rank


# ── Streamlit render ──────────────────────────────────────────────────────────

def render(df: pd.DataFrame, metadata: dict, months: list[int] | None = None):
    """Render the psychrometric analysis section."""
    d = df if not months else df[df["month"].isin(months)]
    if d.empty:
        st.warning("No hours in the selected month range.")
        return

    pdf = classify_strategies(d)

    city = metadata.get("city") or "site"
    st.markdown(
        f'<div class="section-title">Psychrometric & Bioclimatic Analysis — {city}</div>',
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4 = st.columns([1.2, 1.4, 1, 1])
    with c1:
        color_mode = st.selectbox(
            "Colour points by",
            ["Month", "Strategy", "Temperature", "Relative Humidity", "Frequency"],
            key="psy_color_mode",
        )
    with c2:
        zones = st.multiselect(
            "Strategy zones",
            ["Comfort (still air)", "Natural Ventilation", "Evaporative Cooling",
             "High Thermal Mass + Night Flush", "Passive Solar Heating"],
            default=["Comfort (still air)", "Natural Ventilation",
                     "Evaporative Cooling"],
            key="psy_zones",
        )
    with c3:
        show_wb = st.toggle("Wet-bulb lines", value=True, key="psy_wb")
        show_h = st.toggle("Enthalpy labels", value=False, key="psy_h")
    with c4:
        animate = st.toggle("Animate months ▶", value=False, key="psy_anim",
                            help="Play the year month-by-month")

    fig = build_psychro_figure(pdf, color_mode, zones, show_wb, show_h,
                               animate and color_mode == "Month")
    st.plotly_chart(fig, use_container_width=True, key="psy_chart")

    if animate and color_mode != "Month":
        st.caption("Animation runs in **Month** colour mode — switch the colour selector to Month.")

    # ── Strategy effectiveness ────────────────────────────────────────────────
    st.markdown('<div class="section-title">Passive Strategy Effectiveness</div>',
                unsafe_allow_html=True)

    bars_fig, rank = build_strategy_bars(pdf)

    k1, k2, k3, k4 = st.columns(4)
    total = len(pdf)
    comfort_pct = rank.loc[rank["Strategy"] == "Comfort (still air)", "Share"].iat[0]
    passive_names = ["Natural Ventilation", "Evaporative Cooling",
                     "High Thermal Mass + Night Flush", "Passive Solar Heating",
                     "Humidification"]
    passive_recoverable = 100.0 * (
        pdf[passive_names].any(axis=1) & ~pdf["Comfort (still air)"]
    ).sum() / max(total, 1)
    mech_cool = rank.loc[rank["Strategy"] == "Mechanical Cooling", "Share"].iat[0]
    mech_heat = rank.loc[rank["Strategy"] == "Mechanical Heating", "Share"].iat[0]

    for col, label, value, meta in [
        (k1, "Already comfortable", f"{comfort_pct:.1f}%", f"{int(total*comfort_pct/100)} h"),
        (k2, "Recoverable passively", f"{passive_recoverable:.1f}%", "any passive strategy"),
        (k3, "Needs mechanical cooling", f"{mech_cool:.1f}%", "beyond passive limits"),
        (k4, "Needs mechanical heating", f"{mech_heat:.1f}%", "DBT < 10 °C"),
    ]:
        with col:
            st.markdown(
                f'<div class="kpi-card"><div class="kpi-label">{label}</div>'
                f'<div class="kpi-value">{value}</div>'
                f'<div class="kpi-meta">{meta}</div></div>',
                unsafe_allow_html=True,
            )

    st.plotly_chart(bars_fig, use_container_width=True, key="psy_bars")

    with st.expander("Strategy definitions & applicability table"):
        show = rank.copy()
        show["Share"] = show["Share"].map(lambda v: f"{v:.1f}%")
        show["What it means"] = show["Strategy"].map(
            lambda s: STRATEGY_DEFS[s]["desc"])
        st.dataframe(show, use_container_width=True, hide_index=True)
        st.caption(
            "Zones follow the Givoni building bioclimatic chart (as used in "
            "Climate Consultant): screening-level boundaries, evaluated on the "
            "psychrometric state of each of the selected hours. Strategies "
            "overlap — the monthly chart assigns each hour to its "
            "highest-priority applicable strategy."
        )

    # ── 3D frequency surface ──────────────────────────────────────────────────
    with st.expander("🌐 3D climate-frequency landscape", expanded=False):
        st.plotly_chart(build_3d_frequency_figure(pdf),
                        use_container_width=True, key="psy_3d")
        st.caption(
            "Each cell counts the hours the climate spends at that "
            "temperature-moisture state. Sharp ridges = a stable climate; "
            "broad plateaus = large seasonal swings."
        )
