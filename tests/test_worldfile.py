"""Tests dedicated to world file output option."""

import json
from unittest.mock import patch

from click.testing import CliRunner
from PIL import Image

from relievo import worldfile
from relievo.cli import main
from relievo.process import ProcessResult


def _make_process_result(tmp_path):
    dem = tmp_path / "dem_blender.tif"
    dem.write_bytes(b"")
    src = tmp_path / "source_dem.tif"
    src.write_bytes(b"")
    return ProcessResult(
        dem_path=str(dem),
        source_dem_path=str(src),
        width_m=1_000.0,
        height_m=600.0,
        raster_x=200,
        raster_y=120,
    )


def test_worldfile_calls_writer(tmp_path):
    runner = CliRunner()
    bbox = tmp_path / "bbox.geojson"
    bbox.write_text(
        json.dumps(
            {
                "type": "Polygon",
                "coordinates": [
                    [[10.0, 45.0], [11.0, 45.0], [11.0, 46.0], [10.0, 46.0], [10.0, 45.0]]
                ],
            }
        )
    )
    template = tmp_path / "template.blend"
    template.write_bytes(b"BLEND")
    dem = tmp_path / "dem.tif"
    dem.write_bytes(b"TIFF")

    with (
        patch("relievo.cli._find_blender", return_value="/usr/bin/blender"),
        patch("relievo.cli.extract_wgs84_bbox", return_value=(10.0, 45.0, 11.0, 46.0)),
        patch("relievo.cli.process_dem", return_value=_make_process_result(tmp_path)),
        patch("relievo.cli.render"),
        patch(
            "relievo.worldfile.write_worldfile", return_value=str(tmp_path / "out.png.wld")
        ) as mock_wf,
    ):
        result = runner.invoke(
            main,
            [
                "--bbox",
                str(bbox),
                "--template",
                str(template),
                "--output",
                str(tmp_path / "out.png"),
                "--dem",
                str(dem),
                "--worldfile",
            ],
        )

    assert result.exit_code == 0
    mock_wf.assert_called_once()


def test_worldfile_warns_with_no_render(tmp_path):
    runner = CliRunner()
    bbox = tmp_path / "bbox.geojson"
    bbox.write_text(
        json.dumps(
            {
                "type": "Polygon",
                "coordinates": [
                    [[10.0, 45.0], [11.0, 45.0], [11.0, 46.0], [10.0, 46.0], [10.0, 45.0]]
                ],
            }
        )
    )
    template = tmp_path / "template.blend"
    template.write_bytes(b"BLEND")
    dem = tmp_path / "dem.tif"
    dem.write_bytes(b"TIFF")

    with (
        patch("relievo.cli.extract_wgs84_bbox", return_value=(10.0, 45.0, 11.0, 46.0)),
        patch("relievo.cli.process_dem", return_value=_make_process_result(tmp_path)),
        patch("relievo.worldfile.write_worldfile") as mock_wf,
    ):
        result = runner.invoke(
            main,
            [
                "--bbox",
                str(bbox),
                "--template",
                str(template),
                "--output",
                str(tmp_path / "out.png"),
                "--dem",
                str(dem),
                "--no-render",
                "--worldfile",
            ],
        )

    assert result.exit_code == 0
    assert "worldfile" in result.output.lower()
    mock_wf.assert_not_called()


def test_worldfile_writes_standard_extension_and_prj(tmp_path, monkeypatch):
    class _FakeDS:
        RasterXSize = 4
        RasterYSize = 2

        def GetGeoTransform(self):
            return (10.0, 1.0, 0.0, 50.0, 0.0, -1.0)

        def GetProjection(self):
            return 'GEOGCS["WGS 84"]'

    class _FakeGDAL:
        @staticmethod
        def Open(_):
            return _FakeDS()

    monkeypatch.setattr(worldfile, "gdal", _FakeGDAL)

    image_path = tmp_path / "out.jpg"
    Image.new("RGB", (2, 1), (255, 255, 255)).save(image_path)
    legacy = tmp_path / "out.jpg.wld"
    legacy.write_text("legacy")

    wf_path = worldfile.write_worldfile(str(image_path), "dummy.tif")
    assert wf_path.endswith(".jgw")
    assert (tmp_path / "out.jgw").is_file()
    assert (tmp_path / "out.prj").is_file()
    assert not legacy.exists()
