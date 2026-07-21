"""Chart-data API: serves the interactive analysis modules' Plotly figures as JSON.

Included by report_api.py (`app.include_router(router)`). Each endpoint mirrors
the orchestration its module's Streamlit ``render()`` performs — same compute
pipeline, same figure builders — and returns::

    {
      "module": "<name>",
      "charts": [{"id": "...", "title": "...", "figure": {plotly data/layout}}],
      "stats":  {KPI numbers the Streamlit cards show}
    }

Figures serialize via ``fig.to_json()`` (plotly's encoder handles numpy/datetime),
so the client renders them directly with ``Plotly.newPlot(div, figure.data,
figure.layout)``.

EPW files are re-sent with every request (the client keeps the blob); a
content-hash TTL cache makes repeat parses free, so module switching stays
cheap and the service stays stateless.
"""

import asyncio
import hashlib
import json
from functools import partial
from typing import Optional

import numpy as np
import pandas as pd
from cachetools import TTLCache
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from pages.modules.epw_parser import parse_epw
from pages.modules import (
    dbt_module,
    humidity_module,
    psychro_module,
    rainfall_module,
    shading_designer_module,
    site_analysis_module,
    solar_pv_module,
    sun_path,
    sun_path_3d,
    thermal_comfort_module,
    utci_module,
    ventilation_module,
    wind_module,
)

router = APIRouter(prefix="/api/charts", tags=["charts"])

_VALID_SECTORS = [4, 8, 16]

# Parsed-EPW cache: sha256(file bytes) -> (df, metadata). 30 min, 32 files.
_epw_cache: TTLCache = TTLCache(maxsize=32, ttl=1800)


# ── Shared helpers ────────────────────────────────────────────────────────────

def _http_400(msg: str) -> HTTPException:
    return HTTPException(status_code=400, detail={"error": "validation_error", "detail": msg})


async def _parsed_epw(file: UploadFile) -> tuple[pd.DataFrame, dict]:
    """Read, parse (cached by content hash), and validate an uploaded EPW."""
    content = await file.read()
    if not content:
        raise _http_400("Uploaded EPW file is empty.")
    key = hashlib.sha256(content).hexdigest()
    cached = _epw_cache.get(key)
    if cached is not None:
        return cached
    loop = asyncio.get_event_loop()
    try:
        df, metadata = await loop.run_in_executor(
            None, parse_epw, content.decode("utf-8", errors="replace")
        )
    except Exception as e:
        raise _http_400(f"Could not parse EPW file: {e}")
    if df.empty:
        raise _http_400("EPW file parsed to an empty dataset.")
    _epw_cache[key] = (df, metadata)
    return df, metadata


def _months_list(start_month: int, end_month: int) -> list[int]:
    if not (1 <= start_month <= 12 and 1 <= end_month <= 12):
        raise _http_400("start_month and end_month must be in 1..12.")
    if start_month <= end_month:
        return list(range(start_month, end_month + 1))
    # wrap-around range (e.g. Nov..Feb)
    return list(range(start_month, 13)) + list(range(1, end_month + 1))


def _fig_json(fig) -> dict:
    return json.loads(fig.to_json())


def _clean(obj):
    """Make stats JSON-safe (numpy scalars, pandas objects, NaN)."""
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return None if (np.isnan(f) or np.isinf(f)) else f
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    if isinstance(obj, pd.DataFrame):
        return _clean(obj.to_dict(orient="records"))
    if isinstance(obj, pd.Series):
        return _clean(obj.to_dict())
    return obj


def _chart(chart_id: str, title: str, fig) -> dict:
    return {"id": chart_id, "title": title, "figure": _fig_json(fig)}


def _response(module: str, charts: list[dict], stats: dict) -> dict:
    return {"module": module, "charts": charts, "stats": _clean(stats)}


async def _run(worker, *args) -> dict:
    """Run a synchronous compute+figure worker off the event loop."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, partial(worker, *args))


def _require_columns(df: pd.DataFrame, required: set[str], module: str) -> None:
    missing = required - set(df.columns)
    if missing:
        raise _http_400(
            f"EPW data is missing columns required for {module}: {', '.join(sorted(missing))}."
        )


def _daily_stats(df: pd.DataFrame) -> pd.DataFrame:
    """Daily min/max/avg + ASHRAE comfort bands, as pages/analysis.py builds it."""
    daily = df.groupby("doy").agg(
        temp_min=("dry_bulb_temperature", "min"),
        temp_max=("dry_bulb_temperature", "max"),
        temp_avg=("dry_bulb_temperature", "mean"),
        rh_min=("relative_humidity", "min"),
        rh_max=("relative_humidity", "max"),
        rh_avg=("relative_humidity", "mean"),
    ).reset_index()
    year = df["datetime"].dt.year.iloc[0] if not df.empty else 2024
    daily["datetime"] = pd.to_datetime(
        daily["doy"].astype(str) + f"-{year}", format="%j-%Y", errors="coerce")
    daily["datetime_display"] = daily["datetime"].dt.strftime("%b %d")
    c80lo, c80hi, c90lo, c90hi = dbt_module.calculate_ashrae_comfort(df)
    comfort = pd.DataFrame({
        "doy": c80lo.index,
        "comfort_80_lower": c80lo.values, "comfort_80_upper": c80hi.values,
        "comfort_90_lower": c90lo.values, "comfort_90_upper": c90hi.values,
    })
    return daily.merge(comfort, on="doy", how="left")


def _dates_from_months(df: pd.DataFrame, start_month: int, end_month: int):
    """Derive (start_date, end_date) in the EPW's year from a month range."""
    year = int(df["datetime"].dt.year.iloc[0]) if not df.empty else 2024
    start = pd.Timestamp(year=year, month=start_month, day=1).date()
    end_ts = (pd.Timestamp(year=year, month=end_month, day=1)
              + pd.offsets.MonthEnd(0))
    return start, end_ts.date()


# ── Module registry (drives the PHP param UI; self-documenting) ──────────────

