"""Shared pytest fixtures for relievo tests."""

import json
import struct
import zlib

import pytest

# ---------------------------------------------------------------------------
# GeoJSON fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def canary_geojson(tmp_path):
    """GeoJSON FeatureCollection with a Canary Islands bounding polygon."""
    data = {
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
    path = tmp_path / "canary.geojson"
    path.write_text(json.dumps(data))
    return str(path)


@pytest.fixture
def italy_polygon_geojson(tmp_path):
    """GeoJSON bare Polygon (no Feature wrapper) for an Italian region."""
    data = {
        "type": "Polygon",
        "coordinates": [[
            [10.0, 45.0], [12.0, 45.0], [12.0, 47.0], [10.0, 47.0], [10.0, 45.0],
        ]],
    }
    path = tmp_path / "italy.geojson"
    path.write_text(json.dumps(data))
    return str(path)


@pytest.fixture
def inner_polygon_geojson(tmp_path):
    """GeoJSON with a polygon that covers only the inner half of a 1°×1° cell."""
    data = {
        "type": "Feature",
        "properties": {},
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [10.25, 45.25], [11.75, 45.25],
                [11.75, 46.75], [10.25, 46.75], [10.25, 45.25],
            ]],
        },
    }
    path = tmp_path / "inner.geojson"
    path.write_text(json.dumps(data))
    return str(path)


# ---------------------------------------------------------------------------
# Synthetic raster fixtures
# ---------------------------------------------------------------------------

def _make_png_bytes(width: int, height: int, color=(128, 128, 128)) -> bytes:
    """Return a minimal valid PNG byte string (RGB, 8-bit)."""
    # PNG header
    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(tag: bytes, data: bytes) -> bytes:
        length = struct.pack(">I", len(data))
        crc = struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        return length + tag + data + crc

    # IHDR
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    ihdr = chunk(b"IHDR", ihdr_data)

    # IDAT - uncompressed scanlines with filter byte 0
    r, g, b = color
    scanline = bytes([0] + [r, g, b] * width)
    raw = scanline * height
    idat = chunk(b"IDAT", zlib.compress(raw))

    # IEND
    iend = chunk(b"IEND", b"")

    return sig + ihdr + idat + iend


@pytest.fixture
def synthetic_png(tmp_path):
    """100×100 RGB PNG with a neutral grey colour."""
    path = tmp_path / "render.png"
    path.write_bytes(_make_png_bytes(100, 100, color=(180, 180, 180)))
    return str(path)


@pytest.fixture
def synthetic_dem(tmp_path):
    """100×100 single-band GeoTIFF in WGS84 with a flat elevation of 1000 m.

    Covers lon 10–11, lat 45–46 (1°×1°).
    Requires GDAL.
    """
    try:
        from osgeo import gdal, osr
    except ImportError:
        pytest.skip("GDAL not available")

    path = tmp_path / "dem.tif"
    driver = gdal.GetDriverByName("GTiff")
    ds = driver.Create(str(path), 100, 100, 1, gdal.GDT_UInt16)
    # Geotransform: top-left (10, 46), pixel size (0.01°, -0.01°)
    ds.SetGeoTransform([10.0, 0.01, 0.0, 46.0, 0.0, -0.01])
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    ds.SetProjection(srs.ExportToWkt())
    band = ds.GetRasterBand(1)
    band.Fill(32768)    # mid-range UInt16 ≈ elevation 1000 m (scaled)
    band.SetNoDataValue(0)
    ds.FlushCache()
    ds = None

    return str(path)


@pytest.fixture
def color_ramp_file(tmp_path):
    """Simple gdaldem color ramp text file."""
    content = (
        "0     70  130 180\n"
        "1000  34  139 34\n"
        "3000  255 255 255\n"
        "nv    0   0   0\n"
    )
    path = tmp_path / "ramp.txt"
    path.write_text(content)
    return str(path)


@pytest.fixture
def toml_config(tmp_path):
    """Minimal TOML config file for CLI tests."""
    content = (
        'demtype = "SRTMGL3"\n'
        "samples = 64\n"
        "scale = 50\n"
        "light_azimuth = 270.0\n"
        "light_altitude = 30.0\n"
    )
    path = tmp_path / "config.toml"
    path.write_text(content)
    return str(path)
