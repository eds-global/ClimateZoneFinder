"""Relative Humidity tab rendering module."""

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from .st_compat import st

from .dbt_module import (
    build_animated_diurnal,
    build_carpet_heatmap,
    build_month_hour_surface,
)


def render(
    df: pd.DataFrame,
    daily_stats: pd.DataFrame,
    active_tab: str,
    start_date,
    end_date,
    start_hour: int,
    end_hour: int,
) -> None:
    """Dispatch rendering based on the active tab."""
    if active_tab == "Annual Trend":
        _render_annual_trend(df, daily_stats)
    elif active_tab == "Monthly Trend":
        _render_monthly_trend(df, start_date, end_date)
    elif active_tab == "Diurnal Profile":
        _render_diurnal_profile(df, start_hour, end_hour)
    elif active_tab == "Comfort Analysis":
        _render_comfort_analysis(df, daily_stats, start_date, end_date)
    elif active_tab == "Energy Metrics":
        _render_energy_metrics(df, start_date, end_date, start_hour, end_hour)
    elif active_tab == "Heatmap + 3D":
        _render_heatmap_3d(df)


# ─────────────────────────────────────────────────────────────────────────────


def _sustained_high_rh_hours(df: pd.DataFrame, threshold: float = 70.0,
                             min_run_hours: int = 24) -> int:
    """Hours belonging to runs of >= `min_run_hours` consecutive hours with
    RH > `threshold` — a standard mold-germination screening proxy."""
    rh = df.sort_values("datetime")["relative_humidity"]
    mask = rh > threshold
    run_id = (mask != mask.shift()).cumsum()
    run_len = mask.groupby(run_id).transform("size")
    return int((mask & (run_len >= min_run_hours)).sum())


def _condensation_prone_mask(df: pd.DataFrame) -> pd.Series:
    """Hours where the dew point is within 2 °C of the dry-bulb temperature —
    an honest surface-condensation-risk proxy computed from the EPW data."""
    return df["dew_point_temperature"] >= (df["dry_bulb_temperature"] - 2.0)


def _render_heatmap_3d(df: pd.DataFrame) -> None:
    st.plotly_chart(
        build_carpet_heatmap(
            df, "relative_humidity",
            title="Annual Relative Humidity Carpet Plot (Day × Hour)",
            colorscale="Blues", unit="%", series_name="RH",
        ),
        use_container_width=True,
    )
    st.plotly_chart(
        build_month_hour_surface(
            df, "relative_humidity",
            title="Mean Relative Humidity Surface — Month × Hour",
            colorscale="Blues", unit="%", series_name="RH",
        ),
        use_container_width=True,
    )
    st.plotly_chart(
        build_animated_diurnal(
            df, "relative_humidity",
            title="Monthly Diurnal Humidity Profile (animated)",
            unit="%", series_name="RH",
            line_color="#00a8ff", band_fill="rgba(0,150,255,0.25)",
        ),
        use_container_width=True,
    )
    # Dew point is the better "mugginess" metric — RH alone hides absolute moisture.
    st.plotly_chart(
        build_carpet_heatmap(
            df, "dew_point_temperature",
            title="Annual Dew Point Carpet Plot (Day × Hour)",
            colorscale="Viridis", unit="°C", series_name="Dew Point",
        ),
        use_container_width=True,
    )


def compute_annual_trend_stats(df: pd.DataFrame) -> dict:
    """Pure KPI calculations for the Annual Trend tab (no Streamlit calls)."""
    rh_min = df["relative_humidity"].min()
    rh_max = df["relative_humidity"].max()
    rh_avg = df["relative_humidity"].mean()

    comfort_hrs   = len(df[(df["relative_humidity"] >= 40) & (df["relative_humidity"] <= 60)])
    comfort_pct   = comfort_hrs / len(df) * 100
    high_rh_hrs   = len(df[df["relative_humidity"] > 60])
    # Honest condensation proxy: dew point within 2 °C of dry-bulb temperature
    cond_risk_hrs = int(_condensation_prone_mask(df).sum())
    low_rh_hrs    = len(df[df["relative_humidity"] < 30])
    # Sustained-humidity mold proxy: hours in runs of >= 24 consecutive hours > 70% RH
    mold_risk_hrs = _sustained_high_rh_hours(df)
    over_humid_hrs = len(df[df["relative_humidity"] > 70])
    mean_dew_point = df["dew_point_temperature"].mean()

    return {
        "rh_min": float(rh_min),
        "rh_max": float(rh_max),
        "rh_avg": float(rh_avg),
        "comfort_pct": float(comfort_pct),
        "high_rh_hrs": int(high_rh_hrs),
        "cond_risk_hrs": int(cond_risk_hrs),
        "low_rh_hrs": int(low_rh_hrs),
        "mold_risk_hrs": int(mold_risk_hrs),
        "over_humid_hrs": int(over_humid_hrs),
        "mean_dew_point": float(mean_dew_point),
    }


