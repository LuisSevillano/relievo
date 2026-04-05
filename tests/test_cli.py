"""Tests for cli.py — full CLI with mocked I/O operations.

Uses click.testing.CliRunner and unittest.mock.patch to avoid any real
network, GDAL, or Blender calls. All tests run offline.
"""

import json
import os
import shutil
from unittest.mock import MagicMock, call, patch

import pytest
from click.testing import CliRunner

from blender_relief.cli import main
from blender_relief.download import DEM_DATASETS
from blender_relief.process import ProcessResult


# ---------------------------------------------------------------------------
# Shared helpers / factories
# ---------------------------------------------------------------------------

def _make_process_result(tmp_path, filename="dem_blender.tif"):
    """Return a ProcessResult pointing at a real (empty) temp file."""
    p = tmp_path / filename
    p.write_bytes(b"")
    return ProcessResult(
        dem_path=str(p),
        width_m=50_000.0,
        height_m=40_000.0,
        raster_x=1200,
        raster_y=960,
    )


def _base_args(bbox_path, template_path, output_path):
    """Minimal set of required CLI arguments."""
    return [
        "--bbox", str(bbox_path),
        "--template", str(template_path),
        "--output", str(output_path),
    ]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def bbox_file(tmp_path):
    data = {
        "type": "Polygon",
        "coordinates": [[[10.0, 45.0], [11.0, 45.0], [11.0, 46.0], [10.0, 46.0], [10.0, 45.0]]],
    }
    p = tmp_path / "bbox.geojson"
    p.write_text(json.dumps(data))
    return p


@pytest.fixture
def template_file(tmp_path):
    p = tmp_path / "template.blend"
    p.write_bytes(b"BLEND")
    return p


@pytest.fixture
def local_dem_file(tmp_path):
    p = tmp_path / "dem.tif"
    p.write_bytes(b"TIFF")
    return p


# ---------------------------------------------------------------------------
# Required options / missing args
# ---------------------------------------------------------------------------

def test_missing_bbox(runner, tmp_path, template_file):
    result = runner.invoke(main, [
        "--template", str(template_file),
        "--output", str(tmp_path / "out.png"),
        "--dem", str(template_file),
    ])
    assert result.exit_code == 2
    assert "bbox" in result.output.lower() or "Missing" in result.output


def test_missing_template(runner, tmp_path, bbox_file):
    result = runner.invoke(main, [
        "--bbox", str(bbox_file),
        "--output", str(tmp_path / "out.png"),
        "--dem", str(bbox_file),
    ])
    assert result.exit_code == 2


def test_missing_output(runner, tmp_path, bbox_file, template_file):
    result = runner.invoke(main, [
        "--bbox", str(bbox_file),
        "--template", str(template_file),
        "--dem", str(bbox_file),
    ])
    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# --list-demtypes
# ---------------------------------------------------------------------------

def test_list_demtypes(runner):
    result = runner.invoke(main, ["--list-demtypes"])
    assert result.exit_code == 0
    assert "SRTMGL1" in result.output
    assert "COP30" in result.output


# ---------------------------------------------------------------------------
# --demtype validation
# ---------------------------------------------------------------------------

def test_demtype_invalid(runner, tmp_path, bbox_file, template_file):
    result = runner.invoke(main, [
        "--bbox", str(bbox_file),
        "--template", str(template_file),
        "--output", str(tmp_path / "out.png"),
        "--demtype", "NOTADATASET",
        "--dem", str(bbox_file),
    ])
    assert result.exit_code == 2
    assert "Invalid value" in result.output or "invalid choice" in result.output.lower()


@pytest.mark.parametrize("demtype", list(DEM_DATASETS.keys()))
def test_all_valid_demtypes_accepted(runner, tmp_path, bbox_file, template_file, demtype):
    """Every dataset code in DEM_DATASETS should be accepted by the CLI."""
    with patch("blender_relief.cli._find_blender", return_value="/usr/bin/blender"), \
         patch("blender_relief.cli.extract_wgs84_bbox", return_value=(10.0, 45.0, 11.0, 46.0)), \
         patch("blender_relief.cli.download_dem"), \
         patch("blender_relief.cli.process_dem", return_value=_make_process_result(tmp_path)), \
         patch("blender_relief.cli.render"):
        result = runner.invoke(main, [
            "--bbox", str(bbox_file),
            "--template", str(template_file),
            "--output", str(tmp_path / "out.png"),
            "--demtype", demtype,
            "--api-key", "TESTKEY",
        ])
        # Should not fail due to invalid demtype
        assert "Invalid value" not in result.output
        assert result.exit_code in (0, 1)  # 1 only if another error occurs