def _registry() -> dict:
    return {
        "dbt": {
            "title": "Dry Bulb Temperature",
            "requires_epw": True,
            "params": {
                "start_month": {"type": "int", "default": 1},
                "end_month": {"type": "int", "default": 12},
                "start_hour": {"type": "int", "default": 0},
                "end_hour": {"type": "int", "default": 23},
            },
        },
        "humidity": {
            "title": "Relative Humidity",
            "requires_epw": True,
            "params": {
                "start_month": {"type": "int", "default": 1},
                "end_month": {"type": "int", "default": 12},
                "start_hour": {"type": "int", "default": 0},
                "end_hour": {"type": "int", "default": 23},
            },
        },
        "utci": {
            "title": "Outdoor Thermal Stress (UTCI)",
            "requires_epw": True,
            "params": {
                "start_month": {"type": "int", "default": 1},
                "end_month": {"type": "int", "default": 12},
            },
        },
        "thermal-comfort": {
            "title": "Thermal Comfort & Passive Strategies",
            "requires_epw": True,
            "params": {
                "start_month": {"type": "int", "default": 1},
                "end_month": {"type": "int", "default": 12},
                "start_hour": {"type": "int", "default": 0},
                "end_hour": {"type": "int", "default": 23},
                "air_speed_adjust": {"type": "bool", "default": False},
            },
        },
        "sun-path": {
            "title": "Sun Path & Shading Analysis",
            "requires_epw": True,
            "params": {
                "temp_threshold": {"type": "float", "default": 28.0},
                "rad_threshold": {"type": "float", "default": 315.0},
                "design_cutoff_angle": {"type": "float", "default": 45.0},
            },
        },
        "rainfall": {
            "title": "Rainfall Analysis",
            "requires_epw": False,
            "params": {
                "station_name": {"type": "str", "default": None,
                                 "options": list(rainfall_module.STATIONS.keys())},
                "year": {"type": "int", "default": 2023},
                "start_month": {"type": "int", "default": 1},
                "end_month": {"type": "int", "default": 12},
                "heavy_rain_threshold": {"type": "float", "default": 50.0},
                "roof_area_m2": {"type": "float", "default": 0.0},
                "paved_area_m2": {"type": "float", "default": 0.0},
                "green_area_m2": {"type": "float", "default": 0.0},
                "water_area_m2": {"type": "float", "default": 0.0},
                "gi_percentile": {"type": "int", "default": 95,
                                  "options": [85, 90, 95, 98]},
                "gi_start_year": {"type": "int", "default": 1990},
            },
        },
        "wind": {
            "title": "Wind Analysis",
            "requires_epw": True,
            "params": {
                "start_month": {"type": "int", "default": 1},
                "end_month": {"type": "int", "default": 12},
                "n_sectors": {"type": "int", "default": 16, "options": _VALID_SECTORS},
                "exclude_calm": {"type": "bool", "default": False},
            },
        },
        "ventilation": {
            "title": "Natural Ventilation Potential",
            "requires_epw": True,
            "params": {
                "start_month": {"type": "int", "default": 1},
                "end_month": {"type": "int", "default": 12},
                "wind_threshold": {"type": "float", "default": 1.5},
                "comfort_min": {"type": "float", "default": 24.0},
                "comfort_max": {"type": "float", "default": 26.0},
            },
        },
        "psychrometric": {
            "title": "Psychrometric & Bioclimatic Analysis",
            "requires_epw": True,
            "params": {
                "start_month": {"type": "int", "default": 1},
                "end_month": {"type": "int", "default": 12},
                "color_mode": {
                    "type": "str", "default": "Month",
                    "options": ["Month", "Strategy", "Temperature",
                                "Relative Humidity", "Frequency"],
                },
                "zones": {
                    "type": "str", "default":
                        "Comfort (still air),Natural Ventilation,Evaporative Cooling",
                    "note": "comma-separated strategy zone names",
                },
                "show_wetbulb": {"type": "bool", "default": True},
                "show_enthalpy": {"type": "bool", "default": False},
                "animate": {"type": "bool", "default": False},
            },
        },
        "sun-path-3d": {
            "title": "Interactive 3D Sun Path",
            "requires_epw": True,
            "params": {
                "color_by": {"type": "str",
                             "default": list(sun_path_3d._COLOR_OPTIONS.keys())[0],
                             "options": list(sun_path_3d._COLOR_OPTIONS.keys())},
                "day_label": {"type": "str", "default": "Jun 21"},
            },
        },
        "site-analysis": {
            "title": "Site Analysis — massing, shadows & solar exposure",
            "requires_epw": True,
            "params": {
                "length": {"type": "float", "default": 30.0},
                "width": {"type": "float", "default": 15.0},
                "height": {"type": "float", "default": 12.0},
                "rotation": {"type": "float", "default": 0.0},
                "day_label": {"type": "str", "default": "Dec 21",
                              "options": list(site_analysis_module.DESIGN_DAYS.keys())},
                "neighbor_enabled": {"type": "bool", "default": False},
                "neighbor_distance": {"type": "float", "default": 25.0},
                "neighbor_bearing": {"type": "float", "default": 180.0},
                "neighbor_height": {"type": "float", "default": 24.0},
            },
        },
        "shading-designer": {
            "title": "Window Shading Designer",
            "requires_epw": True,
            "params": {
                "orientation": {"type": "str",
                                "default": list(shading_designer_module.ORIENTATIONS.keys())[4],
                                "options": list(shading_designer_module.ORIENTATIONS.keys())},
                "window_width": {"type": "float", "default": 2.4},
                "window_height": {"type": "float", "default": 1.5},
                "overhang_depth": {"type": "float", "default": 0.6},
                "overhang_gap": {"type": "float", "default": 0.1},
                "overhang_extension": {"type": "float", "default": 0.3},
                "fin_left": {"type": "float", "default": 0.0},
                "fin_right": {"type": "float", "default": 0.0},
                "day_label": {"type": "str", "default": "Jun 21"},
            },
        },
        "solar-pv": {
            "title": "Solar PV Potential",
            "requires_epw": False,
            "params": {
                "country": {"type": "str", "default": "United Arab Emirates"},
                "roof_size_m2": {"type": "float", "default": 100.0},
                "roof_pct": {"type": "float", "default": 80.0},
            },
        },
    }


@router.get("/modules")
def list_modules():
    """List available chart modules with their parameters and defaults."""
    return _registry()


# ── Wind ──────────────────────────────────────────────────────────────────────