def build_annual_trend_figure(daily_stats: pd.DataFrame) -> go.Figure:
    """Annual relative-humidity profile: daily min/max band, average line and comfort band."""
    fig = go.Figure()

    # Comfort band (30–65%)
    fig.add_trace(go.Scatter(x=daily_stats["datetime_display"], y=[65] * len(daily_stats),
                             fill=None, mode="lines", line_color="rgba(128,128,128,0)",
                             showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=daily_stats["datetime_display"], y=[30] * len(daily_stats),
                             fill="tonexty", mode="lines", line_color="rgba(128,128,128,0)",
                             name="Humidity comfort band",
                             fillcolor="rgba(128,128,128,0.2)", hoverinfo="skip"))

    # RH range
    fig.add_trace(go.Scatter(x=daily_stats["datetime_display"], y=daily_stats["rh_max"],
                             fill=None, mode="lines", line_color="rgba(0,0,255,0)",
                             showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=daily_stats["datetime_display"], y=daily_stats["rh_min"],
                             fill="tonexty", mode="lines", line_color="rgba(0,0,255,0)",
                             name="Relative humidity Range",
                             fillcolor="rgba(0,150,255,0.3)",
                             hovertemplate="<b>%{x}</b><br>Min: %{y:.1f}%<extra></extra>"))

    # Average
    fig.add_trace(go.Scatter(x=daily_stats["datetime_display"], y=daily_stats["rh_avg"],
                             mode="lines", name="Average Relative humidity",
                             line=dict(color="#00a8ff", width=2),
                             hovertemplate="<b>%{x}</b><br>Avg: %{y:.1f}%<extra></extra>"))

    fig.update_layout(
        title="Annual Profile – Relative Humidity",
        xaxis_title="Day", yaxis_title="Relative Humidity (%)",
        hovermode="x unified", showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        xaxis_rangeslider_visible=False, height=450, template="plotly_white", margin=dict(b=80),
    )
    return fig


def _render_annual_trend(df, daily_stats):
    fig = build_annual_trend_figure(daily_stats)
    st.plotly_chart(fig, use_container_width=True)

    # ── KPI cards ─────────────────────────────────────────────────────────────
    stats = compute_annual_trend_stats(df)

    def _card(label, value, sub, color):
        return f"""
<div style="background:white;padding:16px;border-radius:8px;border-left:4px solid {color};
            box-shadow:0 2px 4px rgba(0,0,0,0.08);text-align:center;">
  <div style="font-size:11px;font-weight:700;color:{color};text-transform:uppercase;letter-spacing:0.5px;">{label}</div>
  <div style="font-size:26px;font-weight:700;color:#2c3e50;margin:8px 0;">{value}</div>
  <div style="font-size:11px;color:#718096;">{sub}</div>
</div>"""

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1: st.markdown(_card("Comfort 40-60%",   f"{stats['comfort_pct']:.0f} %",    "Occupied RH Hrs",          "#f59e0b"), unsafe_allow_html=True)
    with c2: st.markdown(_card("Peak RH (Occupied)", f"{stats['rh_max']:.1f} %",       "All year",                 "#ef4444"), unsafe_allow_html=True)
    with c3: st.markdown(_card("High Humidity Hrs", f"{stats['high_rh_hrs']}",         "> 60% RH",                 "#8b5cf6"), unsafe_allow_html=True)
    with c4: st.markdown(_card("Condensation-Prone Hrs", f"{stats['cond_risk_hrs']}",  "DPT within 2°C of DBT",    "#06b6d4"), unsafe_allow_html=True)
    with c5: st.markdown(_card("Avg RH",            f"{stats['rh_avg']:.1f} %",        "",                         "#3b82f6"), unsafe_allow_html=True)

    c6, c7, c8, c9, c10 = st.columns(5)
    with c6:  st.markdown(_card("Low Humidity Hrs",    f"{stats['low_rh_hrs']}",        "< 30% RH",                 "#f59e0b"), unsafe_allow_html=True)
    with c7:  st.markdown(_card("Sustained RH>70% (24h+ runs)", f"{stats['mold_risk_hrs']}", "Mold germination proxy", "#ef4444"), unsafe_allow_html=True)
    with c8:  st.markdown(_card("Mean Dew Point",      f"{stats['mean_dew_point']:.1f} °C", "Annual average",       "#06b6d4"), unsafe_allow_html=True)
    with c9:  st.markdown(_card("Overhumidification",  f"{stats['over_humid_hrs']}",    "System Failure Indicator", "#3b82f6"), unsafe_allow_html=True)
    with c10: st.markdown(_card("Min RH",              f"{stats['rh_min']:.1f} %",      "",                         "#0891b2"), unsafe_allow_html=True)