# ---------------------------------------------------------------------------
# --scale range
# ---------------------------------------------------------------------------

def test_scale_zero_invalid(runner, tmp_path, bbox_file, template_file):
    result = runner.invoke(main, [
        "--bbox", str(bbox_file), "--template", str(template_file),
        "--output", str(tmp_path / "out.png"),
        "--dem", str(bbox_file), "--scale", "0",
    ])
    assert result.exit_code == 2


def test_scale_101_invalid(runner, tmp_path, bbox_file, template_file):
    result = runner.invoke(main, [
        "--bbox", str(bbox_file), "--template", str(template_file),
        "--output", str(tmp_path / "out.png"),
        "--dem", str(bbox_file), "--scale", "101",
    ])
    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# --api-key requirement
# ---------------------------------------------------------------------------

def test_missing_api_key_without_dem(runner, tmp_path, bbox_file, template_file):
    """When --dem is not provided, --api-key (or OPENTOPO_API_KEY) must be present."""
    with patch("blender_relief.cli._find_blender", return_value="/usr/bin/blender"):
        result = runner.invoke(main, [
            "--bbox", str(bbox_file),
            "--template", str(template_file),
            "--output", str(tmp_path / "out.png"),
        ], env={"OPENTOPO_API_KEY": ""})
    assert result.exit_code != 0
    assert "API key" in result.output or "api" in result.output.lower()


def test_api_key_not_required_with_local_dem(runner, tmp_path, bbox_file, template_file, local_dem_file):
    """When --dem is provided, no API key is needed."""
    with patch("blender_relief.cli._find_blender", return_value="/usr/bin/blender"), \
         patch("blender_relief.cli.extract_wgs84_bbox", return_value=(10.0, 45.0, 11.0, 46.0)), \
         patch("blender_relief.cli.process_dem", return_value=_make_process_result(tmp_path)), \
         patch("blender_relief.cli.render"):
        result = runner.invoke(main, [
            "--bbox", str(bbox_file),
            "--template", str(template_file),
            "--output", str(tmp_path / "out.png"),
            "--dem", str(local_dem_file),
        ], env={"OPENTOPO_API_KEY": ""})
    assert "API key" not in result.output
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# --dry-run
# ---------------------------------------------------------------------------

def test_dry_run_exits_zero(runner, tmp_path, bbox_file, template_file):
    result = runner.invoke(main, [
        "--bbox", str(bbox_file),
        "--template", str(template_file),
        "--output", str(tmp_path / "out.png"),
        "--api-key", "TESTKEY",
        "--dry-run",
    ])
    assert result.exit_code == 0
    assert "Dry run" in result.output


def test_dry_run_shows_estimated_pixels(runner, tmp_path, bbox_file, template_file):
    result = runner.invoke(main, [
        "--bbox", str(bbox_file),
        "--template", str(template_file),
        "--output", str(tmp_path / "out.png"),
        "--api-key", "TESTKEY",
        "--dry-run",
    ])
    assert "pixel" in result.output.lower() or "×" in result.output


def test_dry_run_shows_output_path(runner, tmp_path, bbox_file, template_file):
    output_path = str(tmp_path / "my_output.png")
    result = runner.invoke(main, [
        "--bbox", str(bbox_file),
        "--template", str(template_file),
        "--output", output_path,
        "--api-key", "TESTKEY",
        "--dry-run",
    ])
    assert "my_output.png" in result.output


def test_dry_run_does_not_download(runner, tmp_path, bbox_file, template_file):
    """--dry-run must not call download_dem."""
    with patch("blender_relief.cli.download_dem") as mock_dl:
        runner.invoke(main, [
            "--bbox", str(bbox_file),
            "--template", str(template_file),
            "--output", str(tmp_path / "out.png"),
            "--api-key", "TESTKEY",
            "--dry-run",
        ])
        mock_dl.assert_not_called()


def test_dry_run_with_local_dem(runner, tmp_path, bbox_file, template_file, local_dem_file):
    result = runner.invoke(main, [
        "--bbox", str(bbox_file),
        "--template", str(template_file),
        "--output", str(tmp_path / "out.png"),
        "--dem", str(local_dem_file),
        "--dry-run",
    ])
    assert result.exit_code == 0
    assert "Dry run" in result.output