def _wind_worker(df: pd.DataFrame, months: list[int], n_sectors: int,
                 exclude_calm: bool) -> dict:
    wdf = wind_module.prepare_wind_data(df, months=months, n_sectors=n_sectors)
    if wdf.empty:
        raise _http_400("No wind data available for the selected month range.")
    rose_df, calm_pct = wind_module.compute_wind_rose(
        wdf, n_sectors=n_sectors, exclude_calm=exclude_calm)
    stats = wind_module.compute_wind_statistics(wdf)
    charts = [
        _chart("wind_rose", "Annual Wind Rose",
               wind_module.plot_wind_rose(rose_df, calm_pct, n_sectors)),
        _chart("seasonal_roses", "Seasonal Wind Roses",
               wind_module.plot_seasonal_wind_roses(wdf, n_sectors)),
        _chart("animated_rose", "Monthly Animation",
               wind_module.plot_animated_wind_rose(
                   wdf, n_sectors=n_sectors, exclude_calm=exclude_calm)),
        _chart("rose_3d", "3D Rose Tower",
               wind_module.plot_wind_rose_3d(wdf, n_sectors=n_sectors)),
        _chart("comfort_rose", "Comfort Winds",
               wind_module.plot_comfort_wind_rose(wdf, n_sectors=n_sectors)),
        _chart("speed_heatmap", "Wind Speed Heatmap",
               wind_module.plot_speed_heatmap(wdf)),
        _chart("direction_heatmap", "Wind Direction Heatmap",
               wind_module.plot_direction_heatmap(wdf)),
        _chart("speed_histogram", "Speed Distribution",
               wind_module.plot_speed_histogram(wdf)),
        _chart("climate_bubble", "Climate Bubble",
               wind_module.plot_climate_bubble(wdf)),
    ]
    return _response("wind", charts, stats)


@router.post("/wind")
async def wind_charts(
    file: UploadFile = File(...),
    start_month: int = Form(1),
    end_month: int = Form(12),
    n_sectors: int = Form(16),
    exclude_calm: bool = Form(False),
):
    if n_sectors not in _VALID_SECTORS:
        raise _http_400(f"n_sectors must be one of {_VALID_SECTORS}. Got: {n_sectors}")
    df, _meta = await _parsed_epw(file)
    _require_columns(df, {"wind_speed", "wind_direction"}, "wind analysis")
    months = _months_list(start_month, end_month)
    return await _run(_wind_worker, df, months, n_sectors, exclude_calm)


# ── Ventilation ───────────────────────────────────────────────────────────────

def _ventilation_worker(df: pd.DataFrame, months: list[int], wind_threshold: float,
                        comfort_min: float, comfort_max: float) -> dict:
    m = ventilation_module
    vdf = m.prepare_ventilation_data(df, months=months)
    if vdf.empty:
        raise _http_400("No data available for the selected month range.")
    opening_factor = m._ACH_OPENING
    effectiveness = m._ACH_EFFCY
    cross = m.compute_cross_ventilation(vdf, wind_threshold)
    stack = m.compute_stack_ventilation(vdf, comfort_min, comfort_max)
    night = m.compute_night_flushing(
        vdf, hot_day_threshold=30.0, night_cool_threshold=comfort_max)
    ach = m.compute_ach(vdf, opening_factor, effectiveness)
    orient = m.generate_orientation_recommendation(cross["prevailing_dir"], cross)

    flags = m._build_usability_flags(
        vdf, wind_threshold, comfort_max,
        hot_day_threshold=30.0, night_cool_threshold=comfort_max)
    total_vent_pct = float(flags["any_vent"].mean()) * 100.0
    calm_pct = float(vdf["is_calm"].sum()) / len(vdf) * 100.0

    stats = {
        "total_potential_pct": total_vent_pct,
        "cross_hours_pct": cross["cross_hours_pct"],
        "stack_hours_pct": stack["stack_hours_pct"],
        "night_flush_pct": night["night_flush_pct"],
        "mean_ach": ach["mean_ach"],
        "ach_category": ach["category"],
        "prevailing_dir": cross["prevailing_dir"],
        "best_pair_label": cross["best_pair_label"],
        "stack_strength": stack["strength"],
        "mean_diurnal_range": night["mean_diurnal_range"],
        "hot_days_pct": night["hot_days_pct"],
        "calm_pct": calm_pct,
        "orientation_narrative": orient["narrative"],
    }
    charts = [
        _chart("usability_heatmap", "Ventilation Usability Heatmap",
               m.plot_ventilation_heatmap(
                   vdf, wind_threshold=wind_threshold, comfort_max=comfort_max,
                   hot_day_threshold=30.0, night_cool_threshold=comfort_max)),
        _chart("monthly_strategy", "Monthly Strategy Breakdown",
               m.plot_monthly_strategy_breakdown(cross, stack, night)),
        _chart("facade_pairs", "Facade Axis Comparison",
               m.plot_facade_pair_table(cross)),
        _chart("wind_usability", "Wind Direction vs Usability",
               m.plot_wind_ventilation_usability(vdf, wind_threshold)),
        _chart("day_night_temp", "Day & Night Temperature Profile",
               m.plot_day_night_temperature(vdf, comfort_min, comfort_max)),
        _chart("ach_distribution", "Air-Change Rate Distribution",
               m.plot_ach_distribution(vdf, opening_factor, effectiveness)),
    ]
    return _response("ventilation", charts, stats)


@router.post("/ventilation")
async def ventilation_charts(
    file: UploadFile = File(...),
    start_month: int = Form(1),
    end_month: int = Form(12),
    wind_threshold: float = Form(1.5),
    comfort_min: float = Form(24.0),
    comfort_max: float = Form(26.0),
):
    df, _meta = await _parsed_epw(file)
    _require_columns(
        df, {"wind_speed", "wind_direction", "dry_bulb_temperature"},
        "ventilation analysis")
    months = _months_list(start_month, end_month)
    return await _run(_ventilation_worker, df, months,
                      wind_threshold, comfort_min, comfort_max)


# ── Psychrometric ─────────────────────────────────────────────────────────────

_PSYCHRO_ZONES = ["Comfort (still air)", "Natural Ventilation", "Evaporative Cooling",
                  "High Thermal Mass + Night Flush", "Passive Solar Heating"]
_PSYCHRO_COLOR_MODES = ["Month", "Strategy", "Temperature",
                        "Relative Humidity", "Frequency"]


