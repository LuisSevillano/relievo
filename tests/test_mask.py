"""Tests for mask.py — clip mask and color relief compositing."""

import json
import subprocess
from unittest.mock import patch, MagicMock

import pytest

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    from osgeo import gdal, osr
    GDAL_AVAILABLE = True
except ImportError:
    GDAL_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not PIL_AVAILABLE or not GDAL_AVAILABLE,
    reason="PIL and GDAL are required for mask tests",
)


from blender_relief.mask import apply_clip_mask, apply_color_relief, _collect_exterior_ring


# ---------------------------------------------------------------------------
# _collect_exterior_ring (internal helper)
# ---------------------------------------------------------------------------

def test_collect_exterior_ring_polygon():
    data = {
        "type": "Polygon",
        "coordinates": [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]],
    }
    ring = _collect_exterior_ring(data)
    assert len(ring) == 5
    assert ring[0] == (0.0, 0.0)


def test_collect_exterior_ring_feature():
    data = {
        "type": "Feature",
        "properties": {},
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0], [0.0, 0.0]]],
        },
    }
    ring = _collect_exterior_ring(data)
    assert len(ring) == 5


def test_collect_exterior_ring_feature_collection():
    data = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {},
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[1.0, 1.0], [3.0, 1.0], [3.0, 3.0], [1.0, 3.0], [1.0, 1.0]]],
            },
        }],
    }
    ring = _collect_exterior_ring(data)
    assert len(ring) == 5
    assert ring[0] == (1.0, 1.0)


def test_collect_exterior_ring_multipolygon():
    data = {
        "type": "MultiPolygon",
        "coordinates": [
            [[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.0, 0.0]]],
            [[[5.0, 5.0], [6.0, 5.0], [6.0, 6.0], [5.0, 6.0], [5.0, 5.0]]],
        ],
    }
    ring = _collect_exterior_ring(data)
    # Should return the first polygon's ring
    assert ring[0] == (0.0, 0.0)


def test_collect_exterior_ring_empty():
    data = {"type": "FeatureCollection", "features": []}
    assert _collect_exterior_ring(data) == []


# ---------------------------------------------------------------------------
# apply_clip_mask
# ---------------------------------------------------------------------------

def _create_wgs84_dem(path: str, west=10.0, north=46.0, size=100):
    """Create a synthetic WGS84 DEM GeoTIFF."""
    driver = gdal.GetDriverByName("GTiff")
    ds = driver.Create(path, size, size, 1, gdal.GDT_UInt16)
    dx = 1.0 / size
    dy = -1.0 / size
    ds.SetGeoTransform([west, dx, 0.0, north, 0.0, dy])
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(4326)
    ds.SetProjection(srs.ExportToWkt())
    ds.GetRasterBand(1).Fill(32768)
    ds.FlushCache()
    ds = None


def test_clip_mask_full_coverage(tmp_path, synthetic_png):
    """A polygon covering the entire DEM extent should leave all pixels opaque."""
    dem_path = str(tmp_path / "dem.tif")
    _create_wgs84_dem(dem_path, west=10.0, north=46.0, size=100)

    # Full-coverage polygon
    geojson = {
        "type": "Polygon",
        "coordinates": [[[10.0, 45.0], [11.0, 45.0], [11.0, 46.0], [10.0, 46.0], [10.0, 45.0]]],
    }
    geo_path = str(tmp_path / "full.geojson")
    with open(geo_path, "w") as f:
        json.dump(geojson, f)

    output = str(tmp_path / "out.png")
    apply_clip_mask(synthetic_png, dem_path, geo_path, output)

    img = Image.open(output).convert("RGBA")
    assert img.mode == "RGBA"
    # All pixels should remain fully opaque
    _, _, _, alpha = img.split()
    pixels = list(alpha.getdata())
    assert all(p == 255 for p in pixels)


def test_clip_mask_inner_polygon(tmp_path, synthetic_png):
    """A polygon covering only the inner 50% should make border pixels transparent."""
    dem_path = str(tmp_path / "dem.tif")
    _create_wgs84_dem(dem_path, west=10.0, north=46.0, size=100)

    # Inner 50% polygon: lon 10.25–10.75, lat 45.25–45.75
    geojson = {
        "type": "Polygon",
        "coordinates": [[[10.25, 45.25], [10.75, 45.25],
                         [10.75, 45.75], [10.25, 45.75], [10.25, 45.25]]],
    }
    geo_path = str(tmp_path / "inner.geojson")
    with open(geo_path, "w") as f:
        json.dump(geojson, f)

    output = str(tmp_path / "out.png")
    apply_clip_mask(synthetic_png, dem_path, geo_path, output)

    img = Image.open(output).convert("RGBA")
    _, _, _, alpha = img.split()
    pixels = list(alpha.getdata())
    # Corners should be transparent
    assert pixels[0] == 0   # top-left
    assert pixels[99] == 0  # top-right