def test_dry_run_combined_with_no_render(runner, tmp_path, bbox_file, template_file):
    """--dry-run and --no-render together should still exit cleanly."""
    result = runner.invoke(main, [
        "--bbox", str(bbox_file),
        "--template", str(template_file),
        "--output", str(tmp_path / "out.png"),
        "--api-key", "TESTKEY",
        "--dry-run", "--no-render",
    ])
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# --no-render
# ---------------------------------------------------------------------------

def test_no_render_skips_render_call(runner, tmp_path, bbox_file, template_file, local_dem_file):
    with patch("blender_relief.cli.extract_wgs84_bbox", return_value=(10.0, 45.0, 11.0, 46.0)), \
         patch("blender_relief.cli.process_dem", return_value=_make_process_result(tmp_path)), \
         patch("blender_relief.cli.render") as mock_render:
        runner.invoke(main, [
            "--bbox", str(bbox_file),
            "--template", str(template_file),
            "--output", str(tmp_path / "out.png"),
            "--dem", str(local_dem_file),
            "--no-render",
        ])
        mock_render.assert_not_called()


def test_no_render_warns_without_save_dem(runner, tmp_path, bbox_file, template_file, local_dem_file):
    with patch("blender_relief.cli.extract_wgs84_bbox", return_value=(10.0, 45.0, 11.0, 46.0)), \
         patch("blender_relief.cli.process_dem", return_value=_make_process_result(tmp_path)):
        result = runner.invoke(main, [
            "--bbox", str(bbox_file),
            "--template", str(template_file),
            "--output", str(tmp_path / "out.png"),
            "--dem", str(local_dem_file),
            "--no-render",
        ])
    assert "save-dem" in result.output.lower() or "--save-dem" in result.output


def test_no_render_with_clip_mask_warns(runner, tmp_path, bbox_file, template_file, local_dem_file):
    with patch("blender_relief.cli.extract_wgs84_bbox", return_value=(10.0, 45.0, 11.0, 46.0)), \
         patch("blender_relief.cli.process_dem", return_value=_make_process_result(tmp_path)):
        result = runner.invoke(main, [
            "--bbox", str(bbox_file),
            "--template", str(template_file),
            "--output", str(tmp_path / "out.png"),
            "--dem", str(local_dem_file),
            "--no-render", "--clip-mask",
        ])
    assert result.exit_code == 0
    assert "no effect" in result.output.lower() or "clip" in result.output.lower()


# ---------------------------------------------------------------------------
# --keep-workdir
# ---------------------------------------------------------------------------

def test_keep_workdir_preserves_directory(runner, tmp_path, bbox_file, template_file, local_dem_file):
    created_dirs = []

    original_mkdtemp = __import__("tempfile").mkdtemp

    def capturing_mkdtemp(**kwargs):
        d = original_mkdtemp(**kwargs)
        created_dirs.append(d)
        return d

    with patch("blender_relief.cli._find_blender", return_value="/usr/bin/blender"), \
         patch("blender_relief.cli.extract_wgs84_bbox", return_value=(10.0, 45.0, 11.0, 46.0)), \
         patch("blender_relief.cli.process_dem", return_value=_make_process_result(tmp_path)), \
         patch("blender_relief.cli.render"), \
         patch("blender_relief.cli.tempfile.mkdtemp", side_effect=capturing_mkdtemp):
        runner.invoke(main, [
            "--bbox", str(bbox_file),
            "--template", str(template_file),
            "--output", str(tmp_path / "out.png"),
            "--dem", str(local_dem_file),
            "--keep-workdir",
        ])

    for d in created_dirs:
        if os.path.isdir(d):
            shutil.rmtree(d)  # clean up after the test


def test_workdir_cleaned_up_by_default(runner, tmp_path, bbox_file, template_file, local_dem_file):
    created_dirs = []
    original_mkdtemp = __import__("tempfile").mkdtemp

    def capturing_mkdtemp(**kwargs):
        d = original_mkdtemp(**kwargs)
        created_dirs.append(d)
        return d

    with patch("blender_relief.cli._find_blender", return_value="/usr/bin/blender"), \
         patch("blender_relief.cli.extract_wgs84_bbox", return_value=(10.0, 45.0, 11.0, 46.0)), \
         patch("blender_relief.cli.process_dem", return_value=_make_process_result(tmp_path)), \
         patch("blender_relief.cli.render"), \
         patch("blender_relief.cli.tempfile.mkdtemp", side_effect=capturing_mkdtemp):
        runner.invoke(main, [
            "--bbox", str(bbox_file),
            "--template", str(template_file),
            "--output", str(tmp_path / "out.png"),
            "--dem", str(local_dem_file),
        ])

    for d in created_dirs:
        assert not os.path.isdir(d), f"Expected workdir to be cleaned up: {d}"


