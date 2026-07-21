"""Pytest test suite for the chart-data API (chart_api.py)."""

import pathlib

import httpx
import pytest

from report_api import app

_EPW_PATH = pathlib.Path(__file__).parent / "IND_DL_New.Delhi-Safdarjung.AP.421820_ISHRAE2014.epw"
_EPW_BYTES = _EPW_PATH.read_bytes() if _EPW_PATH.exists() else None

_SKIP_NO_FILE = pytest.mark.skipif(
    _EPW_BYTES is None,
    reason="Bundled EPW file not found",
)

pytestmark = pytest.mark.anyio


async def _get(path: str, **kwargs):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        timeout=300,
    ) as client:
        return await client.get(path, **kwargs)


async def _post(path: str, **kwargs):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
        timeout=300,
    ) as client:
        return await client.post(path, **kwargs)


def _epw_files():
    return {"file": ("test.epw", _EPW_BYTES, "application/octet-stream")}


def _assert_chart_response(body: dict, module: str):
    assert body["module"] == module
    assert body["charts"], f"{module}: no charts returned"
    for chart in body["charts"]:
        assert chart["id"] and chart["title"]
        assert "data" in chart["figure"], f"{module}/{chart['id']}: figure missing data"
        assert "layout" in chart["figure"], f"{module}/{chart['id']}: figure missing layout"
    assert isinstance(body["stats"], dict)


# ── Registry ──────────────────────────────────────────────────────────────────

async def test_modules_registry():
    resp = await _get("/api/charts/modules")
    assert resp.status_code == 200
    registry = resp.json()
    assert len(registry) == 13
    for name, spec in registry.items():
        assert "title" in spec and "params" in spec and "requires_epw" in spec


# ── EPW-based modules ─────────────────────────────────────────────────────────

_EPW_MODULES = [
    "dbt", "humidity", "wind", "ventilation", "thermal-comfort",
    "utci", "psychrometric", "sun-path", "sun-path-3d",
    "site-analysis", "shading-designer",
]


@_SKIP_NO_FILE
@pytest.mark.parametrize("module", _EPW_MODULES)
async def test_epw_module_charts(module):
    resp = await _post(f"/api/charts/{module}", files=_epw_files())
    assert resp.status_code == 200, resp.text
    _assert_chart_response(resp.json(), module)


# ── No-EPW modules ────────────────────────────────────────────────────────────

async def test_solar_pv_charts():
    resp = await _post("/api/charts/solar-pv",
                       data={"country": "India", "roof_size_m2": 100, "roof_pct": 80})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    _assert_chart_response(body, "solar-pv")
    assert body["stats"]["system_kwp"] == pytest.approx(8.0)


async def test_solar_pv_countries():
    resp = await _get("/api/charts/solar-pv/countries")
    assert resp.status_code == 200
    assert "India" in resp.json()["countries"]


@pytest.mark.network
async def test_rainfall_charts():
    """Live NOAA fetch — deselect with `-m "not network"` when offline."""
    resp = await _post("/api/charts/rainfall",
                       data={"station_name": "New Delhi (Safdarjung)", "year": 2023})
    assert resp.status_code == 200, resp.text
    _assert_chart_response(resp.json(), "rainfall")


async def test_rainfall_stations_endpoint():
    resp = await _get("/api/charts/rainfall/stations")
    assert resp.status_code == 200
    assert "New Delhi (Safdarjung)" in resp.json()["stations"]


# ── Validation errors ─────────────────────────────────────────────────────────

@_SKIP_NO_FILE
async def test_wind_invalid_sectors():
    resp = await _post("/api/charts/wind", files=_epw_files(),
                       data={"n_sectors": 7})
    assert resp.status_code == 400


@_SKIP_NO_FILE
async def test_invalid_month_range():
    resp = await _post("/api/charts/dbt", files=_epw_files(),
                       data={"start_month": 0, "end_month": 13})
    assert resp.status_code == 400


async def test_rainfall_unknown_station():
    resp = await _post("/api/charts/rainfall",
                       data={"station_name": "Nowhere", "year": 2023})
    assert resp.status_code == 400


async def test_solar_pv_unknown_country():
    resp = await _post("/api/charts/solar-pv", data={"country": "Atlantis"})
    assert resp.status_code == 400


async def test_empty_epw_rejected():
    resp = await _post("/api/charts/wind",
                       files={"file": ("empty.epw", b"", "application/octet-stream")})
    assert resp.status_code == 400
