"""FastAPI server for generating PowerPoint climate analysis reports from EPW files."""

from fastapi import FastAPI, UploadFile, File, HTTPException, Query, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import pandas as pd
import io
import os
import asyncio
import hashlib
import subprocess
import tempfile
from datetime import datetime
from functools import partial
from typing import Optional
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from cachetools import TTLCache

from pages.modules.epw_parser import parse_epw
from pages.modules.ppt_report import generate_pptx_report, generate_shading_pptx_report, generate_wind_pptx_report
from pages.modules.thermal_comfort_ppt import generate_thermal_comfort_pptx_report
from pages.modules.combined_report import generate_combined_pptx_report
from pages.modules.rainfall_ppt import generate_rainfall_pptx_report
from pages.modules.rainfall_module import STATIONS as RAINFALL_STATIONS


class ErrorResponse(BaseModel):
    error: str
    detail: str


_VALID_PERCENTILES = [85, 90, 95, 98]
_VALID_SECTORS = [4, 8, 16]


def _validate_n_sectors(n_sectors: int) -> int:
    """Raise 400 if n_sectors is not one of 4, 8, 16."""
    if n_sectors not in _VALID_SECTORS:
        raise HTTPException(
            status_code=400,
            detail={"error": "validation_error", "detail": f"n_sectors must be one of {_VALID_SECTORS}. Got: {n_sectors}"},
        )
    return n_sectors


def _validate_combined_params(
    rainfall_station_name: Optional[str],
    rainfall_year: Optional[int],
    rainfall_gi_percentile: int,
) -> Optional[str]:
    """Validate combined-analysis rainfall params. Returns station_id or None."""
    if rainfall_station_name is None:
        return None
    if rainfall_station_name not in RAINFALL_STATIONS:
        raise ValueError(
            f"Unknown rainfall station '{rainfall_station_name}'. "
            f"Valid options: {list(RAINFALL_STATIONS.keys())}"
        )
    if rainfall_year is None:
        raise ValueError("rainfall_year is required when rainfall_station_name is provided.")
    if rainfall_gi_percentile not in _VALID_PERCENTILES:
        raise ValueError(
            f"rainfall_gi_percentile must be one of {_VALID_PERCENTILES}. Got: {rainfall_gi_percentile}"
        )
    return RAINFALL_STATIONS[rainfall_station_name]


# ── In-memory report cache (30 min TTL, max 64 entries) ──────────────────────
_report_cache: TTLCache = TTLCache(maxsize=64, ttl=1800)


def _make_cache_key(file_content: bytes, **params) -> str:
    content_hash = hashlib.sha256(file_content).hexdigest()[:16]
    param_str = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    return f"{content_hash}:{param_str}"