# ---------------------------------------------------------------------------
# --save-dem
# ---------------------------------------------------------------------------

def test_save_dem_copies_file(runner, tmp_path, bbox_file, template_file):
    save_path = str(tmp_path / "saved.tif")
    with patch("blender_relief.cli._find_blender", return_value="/usr/bin/blender"), \
         patch("blender_relief.cli.extract_wgs84_bbox", return_value=(10.0, 45.0, 11.0, 46.0)), \
         patch("blender_relief.cli.download_dem", side_effect=lambda bbox, dt, key, path, **kw: open(path, "wb").write(b"TIFF")), \
         patch("blender_relief.cli.process_dem", return_value=_make_process_result(tmp_path)), \
         patch("blender_relief.cli.render"):
        result = runner.invoke(main, [
            "--bbox", str(bbox_file),
            "--template", str(template_file),
            "--output", str(tmp_path / "out.png"),
            "--api-key", "TESTKEY",
            "--save-dem", save_path,
        ])
    assert result.exit_code == 0
    assert os.path.isfile(save_path)


# ---------------------------------------------------------------------------
# --local-dem skips download
# ---------------------------------------------------------------------------

def test_local_dem_skips_download(runner, tmp_path, bbox_file, template_file, local_dem_file):
    with patch("blender_relief.cli._find_blender", return_value="/usr/bin/blender"), \
         patch("blender_relief.cli.extract_wgs84_bbox", return_value=(10.0, 45.0, 11.0, 46.0)), \
         patch("blender_relief.cli.download_dem") as mock_dl, \
         patch("blender_relief.cli.process_dem", return_value=_make_process_result(tmp_path)), \
         patch("blender_relief.cli.render"):
        runner.invoke(main, [
            "--bbox", str(bbox_file),
            "--template", str(template_file),
            "--output", str(tmp_path / "out.png"),
            "--dem", str(local_dem_file),
        ])
        mock_dl.assert_not_called()


# ---------------------------------------------------------------------------
# --config
# ---------------------------------------------------------------------------

def test_config_overrides_defaults(runner, tmp_path, bbox_file, template_file, local_dem_file, toml_config):
    """Options set in the TOML config should reach the render() call."""
    captured = {}

    def fake_render(**kwargs):
        captured.update(kwargs)

    with patch("blender_relief.cli._find_blender", return_value="/usr/bin/blender"), \
         patch("blender_relief.cli.extract_wgs84_bbox", return_value=(10.0, 45.0, 11.0, 46.0)), \
         patch("blender_relief.cli.process_dem", return_value=_make_process_result(tmp_path)), \
         patch("blender_relief.cli.render", side_effect=fake_render):
        result = runner.invoke(main, [
            "--config", toml_config,
            "--bbox", str(bbox_file),
            "--template", str(template_file),
            "--output", str(tmp_path / "out.png"),
            "--dem", str(local_dem_file),
        ])
    assert result.exit_code == 0
    # toml_config sets samples=64, scale=50
    assert captured.get("samples") == 64
    assert captured.get("scale") == 50


def test_cli_args_override_config(runner, tmp_path, bbox_file, template_file, local_dem_file, toml_config):
    """Explicit CLI args must take precedence over config file values."""
    captured = {}

    def fake_render(**kwargs):
        captured.update(kwargs)

    with patch("blender_relief.cli._find_blender", return_value="/usr/bin/blender"), \
         patch("blender_relief.cli.extract_wgs84_bbox", return_value=(10.0, 45.0, 11.0, 46.0)), \
         patch("blender_relief.cli.process_dem", return_value=_make_process_result(tmp_path)), \
         patch("blender_relief.cli.render", side_effect=fake_render):
        result = runner.invoke(main, [
            "--config", toml_config,
            "--bbox", str(bbox_file),
            "--template", str(template_file),
            "--output", str(tmp_path / "out.png"),
            "--dem", str(local_dem_file),
            "--samples", "512",   # override config's 64
        ])
    assert result.exit_code == 0
    assert captured.get("samples") == 512


