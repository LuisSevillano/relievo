"""Tests for download.py — bbox extraction, buffering, pixel estimation, and download."""

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from blender_relief.download import (
    DEM_DATASETS,
    buffer_bbox,
    download_dem,
    estimate_pixels,
    extract_wgs84_bbox,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_geojson(data: dict) -> str:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".geojson", delete=False)
    json.dump(data, f)
    f.close()
    return f.name


# ---------------------------------------------------------------------------
# extract_wgs84_bbox
# ---------------------------------------------------------------------------

def test_extract_bbox_feature_collection():
    geojson = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [-16.9, 28.1], [-16.1, 28.1],
                    [-16.1, 28.6], [-16.9, 28.6], [-16.9, 28.1],
                ]],
            },
        }],
    }
    path = _write_geojson(geojson)
    try:
        west, south, east, north = extract_wgs84_bbox(path)
        assert west == pytest.approx(-16.9)
        assert south == pytest.approx(28.1)
        assert east == pytest.approx(-16.1)
        assert north == pytest.approx(28.6)
    finally:
        os.unlink(path)


def test_extract_bbox_polygon():
    geojson = {
        "type": "Polygon",
        "coordinates": [[
            [10.0, 45.0], [12.0, 45.0], [12.0, 47.0], [10.0, 47.0], [10.0, 45.0],
        ]],
    }
    path = _write_geojson(geojson)
    try:
        west, south, east, north = extract_wgs84_bbox(path)
        assert west == pytest.approx(10.0)
        assert north == pytest.approx(47.0)
    finally:
        os.unlink(path)


def test_extract_bbox_multipolygon():
    geojson = {
        "type": "MultiPolygon",
        "coordinates": [
            [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]],
            [[[2.0, 2.0], [5.0, 2.0], [5.0, 4.0], [2.0, 4.0], [2.0, 2.0]]],
        ],
    }
    path = _write_geojson(geojson)
    try:
        west, south, east, north = extract_wgs84_bbox(path)
        assert west == pytest.approx(0.0)
        assert south == pytest.approx(0.0)
        assert east == pytest.approx(5.0)
        assert north == pytest.approx(4.0)
    finally:
        os.unlink(path)


def test_extract_bbox_geometry_collection():
    geojson = {
        "type": "GeometryCollection",
        "geometries": [
            {
                "type": "Polygon",
                "coordinates": [[[1.0, 1.0], [2.0, 1.0], [2.0, 2.0], [1.0, 2.0], [1.0, 1.0]]],
            },
            {
                "type": "Polygon",
                "coordinates": [[[3.0, 3.0], [4.0, 3.0], [4.0, 5.0], [3.0, 5.0], [3.0, 3.0]]],
            },
        ],
    }
    path = _write_geojson(geojson)
    try:
        west, south, east, north = extract_wgs84_bbox(path)
        assert west == pytest.approx(1.0)
        assert south == pytest.approx(1.0)
        assert east == pytest.approx(4.0)
        assert north == pytest.approx(5.0)
    finally:
        os.unlink(path)


def test_extract_bbox_empty_raises():
    geojson = {"type": "FeatureCollection", "features": []}
    path = _write_geojson(geojson)
    try:
        with pytest.raises(ValueError, match="No coordinates"):
            extract_wgs84_bbox(path)
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# buffer_bbox
# ---------------------------------------------------------------------------

def test_buffer_bbox_basic():
    bbox = (-10.0, 35.0, -9.0, 36.0)  # 1°×1°
    buffered = buffer_bbox(bbox, 0.05)
    west, south, east, north = buffered
    assert west < -10.0
    assert south < 35.0
    assert east > -9.0
    assert north > 36.0
    # Check that values are still within WGS84 bounds
    assert west >= -180.0
    assert south >= -90.0
    assert east <= 180.0
    assert north <= 90.0


def test_buffer_bbox_zero():
    bbox = (-10.0, 35.0, -9.0, 36.0)
    assert buffer_bbox(bbox, 0.0) == bbox


def test_buffer_bbox_clamped_to_pole():
    """Buffer on a bbox near the north pole must not exceed 90°."""
    bbox = (-10.0, 89.5, 10.0, 89.9)
    _, _, _, north = buffer_bbox(bbox, 0.5)
    assert north <= 90.0


def test_buffer_bbox_clamped_to_antimeridian():
    """Buffer near the antimeridian must clamp to ±180°."""
    bbox = (178.0, 0.0, 179.9, 1.0)
    _, _, east, _ = buffer_bbox(bbox, 0.5)
    assert east <= 180.0


# ---------------------------------------------------------------------------
# estimate_pixels
# ---------------------------------------------------------------------------