def compute_monthly_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Monthly min/max/avg relative-humidity aggregates, with month names."""
    monthly = df.groupby("month").agg(
        rh_min=("relative_humidity", "min"),
        rh_max=("relative_humidity", "max"),
        rh_avg=("relative_humidity", "mean"),
    ).reset_index()

    month_lbl = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
    monthly["month_name"] = monthly["month"].apply(lambda x: month_lbl[x - 1])
    return monthly


def build_monthly_trend_figure(df: pd.DataFrame, start_date, end_date) -> go.Figure:
    """Monthly RH trend: min/max band, average line and comfort band, greyed outside range."""
    monthly = compute_monthly_summary(df)

    start_month = start_date.month
    end_month   = end_date.month

    fig = go.Figure()

    def _grey_band(data):
        fig.add_trace(go.Scatter(x=data["month_name"], y=[65] * len(data),
                                 fill=None, mode="lines", line_color="rgba(100,100,100,0)",
                                 showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=data["month_name"], y=[30] * len(data),
                                 fill="tonexty", mode="lines", line_color="rgba(100,100,100,0)",
                                 fillcolor="rgba(180,180,180,0.15)", showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=data["month_name"], y=data["rh_max"],
                                 fill=None, mode="lines", line_color="rgba(100,100,100,0)",
                                 showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=data["month_name"], y=data["rh_min"],
                                 fill="tonexty", mode="lines", line_color="rgba(100,100,100,0)",
                                 fillcolor="rgba(180,180,180,0.15)", showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=data["month_name"], y=data["rh_avg"],
                                 mode="lines", line=dict(color="rgba(150,150,150,0.4)", width=1, dash="dot"),
                                 showlegend=False, hoverinfo="skip"))

    before = monthly[monthly["month"] < start_month]
    after  = monthly[monthly["month"] > end_month]
    active = monthly[(monthly["month"] >= start_month) & (monthly["month"] <= end_month)]

    if not before.empty:
        fig.add_trace(go.Scatter(x=before["month_name"], y=[65] * len(before),
                                 fill=None, mode="lines", line_color="rgba(100,100,100,0)",
                                 showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=before["month_name"], y=[30] * len(before),
                                 fill="tonexty", mode="lines", line_color="rgba(100,100,100,0)",
                                 fillcolor="rgba(180,180,180,0.15)", name="Unselected Period",
                                 showlegend=True, hoverinfo="skip"))
        _grey_band(before)

    # Active: comfort band
    fig.add_trace(go.Scatter(x=active["month_name"], y=[65] * len(active),
                             fill=None, mode="lines", line_color="rgba(128,128,128,0)",
                             showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=active["month_name"], y=[30] * len(active),
                             fill="tonexty", mode="lines", line_color="rgba(128,128,128,0)",
                             name="Humidity comfort band (30-65%)",
                             fillcolor="rgba(128,128,128,0.2)", hoverinfo="skip"))

    fig.add_trace(go.Scatter(x=active["month_name"], y=active["rh_max"],
                             fill=None, mode="lines", line_color="rgba(0,0,255,0)",
                             showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=active["month_name"], y=active["rh_min"],
                             fill="tonexty", mode="lines", line_color="rgba(0,0,255,0)",
                             name="Monthly Humidity Range", fillcolor="rgba(0,150,255,0.3)",
                             customdata=active["rh_max"],
                             hovertemplate="<b>%{x}</b><br>Min: %{y:.1f}%<br>Max: %{customdata:.1f}%<extra></extra>"))
    fig.add_trace(go.Scatter(x=active["month_name"], y=active["rh_avg"],
                             mode="lines+markers", name="Monthly Average Humidity",
                             line=dict(color="#00a8ff", width=2), marker=dict(size=8),
                             hovertemplate="<b>%{x}</b><br>Avg: %{y:.1f}%<extra></extra>"))

    if not after.empty:
        _grey_band(after)

    fig.update_layout(
        title="Monthly Relative Humidity Trend",
        xaxis_title="Month", yaxis_title="Relative Humidity (%)",
        hovermode="x unified", showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        height=450, template="plotly_white", margin=dict(b=80),
    )
    return fig


def _render_monthly_trend(df, start_date, end_date):
    fig = build_monthly_trend_figure(df, start_date, end_date)
    st.plotly_chart(fig, use_container_width=True)

    monthly = compute_monthly_summary(df)
    st.markdown("#### Monthly Humidity Summary")
    kpi = monthly[["month_name", "rh_min", "rh_max", "rh_avg"]].copy()
    kpi.columns = ["Month", "Min (%)", "Max (%)", "Avg (%)"]
    st.dataframe(kpi, use_container_width=True, hide_index=True,
                 column_config={
                     "Min (%)": st.column_config.NumberColumn(format="%.1f"),
                     "Max (%)": st.column_config.NumberColumn(format="%.1f"),
                     "Avg (%)": st.column_config.NumberColumn(format="%.1f"),
                 })


def build_diurnal_profile_figure(df: pd.DataFrame, start_hour: int, end_hour: int) -> go.Figure:
    """Diurnal RH profile: hourly min/max band, average line and comfort band."""
    hourly = df.groupby(["month", "hour"]).agg(
        rh_min=("relative_humidity", "min"),
        rh_max=("relative_humidity", "max"),
        rh_avg=("relative_humidity", "mean"),
    ).reset_index()

    avg = hourly.groupby("hour").agg(
        rh_min=("rh_min", "min"),
        rh_max=("rh_max", "max"),
        rh_avg=("rh_avg", "mean"),
    ).reset_index()

    fig = go.Figure()

    def _grey_rh(data):
        fig.add_trace(go.Scatter(x=data["hour"], y=[65] * len(data),
                                 fill=None, mode="lines", line_color="rgba(100,100,100,0)",
                                 showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=data["hour"], y=[30] * len(data),
                                 fill="tonexty", mode="lines", line_color="rgba(100,100,100,0)",
                                 fillcolor="rgba(180,180,180,0.15)", showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=data["hour"], y=data["rh_max"],
                                 fill=None, mode="lines", line_color="rgba(100,100,100,0)",
                                 showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=data["hour"], y=data["rh_min"],
                                 fill="tonexty", mode="lines", line_color="rgba(100,100,100,0)",
                                 fillcolor="rgba(180,180,180,0.15)", showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=data["hour"], y=data["rh_avg"],
                                 mode="lines+markers",
                                 line=dict(color="rgba(150,150,150,0.4)", width=1, dash="dot"),
                                 marker=dict(size=4), showlegend=False, hoverinfo="skip"))

    if start_hour > 0:
        before = avg[avg["hour"] < start_hour]
        fig.add_trace(go.Scatter(x=before["hour"], y=[65] * len(before),
                                 fill=None, mode="lines", line_color="rgba(100,100,100,0)",
                                 showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=before["hour"], y=[30] * len(before),
                                 fill="tonexty", mode="lines", line_color="rgba(100,100,100,0)",
                                 fillcolor="rgba(180,180,180,0.15)",
                                 name="Unselected Hours", showlegend=True, hoverinfo="skip"))
        _grey_rh(before)

    active = avg[(avg["hour"] >= start_hour) & (avg["hour"] <= end_hour)]

    fig.add_trace(go.Scatter(x=active["hour"], y=[65] * len(active),
                             fill=None, mode="lines", line_color="rgba(128,128,128,0)",
                             showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=active["hour"], y=[30] * len(active),
                             fill="tonexty", mode="lines", line_color="rgba(128,128,128,0)",
                             name="Comfort band (30-65%)",
                             fillcolor="rgba(128,128,128,0.2)", hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=active["hour"], y=active["rh_max"],
                             fill=None, mode="lines", line_color="rgba(0,0,255,0)",
                             showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=active["hour"], y=active["rh_min"],
                             fill="tonexty", mode="lines", line_color="rgba(0,0,255,0)",
                             name="Humidity Range", fillcolor="rgba(0,150,255,0.3)",
                             customdata=active["rh_max"],
                             hovertemplate="<b>Hour %{x}:00</b><br>Min: %{y:.1f}%<br>Max: %{customdata:.1f}%<extra></extra>"))
    fig.add_trace(go.Scatter(x=active["hour"], y=active["rh_avg"],
                             mode="lines+markers", name="Average Humidity",
                             line=dict(color="#00a8ff", width=2), marker=dict(size=6),
                             hovertemplate="<b>Hour %{x}:00</b><br>Avg: %{y:.1f}%<extra></extra>"))

    if end_hour < 23:
        after = avg[avg["hour"] > end_hour]
        _grey_rh(after)

    fig.update_layout(
        title="Diurnal Humidity Profile",
        xaxis_title="Hour of Day", yaxis_title="Relative Humidity (%)",
        hovermode="x unified", showlegend=True,
        template="plotly_white", height=450,
    )
    return fig


def _render_diurnal_profile(df, start_hour, end_hour):
    st.plotly_chart(build_diurnal_profile_figure(df, start_hour, end_hour), use_container_width=True)


def build_comfort_analysis_figure(daily_stats: pd.DataFrame, start_date, end_date) -> go.Figure:
    """Humidity comfort analysis: 40-60% comfort band vs daily RH range."""
    start_month_num = start_date.month
    end_month_num   = end_date.month

    start_doy = pd.to_datetime(f"2024-{start_month_num:02d}-01").dayofyear
    end_doy   = (
        366
        if end_month_num == 12
        else pd.to_datetime(f"2024-{end_month_num + 1:02d}-01").dayofyear - 1
    )

    fig = go.Figure()

    if start_doy > 1:
        before = daily_stats[daily_stats["doy"] < start_doy]
        fig.add_trace(go.Scatter(x=before["datetime_display"], y=before["rh_max"],
                                 fill=None, mode="lines", line_color="rgba(100,100,100,0)",
                                 showlegend=False, hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=before["datetime_display"], y=before["rh_min"],
                                 fill="tonexty", mode="lines", line_color="rgba(100,100,100,0)",
                                 fillcolor="rgba(180,180,180,0.15)",
                                 name="Unselected Period", showlegend=True, hoverinfo="skip"))
        fig.add_trace(go.Scatter(x=before["datetime_display"], y=before["rh_avg"],
                                 mode="lines",
                                 line=dict(color="rgba(150,150,150,0.4)", width=1, dash="dot"),
                                 showlegend=False, hoverinfo="skip"))

    active = daily_stats[(daily_stats["doy"] >= start_doy) & (daily_stats["doy"] <= end_doy)]

    # Comfort band 40–60%
    fig.add_trace(go.Scatter(x=active["datetime_display"], y=[60] * len(active),
                             fill=None, mode="lines", line_color="rgba(128,128,128,0)",
                             showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=active["datetime_display"], y=[40] * len(active),
                             fill="tonexty", mode="lines", line_color="rgba(128,128,128,0)",
                             name="Comfort Band (40-60%)",
                             fillcolor="rgba(76,175,80,0.4)", hoverinfo="skip"))

    fig.add_trace(go.Scatter(x=active["datetime_display"], y=active["rh_max"],
                             fill=None, mode="lines", line_color="rgba(0,150,255,0)",
                             showlegend=False, hoverinfo="skip"))
    fig.add_trace(go.Scatter(x=active["datetime_display"], y=active["rh_min"],
                             fill="tonexty", mode="lines", line_color="rgba(0,150,255,0)",
                             name="Daily RH Range", fillcolor="rgba(0,150,255,0.3)",
                             customdata=active["rh_max"],
                             hovertemplate="<b>%{x}</b><br>Min: %{y:.2f}%<br>Max: %{customdata:.2f}%<extra></extra>"))
    fig.add_trace(go.Scatter(x=active["datetime_display"], y=active["rh_avg"],
                             mode="lines", name="Average RH",
                             line=dict(color="#00a8ff", width=2),
                             hovertemplate="<b>%{x}</b><br>Avg: %{y:.2f}%<extra></extra>"))

    if end_doy < 365:
        after = daily_stats[daily_stats["doy"] > end_doy]
        if not after.empty:
            fig.add_trace(go.Scatter(x=after["datetime_display"], y=after["rh_max"],
                                     fill=None, mode="lines", line_color="rgba(100,100,100,0)",
                                     showlegend=False, hoverinfo="skip"))
            fig.add_trace(go.Scatter(x=after["datetime_display"], y=after["rh_min"],
                                     fill="tonexty", mode="lines", line_color="rgba(100,100,100,0)",
                                     fillcolor="rgba(180,180,180,0.15)", showlegend=False, hoverinfo="skip"))
            fig.add_trace(go.Scatter(x=after["datetime_display"], y=after["rh_avg"],
                                     mode="lines",
                                     line=dict(color="rgba(150,150,150,0.4)", width=1, dash="dot"),
                                     showlegend=False, hoverinfo="skip"))

    fig.update_layout(
        title="Humidity Comfort Analysis – Optimal Range (40-60%)",
        xaxis_title="Day", yaxis_title="Relative Humidity (%)",
        hovermode="x unified", showlegend=True,
        template="plotly_white", height=450,
    )
    return fig


def _render_comfort_analysis(df, daily_stats, start_date, end_date):
    st.plotly_chart(build_comfort_analysis_figure(daily_stats, start_date, end_date),
                    use_container_width=True)


def compute_energy_metrics_stats(
    df: pd.DataFrame, start_date, end_date, start_hour: int, end_hour: int
) -> dict:
    """Pure KPI calculations for the Energy Metrics tab (no Streamlit calls)."""
    filtered = df[
        (df["datetime"].dt.date >= start_date) &
        (df["datetime"].dt.date <= end_date) &
        (df["hour"].between(start_hour, end_hour))
    ]

    high_rh_annual    = len(df[df["relative_humidity"] > 60])
    # Honest condensation proxy: dew point within 2 °C of dry-bulb temperature
    cond_risk_annual   = int(_condensation_prone_mask(df).sum())
    high_rh_filtered  = len(filtered[filtered["relative_humidity"] > 60])
    cond_risk_filtered = int(_condensation_prone_mask(filtered).sum()) if not filtered.empty else 0

    return {
        "high_rh_annual": int(high_rh_annual),
        "cond_risk_annual": int(cond_risk_annual),
        "high_rh_period": int(high_rh_filtered),
        "cond_risk_period": int(cond_risk_filtered),
        "period_hours": int(len(filtered)),
    }


def build_energy_metrics_figure(df: pd.DataFrame) -> go.Figure:
    """Monthly humidity risk distribution: high-RH / condensation bars and low-RH line."""
    monthly_high  = df.groupby("month").apply(lambda x: len(x[x["relative_humidity"] > 60]))
    monthly_cond  = df.groupby("month").apply(lambda x: int(_condensation_prone_mask(x).sum()))
    monthly_low   = df.groupby("month").apply(lambda x: len(x[x["relative_humidity"] < 30]))

    month_names = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]

    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(x=month_names, y=monthly_high.values,
                         name="High RH (>60%)", marker_color="#0099ff"), secondary_y=False)
    fig.add_trace(go.Bar(x=month_names, y=monthly_cond.values,
                         name="Condensation-prone (DPT within 2°C of DBT)", marker_color="#FF6B6B"), secondary_y=False)
    fig.add_trace(go.Scatter(x=month_names, y=monthly_low.values,
                             name="Low RH (<30%)", line=dict(color="#FFA500", width=2),
                             mode="lines+markers"), secondary_y=False)
    fig.update_layout(
        title="Monthly Humidity Risk Distribution",
        xaxis_title="Month", yaxis_title="Hours",
        hovermode="x unified", height=400, barmode="group",
    )
    return fig


def _render_energy_metrics(df, start_date, end_date, start_hour, end_hour):
    stats = compute_energy_metrics_stats(df, start_date, end_date, start_hour, end_hour)

    if stats["period_hours"] == 0:
        st.info("No data in the selected date/hour range.")
        return

    st.markdown("#### Humidity Performance Indicators")
    c1, c2, c3, c4 = st.columns(4)
    with c1: st.metric("High RH Hrs (Annual)",           f"{stats['high_rh_annual']}",   ">60% RH")
    with c2: st.metric("Condensation-Prone (Annual)",    f"{stats['cond_risk_annual']}", "DPT within 2°C of DBT")
    with c3: st.metric("High RH Hrs (Period)",           f"{stats['high_rh_period']}",   ">60% RH")
    with c4: st.metric("Condensation-Prone (Period)",    f"{stats['cond_risk_period']}", "DPT within 2°C of DBT")

    st.plotly_chart(build_energy_metrics_figure(df), use_container_width=True)