# ── PDF conversion helper (requires LibreOffice on server) ───────────────────
def _pptx_to_pdf(pptx_buffer: io.BytesIO) -> io.BytesIO:
    """Convert PPTX bytes to PDF using LibreOffice headless.

    Requires LibreOffice to be installed. Not available in the base Docker
    image — add `apt-get install -y libreoffice` to the Dockerfile to enable.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        pptx_path = os.path.join(tmpdir, "report.pptx")
        with open(pptx_path, "wb") as f:
            f.write(pptx_buffer.getvalue())
        result = subprocess.run(
            ["libreoffice", "--headless", "--convert-to", "pdf", "--outdir", tmpdir, pptx_path],
            capture_output=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(f"LibreOffice conversion failed: {result.stderr.decode()}")
        pdf_path = pptx_path.replace(".pptx", ".pdf")
        with open(pdf_path, "rb") as f:
            buf = io.BytesIO(f.read())
        buf.seek(0)
        return buf


_PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def _stream_response(buffer: io.BytesIO, filename: str, output_format: str) -> StreamingResponse:
    """Return a StreamingResponse, converting to PDF if requested."""
    if output_format == "pdf":
        buffer = _pptx_to_pdf(buffer)
        media_type = "application/pdf"
        filename = filename.replace(".pptx", ".pdf")
    else:
        media_type = _PPTX_MIME
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )

app = FastAPI(
    title="Climate Zone Finder - Climate Analysis API",
    description="REST API for climate analysis charts (Plotly JSON) and PowerPoint reports from EPW files",
    version="1.1.0"
)

# Note: allow_credentials must be False with a wildcard origin — browsers
# reject the "*" + credentials combination, and no endpoint uses cookies.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for production
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Chart JSON responses compress ~10x; PPTX streams are already zip-compressed
# and skip gzip via their content type.
app.add_middleware(GZipMiddleware, minimum_size=1024)

from chart_api import router as chart_router  # noqa: E402  (needs app deps loaded)

app.include_router(chart_router)

def _parse_epw_bytes(file_content: bytes) -> tuple[pd.DataFrame, dict]:
    """Thin wrapper: decode bytes and delegate to the shared parse_epw()."""
    return parse_epw(file_content.decode("utf-8", errors="replace"))


@app.get("/api/health")
def health_check():
    """Health check endpoint."""
    return {"status": "ok", "message": "Climate Zone Finder PPT Report API is running"}


@app.post(
    "/api/reports/climate-analysis",
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def generate_climate_report(
    file: UploadFile = File(...),
    start_date: Optional[str] = Query(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Query(None, description="End date (YYYY-MM-DD)"),
    start_hour: int = Query(0, description="Start hour (0-23)"),
    end_hour: int = Query(23, description="End hour (0-23)"),
    output_format: str = Query("pptx", description="Output format: 'pptx' (default) or 'pdf' (requires LibreOffice)"),
):
    """Generate a climate analysis PowerPoint report from an EPW file."""
    try:
        content = await file.read()
        df, metadata = _parse_epw_bytes(content)
        if df.empty:
            raise ValueError("EPW file is empty or could not be parsed")
        start_dt = (
            df['datetime'].min().date() if start_date is None
            else datetime.strptime(start_date, "%Y-%m-%d").date()
        )
        end_dt = (
            df['datetime'].max().date() if end_date is None
            else datetime.strptime(end_date, "%Y-%m-%d").date()
        )
        cache_key = _make_cache_key(
            content, start_date=str(start_dt), end_date=str(end_dt),
            start_hour=start_hour, end_hour=end_hour,
        )
        if cache_key in _report_cache:
            pptx_buffer = io.BytesIO(_report_cache[cache_key])
        else:
            loop = asyncio.get_event_loop()
            pptx_buffer = await loop.run_in_executor(
                None,
                partial(
                    generate_pptx_report,
                    df=df, start_date=start_dt, end_date=end_dt,
                    start_hour=start_hour, end_hour=end_hour,
                    selected_parameter="dry_bulb_temperature",
                    metadata=metadata,
                ),
            )
            _report_cache[cache_key] = pptx_buffer.getvalue()
        city = metadata.get('city', 'Climate_Report')
        filename = f"Climate_Analysis_{city}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pptx"
        return _stream_response(pptx_buffer, filename, output_format)

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"error": "validation_error", "detail": str(e)})
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "generation_failed", "detail": str(e)})


@app.post(
    "/api/reports/shading-analysis",
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def generate_shading_report(
    file: UploadFile = File(...),
    temp_threshold: float = Query(28.0, description="Temperature threshold (°C)"),
    rad_threshold: float = Query(315.0, description="Radiation threshold (W/m²)"),
    design_cutoff_angle: float = Query(45.0, description="Design cutoff angle (°)"),
    output_format: str = Query("pptx", description="Output format: 'pptx' (default) or 'pdf' (requires LibreOffice)"),
):
    """Generate a shading analysis PowerPoint report from an EPW file."""
    try:
        content = await file.read()
        df, metadata = _parse_epw_bytes(content)
        if df.empty:
            raise ValueError("EPW file is empty or could not be parsed")
        cache_key = _make_cache_key(
            content, temp_threshold=temp_threshold,
            rad_threshold=rad_threshold, design_cutoff_angle=design_cutoff_angle,
        )
        if cache_key in _report_cache:
            pptx_buffer = io.BytesIO(_report_cache[cache_key])
        else:
            loop = asyncio.get_event_loop()
            pptx_buffer = await loop.run_in_executor(
                None,
                partial(
                    generate_shading_pptx_report,
                    df=df, metadata=metadata,
                    temp_threshold=temp_threshold, rad_threshold=rad_threshold,
                    lat=metadata.get('latitude'), lon=metadata.get('longitude'),
                    tz_str=str(metadata.get('timezone', 'UTC')),
                    design_cutoff_angle=design_cutoff_angle,
                ),
            )
            _report_cache[cache_key] = pptx_buffer.getvalue()
        city = metadata.get('city', 'Shading_Report')
        filename = f"Shading_Analysis_{city}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pptx"
        return _stream_response(pptx_buffer, filename, output_format)

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"error": "validation_error", "detail": str(e)})
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "generation_failed", "detail": str(e)})


@app.post(
    "/api/reports/wind-analysis",
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def generate_wind_report(
    file: UploadFile = File(...),
    n_sectors: int = Form(16, description="Number of wind direction sectors (default: 16, options: 4, 8, 16)"),
    output_format: str = Query("pptx", description="Output format: 'pptx' (default) or 'pdf' (requires LibreOffice)"),
):
    """Generate a wind analysis PowerPoint report from an EPW file."""
    _validate_n_sectors(n_sectors)
    try:
        content = await file.read()
        df, metadata = _parse_epw_bytes(content)
        if df.empty:
            raise ValueError("EPW file is empty or could not be parsed")
        cache_key = _make_cache_key(content, n_sectors=n_sectors)
        if cache_key in _report_cache:
            pptx_buffer = io.BytesIO(_report_cache[cache_key])
        else:
            loop = asyncio.get_event_loop()
            pptx_buffer = await loop.run_in_executor(
                None,
                partial(generate_wind_pptx_report, df=df, metadata=metadata, n_sectors=n_sectors),
            )
            _report_cache[cache_key] = pptx_buffer.getvalue()
        city = metadata.get('city', 'Wind_Report')
        filename = f"Wind_Analysis_{city}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pptx"
        return _stream_response(pptx_buffer, filename, output_format)

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"error": "validation_error", "detail": str(e)})
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "generation_failed", "detail": str(e)})


@app.post(
    "/api/reports/combined-analysis",
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def generate_combined_report(
    file: UploadFile = File(...),
    start_date: Optional[str] = Form(None, description="Start date (YYYY-MM-DD)"),
    end_date: Optional[str] = Form(None, description="End date (YYYY-MM-DD)"),
    start_hour: int = Form(0, description="Start hour (0-23), default: 0 (full day)"),
    end_hour: int = Form(23, description="End hour (0-23), default: 23 (full day)"),
    temp_threshold: float = Form(28.0, description="Temperature threshold (°C), default: 28.0"),
    rad_threshold: float = Form(315.0, description="Radiation threshold (W/m²), default: 315.0"),
    design_cutoff_angle: float = Form(45.0, description="Design cutoff angle (°), default: 45.0"),
    n_sectors: int = Form(16, description="Number of wind direction sectors (default: 16, options: 4, 8, 16)"),
    # ── Branding (cover slide) ───────────────────────────────────────────────
    project_name: Optional[str] = Form(None, description="Project name shown on cover slide"),
    client_name: Optional[str] = Form(None, description="Client name shown on cover slide"),
    report_date: Optional[str] = Form(None, description="Report date on cover (default: today, format: DD Month YYYY)"),
    # ── Optional Rainfall section ────────────────────────────────────────────
    rainfall_station_name: Optional[str] = Form(None, description=(
        "Rainfall station name. Call GET /api/rainfall/stations for valid values. "
        "Omit to skip the Rainfall section."
    )),
    rainfall_year: Optional[int] = Form(None, description="Rainfall data year, e.g. 2023. Required when rainfall_station_name is set."),
    rainfall_heavy_threshold: float = Form(50.0, description="Heavy rain threshold mm/day (default: 50.0)"),
    rainfall_roof_area_m2:   float = Form(0.0, ge=0, description="Roof area m² (default: 0)"),
    rainfall_paved_area_m2:  float = Form(0.0, ge=0, description="Paved area m² (default: 0)"),
    rainfall_green_area_m2:  float = Form(0.0, ge=0, description="Green/landscape area m² (default: 0)"),
    rainfall_water_area_m2:  float = Form(0.0, ge=0, description="Waterbody area m² (default: 0)"),
    rainfall_gi_percentile:  int   = Form(95,  description="GI percentile baseline. Valid: 85, 90, 95, 98 (default: 95)"),
    rainfall_gi_start_year:  int   = Form(1990, ge=1950, le=2023, description="GI historical start year (default: 1990)"),
    # ── Solar PV section ─────────────────────────────────────────────────────
    solar_pv_country:       str   = Form("India", description="Country name for SolarGIS PV yield data (default: India)"),
    solar_pv_roof_size_m2:  float = Form(100.0, ge=1, description="Total roof area in m² (default: 100). Every 10 m² = 1 kWp."),
    solar_pv_roof_pct:      float = Form(80.0, ge=0, le=100, description="Percentage of roof area used for solar panels (default: 80%)"),
    output_format: str = Query("pptx", description="Output format: 'pptx' (default) or 'pdf' (requires LibreOffice)"),
):
    """
    Generate a combined Climate & Shading & Wind & Thermal Comfort (+ optional Rainfall) report.

    Optional branding parameters (project_name, client_name, report_date) are rendered on the
    cover slide. PDF conversion requires LibreOffice installed on the server.
    """
    _validate_n_sectors(n_sectors)
    try:
        content = await file.read()
        df, metadata = _parse_epw_bytes(content)
        if df.empty:
            raise ValueError("EPW file is empty or could not be parsed")

        _year = df["datetime"].dt.year.iloc[0] if not df.empty else 2024
        start_dt = (
            pd.to_datetime(f"{_year}-01-01").date() if start_date is None
            else datetime.strptime(start_date, "%Y-%m-%d").date()
        )
        end_dt = (
            pd.to_datetime(f"{_year}-12-31").date() if end_date is None
            else datetime.strptime(end_date, "%Y-%m-%d").date()
        )

        _rain_sid = _validate_combined_params(
            rainfall_station_name, rainfall_year, rainfall_gi_percentile
        )

        branding = {
            "project_name": project_name,
            "client_name": client_name,
            "report_date": report_date,
        }

        cache_key = _make_cache_key(
            content,
            start_date=str(start_dt), end_date=str(end_dt),
            start_hour=start_hour, end_hour=end_hour,
            temp_threshold=temp_threshold, rad_threshold=rad_threshold,
            design_cutoff_angle=design_cutoff_angle, n_sectors=n_sectors,
            rainfall_station_name=str(rainfall_station_name), rainfall_year=str(rainfall_year),
            rainfall_heavy_threshold=rainfall_heavy_threshold,
            rainfall_gi_percentile=rainfall_gi_percentile,
            project_name=str(project_name), client_name=str(client_name),
            solar_pv_country=solar_pv_country,
            solar_pv_roof_size_m2=solar_pv_roof_size_m2,
            solar_pv_roof_pct=solar_pv_roof_pct,
        )
        if cache_key in _report_cache:
            pptx_buffer = io.BytesIO(_report_cache[cache_key])
        else:
            loop = asyncio.get_event_loop()
            pptx_buffer = await loop.run_in_executor(
                None,
                partial(
                    generate_combined_pptx_report,
                    df=df, start_date=start_dt, end_date=end_dt,
                    start_hour=start_hour, end_hour=end_hour,
                    selected_parameter="combined", metadata=metadata,
                    temp_threshold=temp_threshold, rad_threshold=rad_threshold,
                    n_sectors=n_sectors, design_cutoff_angle=design_cutoff_angle,
                    include_thermal_comfort=True,
                    rainfall_station_name=rainfall_station_name,
                    rainfall_station_id=_rain_sid, rainfall_year=rainfall_year,
                    rainfall_start_month=start_dt.month, rainfall_end_month=end_dt.month,
                    rainfall_heavy_threshold=rainfall_heavy_threshold,
                    rainfall_surface_areas={
                        "roof":  rainfall_roof_area_m2, "paved": rainfall_paved_area_m2,
                        "green": rainfall_green_area_m2, "water": rainfall_water_area_m2,
                    },
                    rainfall_gi_percentile=rainfall_gi_percentile,
                    rainfall_gi_start_year=rainfall_gi_start_year,
                    branding=branding,
                    solar_pv_country=solar_pv_country,
                    solar_pv_roof_size_m2=solar_pv_roof_size_m2,
                    solar_pv_roof_pct=solar_pv_roof_pct,
                ),
            )
            _report_cache[cache_key] = pptx_buffer.getvalue()

        city = metadata.get('city', 'Combined_Report')
        filename = f"Climate_Shading_Analysis_{city}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pptx"
        return _stream_response(pptx_buffer, filename, output_format)

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"error": "validation_error", "detail": str(e)})
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "generation_failed", "detail": str(e)})


@app.post(
    "/api/reports/thermal-comfort",
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def generate_thermal_comfort_report(
    file: UploadFile = File(...),
    output_format: str = Query("pptx", description="Output format: 'pptx' (default) or 'pdf' (requires LibreOffice)"),
):
    """Generate a thermal comfort analysis PowerPoint report from an EPW file."""
    try:
        content = await file.read()
        df, metadata = _parse_epw_bytes(content)
        if df.empty:
            raise ValueError("EPW file is empty or could not be parsed")
        cache_key = _make_cache_key(content)
        if cache_key in _report_cache:
            pptx_buffer = io.BytesIO(_report_cache[cache_key])
        else:
            loop = asyncio.get_event_loop()
            pptx_buffer = await loop.run_in_executor(
                None,
                partial(generate_thermal_comfort_pptx_report, df=df, metadata=metadata),
            )
            _report_cache[cache_key] = pptx_buffer.getvalue()
        city = metadata.get('city', 'Thermal_Comfort_Report')
        filename = f"Thermal_Comfort_Analysis_{city}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pptx"
        return _stream_response(pptx_buffer, filename, output_format)

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"error": "validation_error", "detail": str(e)})
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "generation_failed", "detail": str(e)})


@app.get("/api/rainfall/stations")
def list_rainfall_stations():
    """Return all available NOAA weather stations for rainfall analysis."""
    return {"stations": list(RAINFALL_STATIONS.keys())}


@app.post(
    "/api/reports/rainfall-analysis",
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
)
async def generate_rainfall_report(
    # ── Station & period ─────────────────────────────────────────────────────
    station_name: str = Form(..., description=(
        "Station name — must match one of the available stations. "
        "Call GET /api/rainfall/stations for the full list. "
        "Example: 'New Delhi (Safdarjung)'"
    )),
    year: int = Form(..., description="Data year, e.g. 2023"),
    start_month: int = Form(1,  ge=1, le=12, description="Start month 1–12 (default: 1 = January)"),
    end_month:   int = Form(12, ge=1, le=12, description="End month 1–12 (default: 12 = December)"),

    # ── Rainfall classification ───────────────────────────────────────────────
    heavy_rain_threshold: float = Form(50.0, description=(
        "Daily rainfall ≥ this value (mm/day) is classified as Heavy rain. "
        "Days above 2× this value are classified as Extreme. Default: 50 mm/day"
    )),

    # ── Surface runoff areas ──────────────────────────────────────────────────
    roof_area_m2:   float = Form(0.0, ge=0, description="Roof area — Terrace + Service (m²). RC = 0.90"),
    paved_area_m2:  float = Form(0.0, ge=0, description="Total paved area — Roads, Pathways, Hardscape (m²). RC = 0.90"),
    green_area_m2:  float = Form(0.0, ge=0, description="Total landscape/green area — Trees, Shrubs, Groundcover (m²). RC = 0.10"),
    water_area_m2:  float = Form(0.0, ge=0, description="Waterbody area (m²). RC = 0.90"),

    # ── Green Infrastructure balance ─────────────────────────────────────────
    gi_percentile:  int = Form(95, description=(
        "Percentile of historical rainy-day depths used as the GI retention baseline. "
        "Valid options: 85, 90, 95, 98. Default: 95"
    )),
    gi_start_year:  int = Form(1990, ge=1950, le=2023, description=(
        "Earliest year of historical NOAA data used to calculate the percentile baseline. "
        "Default: 1990"
    )),
    output_format: str = Query("pptx", description="Output format: 'pptx' (default) or 'pdf' (requires LibreOffice)"),
):
    """
    Generate a Rainfall Analysis PowerPoint report using live NOAA daily-summaries data.

    **No file upload required** — data is fetched automatically from NOAA NCEI for the
    selected station and year.

    ### Slides generated
    1. **Cover** — station name, year, analysis period
    2. **Monthly Rainfall** — bar chart + KPIs (Annual total, Wettest month, Mean monthly)
    3. **Rainy Days Classification** — stacked bar chart + KPIs (Total, Light, Moderate, Heavy, Extreme)
    4. **Rainwater Harvesting Potential** — stored vs overflow chart + KPIs (Storage Potential, Recharge Potential, Overflow Days, Worst Overflow Month)
    5. **Surface Runoff by Type** — stacked bar chart per surface + KPIs (Total annual, Peak month, per-surface breakdown)

    ### Rain intensity thresholds (mm/day)
    | Class    | Rule                                        |
    |----------|---------------------------------------------|
    | Light    | 0.1 – 10                                    |
    | Moderate | 10 – `heavy_rain_threshold`                 |
    | Heavy    | `heavy_rain_threshold` – 2× threshold       |
    | Extreme  | ≥ 2× `heavy_rain_threshold`                 |

    ### Runoff coefficients (RC) — fixed per surface type
    | Surface     | RC   |
    |-------------|------|
    | Roof        | 0.90 |
    | Paved       | 0.90 |
    | Green/Lawn  | 0.10 |
    | Waterbody   | 0.90 |
    """
    # Validate inputs
    if station_name not in RAINFALL_STATIONS:
        raise HTTPException(
            status_code=400,
            detail={"error": "validation_error", "detail": (
                f"Unknown station '{station_name}'. "
                f"Valid options: {list(RAINFALL_STATIONS.keys())}"
            )},
        )
    if start_month > end_month:
        raise HTTPException(
            status_code=400,
            detail={"error": "validation_error", "detail": f"start_month ({start_month}) must be ≤ end_month ({end_month})."},
        )
    if gi_percentile not in _VALID_PERCENTILES:
        raise HTTPException(
            status_code=400,
            detail={"error": "validation_error", "detail": f"gi_percentile must be one of {_VALID_PERCENTILES}. Got: {gi_percentile}"},
        )

    station_id = RAINFALL_STATIONS[station_name]
    surface_areas = {
        "roof":  roof_area_m2,
        "paved": paved_area_m2,
        "green": green_area_m2,
        "water": water_area_m2,
    }

    try:
        cache_key = _make_cache_key(
            b"",
            station_name=station_name, year=year,
            start_month=start_month, end_month=end_month,
            heavy_rain_threshold=heavy_rain_threshold,
            gi_percentile=gi_percentile, gi_start_year=gi_start_year,
        )
        if cache_key in _report_cache:
            pptx_buffer = io.BytesIO(_report_cache[cache_key])
        else:
            loop = asyncio.get_event_loop()
            pptx_buffer = await loop.run_in_executor(
                None,
                partial(
                    generate_rainfall_pptx_report,
                    station_name=station_name, station_id=station_id,
                    year=year, start_month=start_month, end_month=end_month,
                    heavy_rain_threshold=heavy_rain_threshold,
                    surface_areas=surface_areas,
                    gi_percentile=gi_percentile, gi_start_year=gi_start_year,
                ),
            )
            _report_cache[cache_key] = pptx_buffer.getvalue()
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"error": "validation_error", "detail": str(e)})
    except Exception as e:
        raise HTTPException(status_code=500, detail={"error": "generation_failed", "detail": str(e)})

    safe_station = station_name.replace(" ", "_").replace("(", "").replace(")", "").replace("\\", "_")
    filename = f"Rainfall_Analysis_{safe_station}_{year}.pptx"
    return _stream_response(pptx_buffer, filename, output_format)


@app.get("/api/docs")
def api_documentation():
    """API documentation."""
    return {
        "title": "Climate Zone Finder - PPT Report API",
        "version": "1.0.0",
        "endpoints": {
            "health_check": {
                "method": "GET",
                "path": "/api/health",
                "description": "Check if API is running"
            },
            "climate_analysis_report": {
                "method": "POST",
                "path": "/api/reports/climate-analysis",
                "description": "Generate climate analysis PowerPoint report from EPW file",
                "parameters": {
                    "file": "EPW weather file (required)",
                    "start_date": "Start date in YYYY-MM-DD format (optional)",
                    "end_date": "End date in YYYY-MM-DD format (optional)",
                    "start_hour": "Start hour 0-23 (default: 0)",
                    "end_hour": "End hour 0-23 (default: 23)"
                }
            },
            "shading_analysis_report": {
                "method": "POST",
                "path": "/api/reports/shading-analysis",
                "description": "Generate shading analysis PowerPoint report from EPW file",
                "parameters": {
                    "file": "EPW weather file (required)",
                    "temp_threshold": "Temperature threshold in °C (default: 28.0)",
                    "rad_threshold": "Radiation threshold in W/m² (default: 315.0)",
                    "design_cutoff_angle": "Design cutoff angle in degrees (default: 45.0)"
                }
            },
            "wind_analysis_report": {
                "method": "POST",
                "path": "/api/reports/wind-analysis",
                "description": "Generate wind analysis PowerPoint report from EPW file",
                "parameters": {
                    "file": "EPW weather file (required)",
                    "n_sectors": "Number of wind direction sectors (default: 16, options: 4, 8, 16)"
                }
            },
            "thermal_comfort_report": {
                "method": "POST",
                "path": "/api/reports/thermal-comfort",
                "description": "Generate thermal comfort analysis PowerPoint report from EPW file",
                "parameters": {
                    "file": "EPW weather file (required)"
                }
            },
            "combined_analysis_report": {
                "method": "POST",
                "path": "/api/reports/combined-analysis",
                "description": "Generate combined Climate & Shading & Wind & Thermal Comfort (+ optional Rainfall) PowerPoint report from EPW file",
                "parameters": {
                    "file": "EPW weather file (required)",
                    "start_date": "Start date in YYYY-MM-DD format (optional, default: first day in file)",
                    "end_date": "End date in YYYY-MM-DD format (optional, default: last day in file)",
                    "start_hour": "Start hour 0-23 (default: 0 - full day)",
                    "end_hour": "End hour 0-23 (default: 23 - full day)",
                    "temp_threshold": "Temperature threshold in °C for overheating (default: 28.0)",
                    "rad_threshold": "Radiation threshold in W/m² (default: 315.0)",
                    "design_cutoff_angle": "Design cutoff angle in degrees (default: 45.0)",
                    "n_sectors": "Wind rose compass sectors (default: 16, options: 4, 8, 16)",
                    "rainfall_station_name": "NOAA station name — omit to skip rainfall section (call GET /api/rainfall/stations)",
                    "rainfall_year": "Rainfall data year e.g. 2023 — required if station_name is set",
                    "rainfall_heavy_threshold": "Heavy rain threshold mm/day (default: 50.0). Month range derived from start_date/end_date.",
                    "rainfall_roof_area_m2": "Roof area m², RC=0.90 (default: 0)",
                    "rainfall_paved_area_m2": "Paved area m², RC=0.90 (default: 0)",
                    "rainfall_green_area_m2": "Green area m², RC=0.10 (default: 0)",
                    "rainfall_water_area_m2": "Waterbody area m², RC=0.90 (default: 0)",
                    "rainfall_gi_percentile": "GI baseline percentile — valid: 85, 90, 95, 98 (default: 95)",
                    "rainfall_gi_start_year": "Earliest year for GI historical baseline (default: 1990)",
                    "solar_pv_country": "Country name for SolarGIS PV data (default: India)",
                    "solar_pv_roof_size_m2": "Total roof area m² — every 10 m² = 1 kWp (default: 100)",
                    "solar_pv_roof_pct": "% of roof used for solar panels, 0–100 (default: 80)"
                }
            },
            "rainfall_stations": {
                "method": "GET",
                "path": "/api/rainfall/stations",
                "description": "List all available NOAA weather stations for rainfall analysis"
            },
            "rainfall_analysis_report": {
                "method": "POST",
                "path": "/api/reports/rainfall-analysis",
                "description": "Generate Rainfall Analysis PowerPoint report using live NOAA data (no file upload required)",
                "parameters": {
                    "station_name": "Station name string — call GET /api/rainfall/stations for valid values (required)",
                    "year": "Data year, e.g. 2023 (required)",
                    "start_month": "Start month 1–12 (default: 1)",
                    "end_month": "End month 1–12 (default: 12)",
                    "heavy_rain_threshold": "Daily mm/day threshold for Heavy rain class (default: 50.0). Extreme = 2× this value",
                    "roof_area_m2": "Roof area in m² — Terrace + Service, RC=0.90 (default: 0)",
                    "paved_area_m2": "Paved area in m² — Roads, Pathways, Hardscape, RC=0.90 (default: 0)",
                    "green_area_m2": "Green/landscape area in m² — Trees, Shrubs, Groundcover, RC=0.10 (default: 0)",
                    "water_area_m2": "Waterbody area in m², RC=0.90 (default: 0)",
                    "gi_percentile": "Percentile for GI baseline calculation. Valid options: 85, 90, 95, 98 (default: 95)",
                    "gi_start_year": "Earliest year of historical data for percentile calculation (default: 1990)"
                }
            }
        },
        "examples": {
            "climate_analysis": "curl -X POST 'http://localhost:8001/api/reports/climate-analysis' -F 'file=@weather.epw' -o report.pptx",
            "shading_analysis": "curl -X POST 'http://localhost:8001/api/reports/shading-analysis' -F 'file=@weather.epw' -o shading_report.pptx",
            "combined_analysis_default": "curl -X POST 'http://localhost:8001/api/reports/combined-analysis' -F 'file=@weather.epw' -o combined_report.pptx",
            "combined_analysis_custom": "curl -X POST 'http://localhost:8001/api/reports/combined-analysis' -F 'file=@weather.epw' -F 'temp_threshold=26' -F 'rad_threshold=300' -F 'design_cutoff_angle=50' -o combined_report.pptx",
            "combined_analysis_with_rainfall": (
                "curl -X POST 'http://localhost:8001/api/reports/combined-analysis'"
                " -F 'file=@weather.epw'"
                " -F 'rainfall_station_name=New Delhi (Safdarjung)'"
                " -F 'rainfall_year=2023'"
                " -F 'rainfall_roof_area_m2=1200'"
                " -F 'rainfall_paved_area_m2=3000'"
                " -F 'rainfall_green_area_m2=5000'"
                " -F 'rainfall_gi_percentile=95'"
                " -o combined_with_rainfall.pptx"
            ),
            "rainfall_stations": "curl 'http://localhost:8001/api/rainfall/stations'",
            "rainfall_analysis_minimal": "curl -X POST 'http://localhost:8001/api/reports/rainfall-analysis' -F 'station_name=New Delhi (Safdarjung)' -F 'year=2023' -o rainfall_report.pptx",
            "rainfall_analysis_full": (
                "curl -X POST 'http://localhost:8001/api/reports/rainfall-analysis'"
                " -F 'station_name=New Delhi (Safdarjung)'"
                " -F 'year=2023'"
                " -F 'start_month=6'"
                " -F 'end_month=9'"
                " -F 'heavy_rain_threshold=50'"
                " -F 'roof_area_m2=1200'"
                " -F 'paved_area_m2=3000'"
                " -F 'green_area_m2=5000'"
                " -F 'water_area_m2=800'"
                " -F 'gi_percentile=95'"
                " -F 'gi_start_year=1990'"
                " -o rainfall_report.pptx"
            )
        }
    }


if __name__ == "__main__":
    import uvicorn
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    print("Starting Climate Zone Finder PPT Report API...")
    print("Documentation available at: http://localhost:8001/api/docs")
    uvicorn.run(app, host="0.0.0.0", port=8001)