def _psychro_worker(df: pd.DataFrame, months: list[int], color_mode: str,
                    zones: list[str], show_wb: bool, show_h: bool,
                    animate: bool) -> dict:
    d = df[df["month"].isin(months)] if months else df
    if d.empty:
        raise _http_400("No hours in the selected month range.")
    pdf = psychro_module.classify_strategies(d)

    bars_fig, rank = psychro_module.build_strategy_bars(pdf)
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

    stats = {
        "comfort_pct": comfort_pct,
        "passive_recoverable_pct": passive_recoverable,
        "mech_cooling_pct": mech_cool,
        "mech_heating_pct": mech_heat,
        "total_hours": total,
        "strategy_ranking": rank,
    }
    charts = [
        _chart("psychro_chart", "Psychrometric Chart",
               psychro_module.build_psychro_figure(
                   pdf, color_mode, zones, show_wb, show_h,
                   animate and color_mode == "Month")),
        _chart("strategy_bars", "Passive Strategy Effectiveness", bars_fig),
        _chart("frequency_3d", "3D Climate-Frequency Landscape",
               psychro_module.build_3d_frequency_figure(pdf)),
    ]
    return _response("psychrometric", charts, stats)


@router.post("/psychrometric")
async def psychrometric_charts(
    file: UploadFile = File(...),
    start_month: int = Form(1),
    end_month: int = Form(12),
    color_mode: str = Form("Month"),
    zones: str = Form("Comfort (still air),Natural Ventilation,Evaporative Cooling"),
    show_wetbulb: bool = Form(True),
    show_enthalpy: bool = Form(False),
    animate: bool = Form(False),
):
    if color_mode not in _PSYCHRO_COLOR_MODES:
        raise _http_400(f"color_mode must be one of {_PSYCHRO_COLOR_MODES}.")
    zone_list = [z.strip() for z in zones.split(",") if z.strip()]
    bad = [z for z in zone_list if z not in _PSYCHRO_ZONES]
    if bad:
        raise _http_400(f"Unknown strategy zones: {bad}. Valid: {_PSYCHRO_ZONES}")
    df, _meta = await _parsed_epw(file)
    _require_columns(df, {"dry_bulb_temperature", "relative_humidity"},
                     "psychrometric analysis")
    months = _months_list(start_month, end_month)
    return await _run(_psychro_worker, df, months, color_mode, zone_list,
                      show_wetbulb, show_enthalpy, animate)


# ── 3D Sun Path ───────────────────────────────────────────────────────────────

def _sun_path_3d_worker(df: pd.DataFrame, metadata: dict, color_by: str,
                        day_label: str) -> dict:
    lat = metadata.get("latitude")
    lon = metadata.get("longitude")
    if lat is None or lon is None:
        raise _http_400("Location (latitude/longitude) not found in EPW file.")
    tz_str = str(metadata.get("timezone") or "UTC")

    geom = sun_path_3d.compute_solar_geometry(float(lat), float(lon), tz_str)
    if geom["hourly"].empty:
        raise _http_400("No above-horizon solar positions found — check location/timezone.")
    points = sun_path_3d.prepare_epw_points(df, geom["hourly"])

    day_info = geom["day_info"]
    day_labels = day_info["label"].tolist()
    if day_label not in day_labels:
        day_label = "Jun 21" if "Jun 21" in day_labels else day_labels[0]
    sel = day_info[day_info["label"] == day_label]
    stats = {"day_label": day_label, "days": day_info}
    if not sel.empty:
        row = sel.iloc[0]
        stats.update({
            "sunrise": row["sunrise"], "sunset": row["sunset"],
            "day_length_h": row["day_length"], "max_altitude_deg": row["max_altitude"],
        })
    charts = [
        _chart("sun_dome", "3D Sun-Path Dome",
               sun_path_3d.build_sun_dome_figure(geom, points, color_by, day_label)),
        _chart("stereographic", "2D Stereographic Sun Path",
               sun_path_3d.build_stereographic_figure(geom, points, color_by)),
    ]
    return _response("sun-path-3d", charts, stats)


@router.post("/sun-path-3d")
async def sun_path_3d_charts(
    file: UploadFile = File(...),
    color_by: Optional[str] = Form(None),
    day_label: str = Form("Jun 21"),
):
    options = list(sun_path_3d._COLOR_OPTIONS.keys())
    color = color_by or options[0]
    if color not in options:
        raise _http_400(f"color_by must be one of {options}.")
    df, metadata = await _parsed_epw(file)
    return await _run(_sun_path_3d_worker, df, metadata, color, day_label)


# ── Site Analysis ─────────────────────────────────────────────────────────────

def _site_worker(df: pd.DataFrame, metadata: dict, length: float, width: float,
                 height: float, rotation: float, day_label: str,
                 neighbor: Optional[dict]) -> dict:
    m = site_analysis_module
    lat = float(metadata.get("latitude") or 0.0)
    lon = float(metadata.get("longitude") or 0.0)
    tz = metadata.get("timezone", "UTC")

    sun_day = m.day_solar_positions(lat, lon, tz, m.DESIGN_DAYS[day_label])
    irr = m.facade_irradiation(df, lat, lon, tz, float(rotation))
    annual = irr.groupby("Surface")["kwh_m2"].sum()
    wexp = m.facade_wind_exposure(df, float(rotation))

    stats: dict = {"annual_irradiation_kwh_m2": annual}
    if not sun_day.empty:
        noon = sun_day.loc[sun_day["altitude"].idxmax()]
        shadow_len = height / np.tan(np.radians(max(noon["altitude"], 1.0)))
        stats.update({
            "sunrise": f"{sun_day['time'].iloc[0]:%H:%M}",
            "sunset": f"{sun_day['time'].iloc[-1]:%H:%M}",
            "max_altitude_deg": float(noon["altitude"]),
            "noon_shadow_length_m": float(shadow_len),
            "daylight_hours": len(sun_day) * 20 / 60,
        })
    facades = annual.drop("Roof (flat)") if "Roof (flat)" in annual.index else annual
    stats["hottest_facade"] = str(facades.idxmax())
    stats["coolest_facade"] = str(facades.idxmin())
    best = wexp.loc[wexp["pct_hours"].idxmax()]
    stats["best_wind_facade"] = str(best["Facade"])
    stats["best_wind_pct_hours"] = float(best["pct_hours"])

    charts = [
        _chart("shadow_study", "Shadow Study",
               m.build_shadow_study_figure(length, width, height, rotation,
                                           sun_day, neighbor)),
        _chart("irradiated_massing", "Solar Exposure — 3D Massing",
               m.build_irradiated_massing_figure(length, width, height,
                                                 rotation, annual)),
        _chart("monthly_irradiation", "Monthly Irradiation by Surface",
               m.build_monthly_irradiation_figure(irr)),
        _chart("wind_exposure", "Wind Exposure by Facade",
               m.build_wind_exposure_figure(wexp)),
    ]
    return _response("site-analysis", charts, stats)