def test_clip_mask_no_polygon_raises(tmp_path, synthetic_png):
    """A GeoJSON with no polygon should raise ValueError."""
    dem_path = str(tmp_path / "dem.tif")
    _create_wgs84_dem(dem_path, west=10.0, north=46.0, size=100)

    geojson = {"type": "FeatureCollection", "features": []}
    geo_path = str(tmp_path / "empty.geojson")
    with open(geo_path, "w") as f:
        json.dump(geojson, f)

    with pytest.raises(ValueError, match="No polygon"):
        apply_clip_mask(synthetic_png, dem_path, geo_path, str(tmp_path / "out.png"))


def test_clip_mask_output_is_rgba(tmp_path, synthetic_png):
    """Output PNG must have an alpha channel (RGBA)."""
    dem_path = str(tmp_path / "dem.tif")
    _create_wgs84_dem(dem_path, west=10.0, north=46.0, size=100)

    geojson = {
        "type": "Polygon",
        "coordinates": [[[10.0, 45.0], [11.0, 45.0], [11.0, 46.0], [10.0, 46.0], [10.0, 45.0]]],
    }
    geo_path = str(tmp_path / "box.geojson")
    with open(geo_path, "w") as f:
        json.dump(geojson, f)

    output = str(tmp_path / "out.png")
    apply_clip_mask(synthetic_png, dem_path, geo_path, output)

    img = Image.open(output)
    assert img.mode == "RGBA"


def test_clip_mask_overwrite_in_place(tmp_path, synthetic_png):
    """output_path can be the same as render_png (in-place)."""
    import shutil
    dem_path = str(tmp_path / "dem.tif")
    _create_wgs84_dem(dem_path, west=10.0, north=46.0, size=100)

    geojson = {
        "type": "Polygon",
        "coordinates": [[[10.0, 45.0], [11.0, 45.0], [11.0, 46.0], [10.0, 46.0], [10.0, 45.0]]],
    }
    geo_path = str(tmp_path / "box.geojson")
    with open(geo_path, "w") as f:
        json.dump(geojson, f)

    # Copy PNG so we don't modify the fixture
    local_png = str(tmp_path / "render.png")
    shutil.copy(synthetic_png, local_png)

    apply_clip_mask(local_png, dem_path, geo_path, local_png)  # overwrite in-place
    img = Image.open(local_png)
    assert img.mode == "RGBA"


# ---------------------------------------------------------------------------
# apply_color_relief (mocked gdaldem)
# ---------------------------------------------------------------------------

def _fake_gdaldem(cmd, capture_output, **kwargs):
    """Simulate gdaldem color-relief or gdal_translate by writing a fake file."""
    # gdaldem: cmd[0]="gdaldem", output is cmd[4]
    # gdal_translate: cmd[0]="gdal_translate", output is cmd[-1]
    if cmd[0] == "gdaldem":
        output_path = cmd[4]
        # Write a 3-band RGB TIFF (no alpha, matching the fixed implementation)
        driver = gdal.GetDriverByName("GTiff")
        ds = driver.Create(output_path, 10, 10, 3, gdal.GDT_Byte)
        for i in range(1, 4):
            ds.GetRasterBand(i).Fill(200)
        ds.FlushCache()
        ds = None
    elif cmd[0] == "gdal_translate":
        output_path = cmd[-1]
        # Write a simple PNG (3-band) so PIL can open it
        from PIL import Image as _Image
        _Image.new("RGB", (10, 10), (200, 200, 200)).save(output_path, "PNG")
    mock_result = MagicMock()
    mock_result.returncode = 0
    return mock_result


@patch("blender_relief.mask.subprocess.run", side_effect=_fake_gdaldem)
def test_color_relief_produces_file(mock_run, tmp_path, synthetic_png, synthetic_dem, color_ramp_file):
    """apply_color_relief should create the output PNG."""
    output = str(tmp_path / "colored.png")
    apply_color_relief(synthetic_png, synthetic_dem, color_ramp_file, output)
    import os
    assert os.path.isfile(output)
    img = Image.open(output)
    assert img.mode == "RGB"


@patch("blender_relief.mask.subprocess.run", side_effect=_fake_gdaldem)
def test_color_relief_output_size_matches_render(mock_run, tmp_path, synthetic_png, synthetic_dem, color_ramp_file):
    """Output image dimensions must match the input render PNG."""
    output = str(tmp_path / "colored.png")
    render_w, render_h = Image.open(synthetic_png).size
    apply_color_relief(synthetic_png, synthetic_dem, color_ramp_file, output)
    out_w, out_h = Image.open(output).size
    assert (out_w, out_h) == (render_w, render_h)


@patch("blender_relief.mask.subprocess.run")
def test_color_relief_gdaldem_failure_raises(mock_run, tmp_path, synthetic_png, synthetic_dem, color_ramp_file):
    """If gdaldem exits with non-zero, RuntimeError is raised."""
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stderr = b"gdaldem: error"
    mock_run.return_value = mock_result

    with pytest.raises(RuntimeError, match="gdaldem color-relief failed"):
        apply_color_relief(synthetic_png, synthetic_dem, color_ramp_file,
                           str(tmp_path / "out.png"))