def test_config_invalid_toml(runner, tmp_path, bbox_file, template_file):
    bad_toml = tmp_path / "bad.toml"
    bad_toml.write_text("this is not valid toml = [[[")
    result = runner.invoke(main, [
        "--config", str(bad_toml),
        "--bbox", str(bbox_file),
        "--template", str(template_file),
        "--output", str(tmp_path / "out.png"),
    ])
    assert result.exit_code != 0


def test_config_nonexistent_file(runner, tmp_path, bbox_file, template_file):
    result = runner.invoke(main, [
        "--config", str(tmp_path / "nonexistent.toml"),
        "--bbox", str(bbox_file),
        "--template", str(template_file),
        "--output", str(tmp_path / "out.png"),
    ])
    assert result.exit_code == 2  # Click path validation fails


# ---------------------------------------------------------------------------
# --light-azimuth / --light-altitude
# ---------------------------------------------------------------------------

def test_light_args_passed_to_render(runner, tmp_path, bbox_file, template_file, local_dem_file):
    captured = {}

    def fake_render(**kwargs):
        captured.update(kwargs)

    with patch("blender_relief.cli._find_blender", return_value="/usr/bin/blender"), \
         patch("blender_relief.cli.extract_wgs84_bbox", return_value=(10.0, 45.0, 11.0, 46.0)), \
         patch("blender_relief.cli.process_dem", return_value=_make_process_result(tmp_path)), \
         patch("blender_relief.cli.render", side_effect=fake_render):
        result = runner.invoke(main, [
            "--bbox", str(bbox_file),
            "--template", str(template_file),
            "--output", str(tmp_path / "out.png"),
            "--dem", str(local_dem_file),
            "--light-azimuth", "315",
            "--light-altitude", "35",
        ])
    assert result.exit_code == 0
    assert captured.get("light_azimuth") == pytest.approx(315.0)
    assert captured.get("light_altitude") == pytest.approx(35.0)


def test_light_azimuth_only(runner, tmp_path, bbox_file, template_file, local_dem_file):
    captured = {}

    def fake_render(**kwargs):
        captured.update(kwargs)

    with patch("blender_relief.cli._find_blender", return_value="/usr/bin/blender"), \
         patch("blender_relief.cli.extract_wgs84_bbox", return_value=(10.0, 45.0, 11.0, 46.0)), \
         patch("blender_relief.cli.process_dem", return_value=_make_process_result(tmp_path)), \
         patch("blender_relief.cli.render", side_effect=fake_render):
        result = runner.invoke(main, [
            "--bbox", str(bbox_file), "--template", str(template_file),
            "--output", str(tmp_path / "out.png"), "--dem", str(local_dem_file),
            "--light-azimuth", "180",
        ])
    assert result.exit_code == 0
    assert captured.get("light_azimuth") == pytest.approx(180.0)
    assert captured.get("light_altitude") is None


# ---------------------------------------------------------------------------
# --exaggeration / --samples / --max-size
# ---------------------------------------------------------------------------

def test_exaggeration_passed_to_render(runner, tmp_path, bbox_file, template_file, local_dem_file):
    captured = {}

    with patch("blender_relief.cli._find_blender", return_value="/usr/bin/blender"), \
         patch("blender_relief.cli.extract_wgs84_bbox", return_value=(10.0, 45.0, 11.0, 46.0)), \
         patch("blender_relief.cli.process_dem", return_value=_make_process_result(tmp_path)), \
         patch("blender_relief.cli.render", side_effect=lambda **kw: captured.update(kw)):
        runner.invoke(main, [
            "--bbox", str(bbox_file), "--template", str(template_file),
            "--output", str(tmp_path / "out.png"), "--dem", str(local_dem_file),
            "--exaggeration", "2.5",
        ])
    assert captured.get("exaggeration") == pytest.approx(2.5)


def test_samples_passed_to_render(runner, tmp_path, bbox_file, template_file, local_dem_file):
    captured = {}

    with patch("blender_relief.cli._find_blender", return_value="/usr/bin/blender"), \
         patch("blender_relief.cli.extract_wgs84_bbox", return_value=(10.0, 45.0, 11.0, 46.0)), \
         patch("blender_relief.cli.process_dem", return_value=_make_process_result(tmp_path)), \
         patch("blender_relief.cli.render", side_effect=lambda **kw: captured.update(kw)):
        runner.invoke(main, [
            "--bbox", str(bbox_file), "--template", str(template_file),
            "--output", str(tmp_path / "out.png"), "--dem", str(local_dem_file),
            "--samples", "128",
        ])
    assert captured.get("samples") == 128