@router.post("/site-analysis")
async def site_analysis_charts(
    file: UploadFile = File(...),
    length: float = Form(30.0),
    width: float = Form(15.0),
    height: float = Form(12.0),
    rotation: float = Form(0.0),
    day_label: str = Form("Dec 21"),
    neighbor_enabled: bool = Form(False),
    neighbor_distance: float = Form(25.0),
    neighbor_bearing: float = Form(180.0),
    neighbor_height: float = Form(24.0),
):
    if day_label not in site_analysis_module.DESIGN_DAYS:
        raise _http_400(
            f"day_label must be one of {list(site_analysis_module.DESIGN_DAYS.keys())}.")
    df, metadata = await _parsed_epw(file)
    _require_columns(df, {"wind_speed", "wind_direction"}, "site analysis")
    neighbor = None
    if neighbor_enabled:
        neighbor = dict(distance=neighbor_distance, bearing=float(neighbor_bearing),
                        height=neighbor_height, length=20.0, width=15.0)
    return await _run(_site_worker, df, metadata, length, width, height,
                      rotation, day_label, neighbor)


# ── Shading Designer ──────────────────────────────────────────────────────────

def _shading_worker(df: pd.DataFrame, metadata: dict, geom: dict,
                    facade_az: float, month: int) -> dict:
    m = shading_designer_module
    lat = float(metadata.get("latitude") or 0.0)
    lon = float(metadata.get("longitude") or 0.0)
    tz = metadata.get("timezone", "UTC")

    sun_day = m.design_day_sun(lat, lon, tz, month)
    day_arcs = {mm: m.design_day_sun(lat, lon, tz, mm) for mm in (3, 6, 9, 12)}
    sun = m.hourly_sun(lat, lon, tz)
    perf = m.annual_shading_performance(df, sun, geom, facade_az)

    hot = perf[perf["dry_bulb_temperature"] >= 28.0]
    cold = perf[perf["dry_bulb_temperature"] <= 18.0]
    tot_beam = perf["beam_wm2"].sum()
    pct_all = 100.0 * perf["blocked_wm2"].sum() / tot_beam if tot_beam else 0.0
    pct_hot = (100.0 * hot["blocked_wm2"].sum() / hot["beam_wm2"].sum()
               if hot["beam_wm2"].sum() else 0.0)
    pct_cold = (100.0 * cold["blocked_wm2"].sum() / cold["beam_wm2"].sum()
                if cold["beam_wm2"].sum() else 0.0)

    # auto-size suggestion: overhang depth for full shade at summer-solstice noon
    noon_month = 6 if lat >= 0 else 12
    noon = m.design_day_sun(lat, lon, tz, noon_month)
    suggestion = None
    if not noon.empty:
        peak = noon.loc[noon["altitude"].idxmax()]
        _, vsa_noon, facing = m.relative_angles(peak["altitude"], peak["azimuth"],
                                                facade_az)
        if bool(facing) and vsa_noon > 5:
            suggestion = float(
                (geom["win_h"] + geom["oh_gap"]) / np.tan(np.radians(float(vsa_noon))))

    stats = {
        "beam_blocked_annual_pct": pct_all,
        "beam_on_glass_kwh_m2": tot_beam / 1000.0,
        "blocked_when_hot_pct": pct_hot,
        "blocked_when_cold_pct": pct_cold,
        "full_shade_overhang_m": suggestion,
    }
    charts = [
        _chart("window_3d", "3D Window & Shading Devices",
               m.build_window_3d_figure(geom, sun_day)),
        _chart("shading_mask", "Shading Mask",
               m.build_shading_mask_figure(geom, facade_az, day_arcs)),
        _chart("monthly_block", "Monthly Beam: Blocked vs Admitted",
               m.build_monthly_block_figure(perf)),
    ]
    return _response("shading-designer", charts, stats)


@router.post("/shading-designer")
async def shading_designer_charts(
    file: UploadFile = File(...),
    orientation: Optional[str] = Form(None),
    window_width: float = Form(2.4),
    window_height: float = Form(1.5),
    overhang_depth: float = Form(0.6),
    overhang_gap: float = Form(0.1),
    overhang_extension: float = Form(0.3),
    fin_left: float = Form(0.0),
    fin_right: float = Form(0.0),
    day_label: str = Form("Jun 21"),
):
    orientations = shading_designer_module.ORIENTATIONS
    orient = orientation or list(orientations.keys())[4]
    if orient not in orientations:
        raise _http_400(f"orientation must be one of {list(orientations.keys())}.")
    import calendar as _cal
    parts = day_label.split()
    if not parts or parts[0] not in list(_cal.month_abbr):
        raise _http_400('day_label must look like "Jun 21".')
    month = list(_cal.month_abbr).index(parts[0])

    df, metadata = await _parsed_epw(file)
    _require_columns(df, {"dry_bulb_temperature", "direct_normal_irradiance"},
                     "shading analysis")
    geom = dict(win_w=window_width, win_h=window_height, sill=0.9,
                oh_depth=overhang_depth, oh_gap=overhang_gap,
                oh_ext=overhang_extension, fin_l_depth=fin_left,
                fin_r_depth=fin_right, facade_az=orientations[orient])
    return await _run(_shading_worker, df, metadata, geom,
                      orientations[orient], month)


# ── Solar PV (no EPW needed) ──────────────────────────────────────────────────