def test_estimate_pixels_srtmgl1():
    """SRTMGL1 at 1 arcsec: 1°×1° bbox → ~3600×3600 pixels."""
    bbox = (0.0, 0.0, 1.0, 1.0)
    px_x, px_y = estimate_pixels(bbox, "SRTMGL1")
    assert px_x == 3600
    assert px_y == 3600


def test_estimate_pixels_srtmgl3():
    """SRTMGL3 at 3 arcsec: 1°×1° → ~1200×1200 pixels."""
    bbox = (0.0, 0.0, 1.0, 1.0)
    px_x, px_y = estimate_pixels(bbox, "SRTMGL3")
    assert px_x == 1200
    assert px_y == 1200


def test_estimate_pixels_unknown_demtype():
    """Unknown DEM type returns (None, None)."""
    bbox = (0.0, 0.0, 1.0, 1.0)
    px_x, px_y = estimate_pixels(bbox, "NONEXISTENT")
    assert px_x is None
    assert px_y is None


# ---------------------------------------------------------------------------
# DEM_DATASETS registry
# ---------------------------------------------------------------------------

def test_dem_datasets_complete():
    """Every entry in DEM_DATASETS has exactly 3 fields: name, arcsec, max_area."""
    for code, value in DEM_DATASETS.items():
        assert len(value) == 3, f"{code}: expected 3-tuple, got {value!r}"
        name, arcsec, max_area = value
        assert isinstance(name, str) and name, f"{code}: name must be a non-empty string"
        assert isinstance(arcsec, (int, float)) and arcsec > 0, f"{code}: arcsec must be positive"
        assert isinstance(max_area, int) and max_area > 0, f"{code}: max_area must be positive int"


def test_dem_datasets_not_empty():
    assert len(DEM_DATASETS) > 0


# ---------------------------------------------------------------------------
# download_dem (mocked — no real network calls)
# ---------------------------------------------------------------------------

def _mock_response(status_code=200, content_type="image/tiff", content=b"TIFF_DATA"):
    mock = MagicMock()
    mock.status_code = status_code
    mock.ok = status_code == 200
    mock.headers = {"Content-Type": content_type}
    mock.text = content.decode(errors="replace") if isinstance(content, bytes) else content
    mock.iter_content = MagicMock(return_value=iter([content]))
    return mock


@patch("blender_relief.download.requests.get")
def test_download_dem_success(mock_get, tmp_path):
    """A successful response writes the content to the output file."""
    mock_get.return_value = _mock_response(200, "image/tiff", b"FAKE_TIFF_BYTES")
    output = str(tmp_path / "dem.tif")
    result = download_dem((0.0, 0.0, 1.0, 1.0), "SRTMGL1", "MYKEY", output)
    assert result == output
    assert open(output, "rb").read() == b"FAKE_TIFF_BYTES"


@patch("blender_relief.download.requests.get")
def test_download_dem_bad_api_key(mock_get, tmp_path):
    """HTTP 401 raises RuntimeError mentioning the API key."""
    mock_get.return_value = _mock_response(401, "text/html", b"Unauthorized")
    with pytest.raises(RuntimeError, match="API key"):
        download_dem((0.0, 0.0, 1.0, 1.0), "SRTMGL1", "BADKEY",
                     str(tmp_path / "dem.tif"))


@patch("blender_relief.download.requests.get")
def test_download_dem_bbox_too_large(mock_get, tmp_path):
    """HTTP 400 raises RuntimeError mentioning bbox or dataset."""
    mock_get.return_value = _mock_response(400, "text/html", b"Bad Request")
    with pytest.raises(RuntimeError, match="400"):
        download_dem((0.0, 0.0, 1.0, 1.0), "SRTMGL1", "KEY",
                     str(tmp_path / "dem.tif"))


@patch("blender_relief.download.requests.get")
def test_download_dem_html_response(mock_get, tmp_path):
    """HTML response on HTTP 200 raises RuntimeError (API returned error page)."""
    html = b"<html><body>Error</body></html>"
    mock_get.return_value = _mock_response(200, "text/html", html)
    # text/html + ok → RuntimeError
    with pytest.raises(RuntimeError, match="HTML"):
        download_dem((0.0, 0.0, 1.0, 1.0), "SRTMGL1", "KEY",
                     str(tmp_path / "dem.tif"))


@patch("blender_relief.download.requests.get")
def test_download_dem_server_error(mock_get, tmp_path):
    """HTTP 500 raises RuntimeError."""
    mock_get.return_value = _mock_response(500, "text/plain", b"Internal Server Error")
    with pytest.raises(RuntimeError, match="500"):
        download_dem((0.0, 0.0, 1.0, 1.0), "SRTMGL1", "KEY",
                     str(tmp_path / "dem.tif"))