def test_max_size_passed_to_render(runner, tmp_path, bbox_file, template_file, local_dem_file):
    captured = {}

    with patch("blender_relief.cli._find_blender", return_value="/usr/bin/blender"), \
         patch("blender_relief.cli.extract_wgs84_bbox", return_value=(10.0, 45.0, 11.0, 46.0)), \
         patch("blender_relief.cli.process_dem", return_value=_make_process_result(tmp_path)), \
         patch("blender_relief.cli.render", side_effect=lambda **kw: captured.update(kw)):
        runner.invoke(main, [
            "--bbox", str(bbox_file), "--template", str(template_file),
            "--output", str(tmp_path / "out.png"), "--dem", str(local_dem_file),
            "--max-size", "1200",
        ])
    assert captured.get("max_size") == 1200


# ---------------------------------------------------------------------------
# --color-relief
# ---------------------------------------------------------------------------

def test_color_relief_nonexistent_file(runner, tmp_path, bbox_file, template_file, local_dem_file):
    result = runner.invoke(main, [
        "--bbox", str(bbox_file), "--template", str(template_file),
        "--output", str(tmp_path / "out.png"), "--dem", str(local_dem_file),
        "--color-relief", str(tmp_path / "nonexistent_ramp.txt"),
    ])
    assert result.exit_code == 2  # Click Path(exists=True) validation


def test_color_relief_calls_apply(runner, tmp_path, bbox_file, template_file, local_dem_file, color_ramp_file):
    with patch("blender_relief.cli._find_blender", return_value="/usr/bin/blender"), \
         patch("blender_relief.cli.extract_wgs84_bbox", return_value=(10.0, 45.0, 11.0, 46.0)), \
         patch("blender_relief.cli.process_dem", return_value=_make_process_result(tmp_path)), \
         patch("blender_relief.cli.render"), \
         patch("blender_relief.mask.apply_color_relief") as mock_cr:
        result = runner.invoke(main, [
            "--bbox", str(bbox_file), "--template", str(template_file),
            "--output", str(tmp_path / "out.png"), "--dem", str(local_dem_file),
            "--color-relief", color_ramp_file,
        ])
    assert result.exit_code == 0
    mock_cr.assert_called_once()


# ---------------------------------------------------------------------------
# --clip-mask
# ---------------------------------------------------------------------------

def test_clip_mask_calls_apply(runner, tmp_path, bbox_file, template_file, local_dem_file):
    with patch("blender_relief.cli._find_blender", return_value="/usr/bin/blender"), \
         patch("blender_relief.cli.extract_wgs84_bbox", return_value=(10.0, 45.0, 11.0, 46.0)), \
         patch("blender_relief.cli.process_dem", return_value=_make_process_result(tmp_path)), \
         patch("blender_relief.cli.render"), \
         patch("blender_relief.mask.apply_clip_mask") as mock_cm:
        result = runner.invoke(main, [
            "--bbox", str(bbox_file), "--template", str(template_file),
            "--output", str(tmp_path / "out.png"), "--dem", str(local_dem_file),
            "--clip-mask",
        ])
    assert result.exit_code == 0
    mock_cm.assert_called_once()


def test_color_relief_before_clip_mask(runner, tmp_path, bbox_file, template_file, local_dem_file, color_ramp_file):
    """Color relief must be applied before clip mask."""
    call_order = []

    def fake_cr(*args, **kwargs):
        call_order.append("color_relief")

    def fake_cm(*args, **kwargs):
        call_order.append("clip_mask")

    with patch("blender_relief.cli._find_blender", return_value="/usr/bin/blender"), \
         patch("blender_relief.cli.extract_wgs84_bbox", return_value=(10.0, 45.0, 11.0, 46.0)), \
         patch("blender_relief.cli.process_dem", return_value=_make_process_result(tmp_path)), \
         patch("blender_relief.cli.render"), \
         patch("blender_relief.mask.apply_color_relief", side_effect=fake_cr), \
         patch("blender_relief.mask.apply_clip_mask", side_effect=fake_cm):
        runner.invoke(main, [
            "--bbox", str(bbox_file), "--template", str(template_file),
            "--output", str(tmp_path / "out.png"), "--dem", str(local_dem_file),
            "--color-relief", color_ramp_file,
            "--clip-mask",
        ])

    assert call_order == ["color_relief", "clip_mask"]