def _solar_pv_worker(country: str, roof_size: float, roof_pct: float) -> dict:
    m = solar_pv_module
    data = m._load_data()
    match = data[data["country"].str.lower() == country.strip().lower()]
    if match.empty:
        raise _http_400(
            f"Country '{country}' not found in the SolarGIS dataset. "
            "GET /api/charts/solar-pv/countries lists valid names.")
    row = match.iloc[0]
    selected = str(row["country"])

    daily_vals = [float(row[mo]) for mo in ["jan", "feb", "mar", "apr", "may", "jun",
                                            "jul", "aug", "sep", "oct", "nov", "dec"]]
    monthly_vals = [d * days for d, days in zip(daily_vals, m._DAYS_MONTH)]
    peak_idx = monthly_vals.index(max(monthly_vals))
    low_idx = monthly_vals.index(min(monthly_vals))
    annual_total = sum(monthly_vals)
    annual_daily_avg = float(row["yearly_daily"])

    effective_area = roof_size * (roof_pct / 100.0)
    system_kwp = effective_area / 10.0  # 10 m² = 1 kWp

    stats = {
        "country": selected,
        "iso_a3": str(row["iso_a3"]),
        "region": str(row["region"]),
        "annual_yield_kwh_per_kwp": annual_total,
        "annual_daily_avg_kwh_per_kwp": annual_daily_avg,
        "peak_month": m._MONTHS[peak_idx],
        "peak_monthly_kwh_per_kwp": monthly_vals[peak_idx],
        "peak_daily_kwh_per_kwp": daily_vals[peak_idx],
        "effective_area_m2": effective_area,
        "system_kwp": system_kwp,
    }
    charts = [
        _chart("specific_yield", "Monthly Yield per kWp",
               m._build_chart(monthly_vals, daily_vals, peak_idx, low_idx, selected)),
    ]
    if system_kwp > 0:
        monthly_kwh = [v * system_kwp for v in monthly_vals]
        daily_kwh = [v * system_kwp for v in daily_vals]
        stats.update({
            "annual_yield_kwh": sum(monthly_kwh),
            "annual_daily_avg_kwh": annual_daily_avg * system_kwp,
            "peak_month_output_kwh": monthly_kwh[peak_idx],
            "peak_day_output_kwh": daily_kwh[peak_idx],
        })
        charts.append(
            _chart("absolute_yield", "Monthly System Output",
                   m._build_absolute_chart(monthly_kwh, daily_kwh, peak_idx,
                                           low_idx, selected, system_kwp)))
    return _response("solar-pv", charts, stats)


# ── Dry Bulb Temperature ──────────────────────────────────────────────────────

def _dbt_worker(df: pd.DataFrame, start_month: int, end_month: int,
                start_hour: int, end_hour: int) -> dict:
    m = dbt_module
    daily = _daily_stats(df)
    sd, ed = _dates_from_months(df, start_month, end_month)
    stats = {
        **m.compute_annual_trend_stats(df, sd, ed),
        **m.compute_energy_metrics_stats(df, sd, ed, start_hour, end_hour),
    }
    charts = [
        _chart("annual_trend", "Annual Temperature Trend",
               m.build_annual_trend_figure(daily, sd, ed)),
        _chart("monthly_trend", "Monthly Temperature Trend",
               m.build_monthly_trend_figure(df, sd, ed)),
        _chart("diurnal_profile", "Diurnal Temperature Profile",
               m.build_diurnal_profile_figure(df, start_hour, end_hour)),
        _chart("comfort_analysis", "ASHRAE Adaptive Comfort",
               m.build_comfort_analysis_figure(daily, sd, ed)),
        _chart("energy_metrics", "Degree Days & Energy Metrics",
               m.build_energy_metrics_figure(df)),
        _chart("carpet_heatmap", "Annual Temperature Carpet Plot",
               m.build_carpet_heatmap(
                   df, "dry_bulb_temperature",
                   title="Annual Temperature Carpet Plot (Day × Hour)",
                   colorscale="RdYlBu_r", unit="°C", series_name="Temperature")),
        _chart("month_hour_surface", "Mean Temperature Surface (3D)",
               m.build_month_hour_surface(
                   df, "dry_bulb_temperature",
                   title="Mean Temperature Surface — Month × Hour",
                   colorscale="RdYlBu_r", unit="°C", series_name="Temperature")),
        _chart("animated_diurnal", "Monthly Diurnal Profile (animated)",
               m.build_animated_diurnal(
                   df, "dry_bulb_temperature",
                   title="Monthly Diurnal Temperature Profile (animated)",
                   unit="°C", series_name="Temperature",
                   line_color="#d32f2f", band_fill="rgba(255,100,100,0.25)")),
    ]
    return _response("dbt", charts, stats)


@router.post("/dbt")
async def dbt_charts(
    file: UploadFile = File(...),
    start_month: int = Form(1),
    end_month: int = Form(12),
    start_hour: int = Form(0),
    end_hour: int = Form(23),
):
    df, _meta = await _parsed_epw(file)
    _require_columns(df, {"dry_bulb_temperature", "relative_humidity"},
                     "temperature analysis")
    _months_list(start_month, end_month)  # validates the range
    return await _run(_dbt_worker, df, start_month, end_month,
                      start_hour, end_hour)


# ── Relative Humidity ─────────────────────────────────────────────────────────

def _humidity_worker(df: pd.DataFrame, start_month: int, end_month: int,
                     start_hour: int, end_hour: int) -> dict:
    m = humidity_module
    daily = _daily_stats(df)
    sd, ed = _dates_from_months(df, start_month, end_month)
    stats = m.compute_annual_trend_stats(df)
    charts = [
        _chart("annual_trend", "Annual Humidity Trend",
               m.build_annual_trend_figure(daily)),
        _chart("monthly_trend", "Monthly Humidity Trend",
               m.build_monthly_trend_figure(df, sd, ed)),
        _chart("diurnal_profile", "Diurnal Humidity Profile",
               m.build_diurnal_profile_figure(df, start_hour, end_hour)),
        _chart("comfort_analysis", "Humidity Comfort Analysis",
               m.build_comfort_analysis_figure(daily, sd, ed)),
        _chart("energy_metrics", "Humidity Energy Metrics",
               m.build_energy_metrics_figure(df)),
        _chart("carpet_heatmap", "Annual RH Carpet Plot",
               dbt_module.build_carpet_heatmap(
                   df, "relative_humidity",
                   title="Annual Relative Humidity Carpet Plot (Day × Hour)",
                   colorscale="Blues", unit="%", series_name="RH")),
        _chart("month_hour_surface", "Mean RH Surface (3D)",
               dbt_module.build_month_hour_surface(
                   df, "relative_humidity",
                   title="Mean Relative Humidity Surface — Month × Hour",
                   colorscale="Blues", unit="%", series_name="RH")),
        _chart("animated_diurnal", "Monthly Diurnal RH Profile (animated)",
               dbt_module.build_animated_diurnal(
                   df, "relative_humidity",
                   title="Monthly Diurnal Humidity Profile (animated)",
                   unit="%", series_name="RH",
                   line_color="#00a8ff", band_fill="rgba(0,150,255,0.25)")),
        _chart("dew_point_carpet", "Annual Dew Point Carpet Plot",
               dbt_module.build_carpet_heatmap(
                   df, "dew_point_temperature",
                   title="Annual Dew Point Carpet Plot (Day × Hour)",
                   colorscale="Viridis", unit="°C", series_name="Dew Point")),
    ]
    return _response("humidity", charts, stats)


