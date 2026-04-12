"""Tests for process.py - DEM processing pipeline (requires GDAL)."""

import pytest

from relievo.process import ProcessResult, process_dem, reproject_bbox

try:
    from osgeo import gdal, osr

    GDAL_AVAILABLE = True
except ImportError:
    GDAL_AVAILABLE = False

pytestmark = pytest.mark.skipif(not GDAL_AVAILABLE, reason="GDAL not available")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_dem_tif(
    path: str,
    width=100,
    height=100,
    west=10.0,
    north=46.0,
    dx=0.01,
    dy=-0.01,
    epsg=4326,
    fill_value=32768,
):
    """Create a minimal synthetic DEM GeoTIFF for testing."""
    driver = gdal.GetDriverByName("GTiff")
    ds = driver.Create(path, width, height, 1, gdal.GDT_UInt16)
    ds.SetGeoTransform([west, dx, 0.0, north, 0.0, dy])
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(epsg)
    ds.SetProjection(srs.ExportToWkt())
    band = ds.GetRasterBand(1)
    band.Fill(fill_value)
    band.SetNoDataValue(0)
    ds.FlushCache()
    ds = None


# ---------------------------------------------------------------------------
# reproject_bbox
# ---------------------------------------------------------------------------


def test_reproject_bbox_identity():
    """Reprojecting WGS84 → WGS84 should not change coordinates."""
    west, south, east, north = reproject_bbox(10.0, 45.0, 12.0, 47.0, "EPSG:4326", "EPSG:4326")
    assert west == pytest.approx(10.0, abs=1e-4)
    assert south == pytest.approx(45.0, abs=1e-4)
    assert east == pytest.approx(12.0, abs=1e-4)
    assert north == pytest.approx(47.0, abs=1e-4)


def test_reproject_bbox_to_3857():
    """Reprojecting a known bbox to EPSG:3857 should yield known approximate values."""
    # lon=0°, lat=0° → (0, 0) in EPSG:3857
    west, south, east, north = reproject_bbox(0.0, -1.0, 1.0, 1.0, "EPSG:4326", "EPSG:3857")
    assert west == pytest.approx(0.0, abs=1.0)  # ~0 m
    assert south < 0.0  # negative northing
    assert east > 0.0  # positive easting
    assert north > 0.0


def test_reproject_bbox_ordering():
    """The returned bbox should always satisfy west<east and south<north."""
    west, south, east, north = reproject_bbox(-10.0, 35.0, -9.0, 36.0, "EPSG:4326", "EPSG:3857")
    assert west < east
    assert south < north


# ---------------------------------------------------------------------------
# process_dem - no CRS (no reprojection)
# ---------------------------------------------------------------------------


def test_process_dem_no_crs(tmp_path):
    """process_dem without CRS should return a valid ProcessResult."""
    dem_path = str(tmp_path / "input.tif")
    output_path = str(tmp_path / "output.tif")
    _make_dem_tif(dem_path, width=100, height=100, west=10.0, north=46.0, dx=0.01, dy=-0.01)

    bbox_wgs84 = (10.2, 45.2, 10.8, 45.8)
    result = process_dem(
        input_dem=dem_path,
        bbox_wgs84=bbox_wgs84,
        target_crs=None,
        output_path=output_path,
        workdir=str(tmp_path),
    )

    assert isinstance(result, ProcessResult)
    assert result.dem_path == output_path
    assert result.source_dem_path  # must be populated
    assert result.raster_x > 0
    assert result.raster_y > 0
    assert result.width_m > 0
    assert result.height_m > 0
    # Output file should exist and be non-empty
    import os

    assert os.path.isfile(output_path)
    assert os.path.getsize(output_path) > 0


def test_process_dem_output_is_uint16(tmp_path):
    """Output DEM should be UInt16 (as required by Blender's 16-bit pipeline)."""
    dem_path = str(tmp_path / "input.tif")
    output_path = str(tmp_path / "output.tif")
    _make_dem_tif(dem_path, fill_value=5000)

    bbox_wgs84 = (10.2, 45.2, 10.8, 45.8)
    process_dem(
        input_dem=dem_path,
        bbox_wgs84=bbox_wgs84,
        target_crs=None,
        output_path=output_path,
        workdir=str(tmp_path),
    )

    ds = gdal.Open(output_path)
    band = ds.GetRasterBand(1)
    assert band.DataType == gdal.GDT_UInt16
    ds = None


def test_process_dem_with_crs(tmp_path):
    """process_dem with target_crs should reproject and still return a valid result."""
    dem_path = str(tmp_path / "input.tif")
    output_path = str(tmp_path / "output.tif")
    _make_dem_tif(dem_path, width=100, height=100, west=10.0, north=46.0, dx=0.01, dy=-0.01)

    bbox_wgs84 = (10.2, 45.2, 10.8, 45.8)
    result = process_dem(
        input_dem=dem_path,
        bbox_wgs84=bbox_wgs84,
        target_crs="EPSG:3857",
        output_path=output_path,
        workdir=str(tmp_path),
    )

    assert isinstance(result, ProcessResult)
    assert result.raster_x > 0
    assert result.raster_y > 0
    # In EPSG:3857 the width/height are in metres, which should be >> 1
    assert result.width_m > 1000


def test_process_dem_missing_input_raises(tmp_path):
    """process_dem with a non-existent input DEM should raise an error."""
    with pytest.raises(Exception):
        process_dem(
            input_dem=str(tmp_path / "nonexistent.tif"),
            bbox_wgs84=(10.0, 45.0, 11.0, 46.0),
            target_crs=None,
            output_path=str(tmp_path / "output.tif"),
            workdir=str(tmp_path),
        )


def test_process_dem_pixel_dimensions_reasonable(tmp_path):
    """Cropping a 1°×1° DEM to its own extent should produce ~100×100 pixels."""
    dem_path = str(tmp_path / "input.tif")
    output_path = str(tmp_path / "output.tif")
    _make_dem_tif(dem_path, width=100, height=100, west=10.0, north=46.0, dx=0.01, dy=-0.01)

    # Crop to a sub-region
    bbox_wgs84 = (10.1, 45.1, 10.9, 45.9)
    result = process_dem(
        input_dem=dem_path,
        bbox_wgs84=bbox_wgs84,
        target_crs=None,
        output_path=output_path,
        workdir=str(tmp_path),
    )

    # Should be approximately 80×80 pixels (0.8° of a 100px/1° raster)
    assert 60 <= result.raster_x <= 100
    assert 60 <= result.raster_y <= 100