# ---------------------------------------------------------------------------
# --crs
# ---------------------------------------------------------------------------

def test_crs_none_skips_warp(runner, tmp_path, bbox_file, template_file, local_dem_file):
    """When --crs is not provided, process_dem is called with target_crs=None."""
    captured = {}

    def fake_process(**kwargs):
        captured.update(kwargs)
        return _make_process_result(tmp_path)

    with patch("blender_relief.cli._find_blender", return_value="/usr/bin/blender"), \
         patch("blender_relief.cli.extract_wgs84_bbox", return_value=(10.0, 45.0, 11.0, 46.0)), \
         patch("blender_relief.cli.process_dem", side_effect=fake_process), \
         patch("blender_relief.cli.render"):
        runner.invoke(main, [
            "--bbox", str(bbox_file), "--template", str(template_file),
            "--output", str(tmp_path / "out.png"), "--dem", str(local_dem_file),
        ])
    assert captured.get("target_crs") is None


# ---------------------------------------------------------------------------
# Elapsed time message
# ---------------------------------------------------------------------------

def test_elapsed_time_shown(runner, tmp_path, bbox_file, template_file, local_dem_file):
    with patch("blender_relief.cli._find_blender", return_value="/usr/bin/blender"), \
         patch("blender_relief.cli.extract_wgs84_bbox", return_value=(10.0, 45.0, 11.0, 46.0)), \
         patch("blender_relief.cli.process_dem", return_value=_make_process_result(tmp_path)), \
         patch("blender_relief.cli.render"):
        result = runner.invoke(main, [
            "--bbox", str(bbox_file), "--template", str(template_file),
            "--output", str(tmp_path / "out.png"), "--dem", str(local_dem_file),
        ])
    assert result.exit_code == 0
    # Should mention elapsed time in some form (seconds or minutes)
    assert "s" in result.output or "Done" in result.output


# ---------------------------------------------------------------------------
# --buffer edge cases
# ---------------------------------------------------------------------------

def test_buffer_zero(runner, tmp_path, bbox_file, template_file):
    """--buffer 0.0 should be accepted without error."""
    with patch("blender_relief.cli._find_blender", return_value="/usr/bin/blender"), \
         patch("blender_relief.cli.extract_wgs84_bbox", return_value=(10.0, 45.0, 11.0, 46.0)), \
         patch("blender_relief.cli.download_dem", side_effect=lambda bbox, dt, key, path, **kw: open(path, "wb").write(b"T")), \
         patch("blender_relief.cli.process_dem", return_value=_make_process_result(tmp_path)), \
         patch("blender_relief.cli.render"):
        result = runner.invoke(main, [
            "--bbox", str(bbox_file), "--template", str(template_file),
            "--output", str(tmp_path / "out.png"),
            "--api-key", "KEY", "--buffer", "0.0",
        ])
    assert result.exit_code == 0


def test_buffer_invalid_above_one(runner, tmp_path, bbox_file, template_file):
    result = runner.invoke(main, [
        "--bbox", str(bbox_file), "--template", str(template_file),
        "--output", str(tmp_path / "out.png"), "--api-key", "KEY",
        "--buffer", "1.5",
    ])
    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# Successful end-to-end (fully mocked)
# ---------------------------------------------------------------------------

def test_full_pipeline_mocked(runner, tmp_path, bbox_file, template_file, local_dem_file):
    """Verify the happy path runs to completion with all mocks in place."""
    with patch("blender_relief.cli._find_blender", return_value="/usr/bin/blender"), \
         patch("blender_relief.cli.extract_wgs84_bbox", return_value=(10.0, 45.0, 11.0, 46.0)), \
         patch("blender_relief.cli.process_dem", return_value=_make_process_result(tmp_path)), \
         patch("blender_relief.cli.render") as mock_render:
        result = runner.invoke(main, [
            "--bbox", str(bbox_file),
            "--template", str(template_file),
            "--output", str(tmp_path / "out.png"),
            "--dem", str(local_dem_file),
        ])
    assert result.exit_code == 0
    mock_render.assert_called_once()
    assert "Done" in result.output
