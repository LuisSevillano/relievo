"""Tests for mask.py - clip mask and color relief compositing."""

import json
from unittest.mock import MagicMock, patch

import pytest

from relievo.mask import _collect_exterior_ring, apply_clip_mask, apply_color_relief

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
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[1.0, 1.0], [3.0, 1.0], [3.0, 3.0], [1.0, 3.0], [1.0, 1.0]]],
                },
            }
        ],
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
        "coordinates": [
            [[10.25, 45.25], [10.75, 45.25], [10.75, 45.75], [10.25, 45.75], [10.25, 45.25]]
        ],
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
    assert pixels[0] == 0  # top-left
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

    # Copy PNG to a different path so we can overwrite it in-place
    local_png = str(tmp_path / "render_copy.png")
    shutil.copy(synthetic_png, local_png)

    apply_clip_mask(local_png, dem_path, geo_path, local_png)  # overwrite in-place
    img = Image.open(local_png)
    assert img.mode == "RGBA"


# ---------------------------------------------------------------------------
# apply_color_relief (mocked gdaldem)
# ---------------------------------------------------------------------------


def _fake_gdaldem(cmd, capture_output=False, **kwargs):
    """Simulate gdaldem / gdal_translate subprocess calls."""
    output_path = cmd[-1]
    mock_result = MagicMock()
    mock_result.returncode = 0

    if cmd[0] == "gdaldem":
        # color-relief → write a 3-band RGB TIFF same size as input DEM
        # cmd layout: ["gdaldem", "color-relief", dem_path, ramp_path, out_path]
        in_ds = gdal.Open(cmd[2])  # dem_path is cmd[2], NOT cmd[3] (which is the ramp)
        out_w = in_ds.RasterXSize if in_ds else 10
        out_h = in_ds.RasterYSize if in_ds else 10
        if in_ds:
            in_ds = None
        driver = gdal.GetDriverByName("GTiff")
        ds = driver.Create(output_path, out_w, out_h, 3, gdal.GDT_Byte)
        for i in range(1, 4):
            ds.GetRasterBand(i).Fill(200)
        ds.FlushCache()
        ds = None
    elif cmd[0] == "gdal_translate":
        # PNG conversion step
        from PIL import Image as _Image

        in_ds = gdal.Open(cmd[-2]) if not cmd[-2].endswith(".png") else None
        out_w = in_ds.RasterXSize if in_ds else 10
        out_h = in_ds.RasterYSize if in_ds else 10
        if in_ds:
            in_ds = None
        _Image.new("RGB", (out_w, out_h), (200, 200, 200)).save(output_path, "PNG")

    return mock_result


@patch("relievo.mask.subprocess.run", side_effect=_fake_gdaldem)
def test_color_relief_produces_file(
    mock_run, tmp_path, synthetic_png, synthetic_dem, color_ramp_file
):
    """apply_color_relief debe crear el PNG de salida."""
    output = str(tmp_path / "colored.png")
    apply_color_relief(
        synthetic_png,
        synthetic_dem,
        synthetic_dem,
        color_ramp_file,
        output,
        src_min=0.0,
        src_max=3000.0,
    )
    import os

    assert os.path.isfile(output)
    img = Image.open(output)
    assert img.mode == "RGB"


@patch("relievo.mask.subprocess.run", side_effect=_fake_gdaldem)
def test_color_relief_output_size_matches_render(
    mock_run, tmp_path, synthetic_png, synthetic_dem, color_ramp_file
):
    """El PNG de salida debe tener las mismas dimensiones que el render."""
    output = str(tmp_path / "colored.png")
    render_w, render_h = Image.open(synthetic_png).size
    apply_color_relief(
        synthetic_png,
        synthetic_dem,
        synthetic_dem,
        color_ramp_file,
        output,
        src_min=0.0,
        src_max=3000.0,
    )
    out_w, out_h = Image.open(output).size
    assert (out_w, out_h) == (render_w, render_h)


@patch("relievo.mask.subprocess.run", side_effect=_fake_gdaldem)
def test_color_relief_mode_separate(
    mock_run, tmp_path, synthetic_png, synthetic_dem, color_ramp_file
):
    """mode='separate' saves the raw colour PNG to <stem>_color.png; render is untouched."""
    import os

    output = str(tmp_path / "colored.png")
    color_output = str(tmp_path / "colored_color.png")
    apply_color_relief(
        synthetic_png,
        synthetic_dem,
        synthetic_dem,
        color_ramp_file,
        output,
        src_min=0.0,
        src_max=3000.0,
        mode="separate",
    )
    # render PNG must NOT be created/overwritten
    assert not os.path.isfile(output), "separate mode must not write to the render output path"
    # colour layer must exist at <stem>_color.png
    assert os.path.isfile(color_output)
    img = Image.open(color_output)
    assert img.mode == "RGB"


@patch("relievo.mask.subprocess.run", side_effect=_fake_gdaldem)
def test_color_relief_mode_both(mock_run, tmp_path, synthetic_png, synthetic_dem, color_ramp_file):
    """mode='both' genera el PNG combinado y el PNG de color por separado."""
    import os

    output = str(tmp_path / "colored.png")
    apply_color_relief(
        synthetic_png,
        synthetic_dem,
        synthetic_dem,
        color_ramp_file,
        output,
        src_min=0.0,
        src_max=3000.0,
        mode="both",
    )
    assert os.path.isfile(output)
    color_only = str(tmp_path / "colored_color.png")
    assert os.path.isfile(color_only)


@patch("relievo.mask.subprocess.run", side_effect=_fake_gdaldem)
def test_color_relief_blend_linearburn(
    mock_run, tmp_path, synthetic_png, synthetic_dem, color_ramp_file
):
    """blend_mode='linearburn' should generate a valid combined output."""
    import os

    output = str(tmp_path / "colored_linearburn.png")
    apply_color_relief(
        synthetic_png,
        synthetic_dem,
        synthetic_dem,
        color_ramp_file,
        output,
        src_min=0.0,
        src_max=3000.0,
        mode="overlay",
        blend_mode="linearburn",
    )
    assert os.path.isfile(output)
    img = Image.open(output)
    assert img.mode == "RGB"


@patch("relievo.mask.subprocess.run")
def test_color_relief_gdaldem_failure_raises(
    mock_run, tmp_path, synthetic_png, synthetic_dem, color_ramp_file
):
    """Si gdaldem falla, debe lanzar RuntimeError."""

    def side_effect(cmd, capture_output=False, **kwargs):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = b"gdaldem: error"
        return mock_result

    mock_run.side_effect = side_effect
    with pytest.raises(RuntimeError, match="gdaldem color-relief failed"):
        apply_color_relief(
            synthetic_png,
            synthetic_dem,
            synthetic_dem,
            color_ramp_file,
            str(tmp_path / "out.png"),
            src_min=0.0,
            src_max=3000.0,
        )