@router.post("/humidity")
async def humidity_charts(
    file: UploadFile = File(...),
    start_month: int = Form(1),
    end_month: int = Form(12),
    start_hour: int = Form(0),
    end_hour: int = Form(23),
):
    df, _meta = await _parsed_epw(file)
    _require_columns(
        df, {"relative_humidity", "dry_bulb_temperature", "dew_point_temperature"},
        "humidity analysis")
    _months_list(start_month, end_month)
    return await _run(_humidity_worker, df, start_month, end_month,
                      start_hour, end_hour)


# ── UTCI ──────────────────────────────────────────────────────────────────────

def _utci_worker(df: pd.DataFrame, metadata: dict, start_month: int,
                 end_month: int) -> dict:
    m = utci_module
    lat = metadata.get("latitude")
    lon = metadata.get("longitude")
    if lat is None or lon is None:
        raise _http_400("Location (latitude/longitude) not found in EPW file.")
    tz_str = str(metadata.get("timezone") or "UTC")

    enriched = m.add_utci_columns(df, float(lat), float(lon), tz_str)
    daily = m.compute_daily_stats(enriched)
    sd, ed = _dates_from_months(df, start_month, end_month)

    stats = {
        **m.compute_annual_trend_stats(enriched, sd, ed),
        **m.compute_stress_distribution_stats(enriched, sd, ed),
    }
    charts = [
        _chart("annual_trend", "Annual UTCI Trend",
               m.build_annual_trend_figure(daily, sd, ed)),
        _chart("annual_scatter", "Annual UTCI Scatter",
               m.build_annual_scatter_figure(enriched)),
        _chart("monthly_scatter", "Monthly UTCI Scatter",
               m.build_monthly_scatter_figure(enriched)),
        _chart("stress_distribution", "Thermal Stress Distribution",
               m.build_stress_distribution_figure(enriched, sd, ed)),
        _chart("stress_monthly", "Monthly Stress Composition",
               m.build_stress_monthly_figure(enriched)),
    ]
    return _response("utci", charts, stats)


@router.post("/utci")
async def utci_charts(
    file: UploadFile = File(...),
    start_month: int = Form(1),
    end_month: int = Form(12),
):
    df, metadata = await _parsed_epw(file)
    _require_columns(
        df,
        {"dry_bulb_temperature", "relative_humidity", "wind_speed",
         "direct_normal_irradiance"},
        "UTCI analysis")
    _months_list(start_month, end_month)
    return await _run(_utci_worker, df, metadata, start_month, end_month)


# ── Thermal Comfort ───────────────────────────────────────────────────────────

def _thermal_comfort_worker(df: pd.DataFrame, months: list[int],
                            start_hour: int, end_hour: int,
                            air_speed_adjust: bool) -> dict:
    m = thermal_comfort_module
    work = df.copy()
    if "month" not in work.columns:
        work["month"] = work["datetime"].dt.month
    if "hour" not in work.columns:
        work["hour"] = work["datetime"].dt.hour
    if "doy" not in work.columns:
        work["doy"] = work["datetime"].dt.dayofyear

    # Full-year pipeline (running mean needs full context), month-filter after.
    df_full = m.map_strategies(m.classify_comfort(m.compute_adaptive_comfort(work)))
    if air_speed_adjust and "wind_speed" in df_full.columns:
        breeze = df_full["wind_speed"] > 1.5
        df_full.loc[breeze, "t_comf_80_hi"] += 1.5
        df_full.loc[breeze, "t_comf_90_hi"] += 1.5
        df_full["in_80"] = df_full["dry_bulb_temperature"].between(
            df_full["t_comf_80_lo"], df_full["t_comf_80_hi"])
        df_full["in_90"] = df_full["dry_bulb_temperature"].between(
            df_full["t_comf_90_lo"], df_full["t_comf_90_hi"])
    fdf = df_full[df_full["month"].isin(months)]
    if fdf.empty:
        raise _http_400("No hours in the selected month range.")
    degree = m.compute_degree_hours(df_full)
    bubble_df = fdf[(fdf["hour"] >= start_hour) & (fdf["hour"] <= end_hour)]

    stats = {
        **m.compute_thermal_comfort_stats(fdf, degree),
        "bubble": m.compute_comfort_bubble_stats(bubble_df),
    }
    charts = [
        _chart("psychrometric", "Psychrometric Chart",
               m.build_psychrometric_chart_figure(work, months)),
        _chart("monthly_breakdown", "Monthly Comfort Breakdown",
               m.build_monthly_comfort_breakdown_figure(fdf)),
        _chart("comfort_heatmap", "Comfort Heatmap (Month × Hour)",
               m.build_comfort_heatmap_figure(fdf)),
        _chart("strategy_chart", "Passive Strategy Mix",
               m.build_strategy_chart_figure(fdf)),
        _chart("degree_hours", "Cooling & Heating Degree Hours",
               m.build_degree_hours_figure(degree, months)),
        _chart("comfort_bubble", "Comfort Bubble Chart",
               m.build_comfort_bubble_chart(bubble_df)),
    ]
    if df_full["adaptive_applicable"].any():
        charts.insert(4, _chart(
            "adaptive_comfort", "ASHRAE Adaptive Comfort Band",
            m.build_adaptive_comfort_chart_figure(df_full, months)))
    return _response("thermal-comfort", charts, stats)


@router.post("/thermal-comfort")
async def thermal_comfort_charts(
    file: UploadFile = File(...),
    start_month: int = Form(1),
    end_month: int = Form(12),
    start_hour: int = Form(0),
    end_hour: int = Form(23),
    air_speed_adjust: bool = Form(False),
):
    df, _meta = await _parsed_epw(file)
    _require_columns(df, {"dry_bulb_temperature", "relative_humidity"},
                     "thermal comfort analysis")
    months = _months_list(start_month, end_month)
    return await _run(_thermal_comfort_worker, df, months,
                      start_hour, end_hour, air_speed_adjust)


# ── Sun Path & Shading ────────────────────────────────────────────────────────

_SUN_PATH_CHART_TYPES = ["Sun Path", "Dry Bulb Temperature",
                         "Direct Normal Radiation",
                         "Global Horizontal Radiation", "Shading"]


def _sun_path_worker(df: pd.DataFrame, metadata: dict, temp_threshold: float,
                     rad_threshold: float, cutoff_angle: float) -> dict:
    m = sun_path
    charts = []
    metrics: dict = {}
    for ct in _SUN_PATH_CHART_TYPES:
        try:
            fig, mtr = m.build_sun_path_figure(
                df, metadata, chart_type=ct,
                temp_threshold=temp_threshold, rad_threshold=rad_threshold)
        except m.SunPathInputError as e:
            raise _http_400(str(e))
        slug = ct.lower().replace(" ", "_")
        charts.append(_chart(f"sun_path_{slug}", f"Sun Path — {ct}", fig))
        if mtr:
            metrics.update(mtr)

    charts.append(_chart("thermal_matrix", "Thermal & Radiation Matrix",
                         m.build_thermal_matrix_figure(
                             df, temp_threshold, rad_threshold)))

    lat = metadata.get("latitude")
    lon = metadata.get("longitude")
    tz_str = str(metadata.get("timezone") or "UTC")
    orient = m.compute_orientation_shading_stats(
        df, float(lat), float(lon), tz_str,
        temp_threshold=temp_threshold, rad_threshold=rad_threshold,
        cutoff_angle=cutoff_angle)

    stats = {
        **metrics,
        "overheating_rows": orient["overheating_rows"],
        "sun_positions": orient["sun_positions"],
        "orientation_table": orient["orientation_table"],
    }
    return _response("sun-path", charts, stats)


@router.post("/sun-path")
async def sun_path_charts(
    file: UploadFile = File(...),
    temp_threshold: float = Form(28.0),
    rad_threshold: float = Form(315.0),
    design_cutoff_angle: float = Form(45.0),
):
    if not (45 <= design_cutoff_angle <= 89):
        raise _http_400("design_cutoff_angle must be within 45..89.")
    df, metadata = await _parsed_epw(file)
    if metadata.get("latitude") is None or metadata.get("longitude") is None:
        raise _http_400("Location (latitude/longitude) not found in EPW file.")
    return await _run(_sun_path_worker, df, metadata, temp_threshold,
                      rad_threshold, design_cutoff_angle)


# ── Rainfall (no EPW needed — NOAA daily summaries) ───────────────────────────

def _rainfall_worker(station_name: str, year: int, months: list[int],
                     heavy_rain_threshold: float, roof_area_m2: float,
                     paved_area_m2: float, green_area_m2: float,
                     water_area_m2: float, gi_percentile: int,
                     gi_start_year: int) -> dict:
    m = rainfall_module
    station_id = m.STATIONS[station_name]

    # Bypass @st.cache_data — it needs a Streamlit script context; the
    # underlying functions are plain fetches (same trick combined_report uses).
    fetch_noaa = getattr(m._fetch_noaa, "__wrapped__", m._fetch_noaa)
    fetch_depth = getattr(m._fetch_percentile_depth, "__wrapped__",
                          m._fetch_percentile_depth)

    df_all = fetch_noaa(station_id, year)
    if df_all.empty:
        raise _http_400(
            f"NOAA returned no daily-summaries data for station "
            f"'{station_name}' in {year}.")
    fdf = df_all[df_all["month"].isin(months)]
    if fdf.empty:
        raise _http_400("No rainfall data in the selected month range.")

    stats = {
        **m.compute_monthly_rainfall_stats(fdf),
        **m.compute_rainy_days_stats(fdf, heavy_rain_threshold),
        **m.compute_roof_runoff_stats(fdf, roof_area_m2, paved_area_m2,
                                      green_area_m2, water_area_m2),
        "station_name": station_name,
        "station_id": station_id,
        "year": year,
    }
    charts = [
        _chart("monthly_rainfall", "Monthly Rainfall",
               m.build_monthly_rainfall_figure(fdf, year)),
        _chart("rainy_days", "Rainy Days & Intensity",
               m.build_rainy_days_figure(fdf, year, heavy_rain_threshold)),
        _chart("roof_runoff", "Surface Runoff Potential",
               m.build_roof_runoff_figure(fdf, year, roof_area_m2,
                                          paved_area_m2, green_area_m2,
                                          water_area_m2)),
    ]

    depth = fetch_depth(station_id, gi_percentile, gi_start_year)
    if "error" not in depth:
        baseline_mm = depth["raw_mm"]
        charts.append(
            _chart("gi_balance", "Green Infrastructure Water Balance",
                   m.build_gi_balance_figure(df_all, year, baseline_mm,
                                             gi_percentile)))
        stats.update({
            "gi_baseline_mm": baseline_mm,
            "gi_percentile": gi_percentile,
            **m.compute_gi_balance_stats(df_all, baseline_mm),
        })
    else:
        stats["gi_error"] = depth["error"]
    return _response("rainfall", charts, stats)


@router.post("/rainfall")
async def rainfall_charts(
    station_name: str = Form(...),
    year: int = Form(...),
    start_month: int = Form(1),
    end_month: int = Form(12),
    heavy_rain_threshold: float = Form(50.0),
    roof_area_m2: float = Form(0.0),
    paved_area_m2: float = Form(0.0),
    green_area_m2: float = Form(0.0),
    water_area_m2: float = Form(0.0),
    gi_percentile: int = Form(95),
    gi_start_year: int = Form(1990),
):
    if station_name not in rainfall_module.STATIONS:
        raise _http_400(
            f"Unknown rainfall station '{station_name}'. "
            f"Valid options: {list(rainfall_module.STATIONS.keys())}")
    if gi_percentile not in [85, 90, 95, 98]:
        raise _http_400("gi_percentile must be one of 85, 90, 95, 98.")
    months = _months_list(start_month, end_month)
    return await _run(_rainfall_worker, station_name, year, months,
                      heavy_rain_threshold, roof_area_m2, paved_area_m2,
                      green_area_m2, water_area_m2, gi_percentile,
                      gi_start_year)


@router.get("/rainfall/stations")
def rainfall_stations():
    """List available NOAA rainfall stations."""
    return {"stations": list(rainfall_module.STATIONS.keys())}


@router.get("/solar-pv/countries")
def solar_pv_countries():
    """List country names available in the bundled SolarGIS dataset."""
    return {"countries": sorted(solar_pv_module._load_data()["country"].tolist())}


@router.post("/solar-pv")
async def solar_pv_charts(
    country: str = Form(...),
    roof_size_m2: float = Form(100.0),
    roof_pct: float = Form(80.0),
):
    if roof_size_m2 <= 0 or not (0 <= roof_pct <= 100):
        raise _http_400("roof_size_m2 must be > 0 and roof_pct in 0..100.")
    return await _run(_solar_pv_worker, country, roof_size_m2, roof_pct)
