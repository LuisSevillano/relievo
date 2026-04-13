"""Tests dedicated to output image format handling."""

from unittest.mock import patch

from click.testing import CliRunner

from relievo.cli import main
from relievo.process import ProcessResult


def _process_result(tmp_path):
    dem = tmp_path / "dem_blender.tif"
    dem.write_bytes(b"")
    src = tmp_path / "source_dem.tif"
    src.write_bytes(b"")
    return ProcessResult(
        dem_path=str(dem),
        source_dem_path=str(src),
        width_m=1000.0,
        height_m=500.0,
        raster_x=100,
        raster_y=50,
    )


def test_jpg_output_is_accepted_and_passed_to_render(tmp_path):
    runner = CliRunner()
    bbox = tmp_path / "bbox.geojson"
    bbox.write_text(
        '{"type":"Polygon","coordinates":[[[10,45],[11,45],[11,46],[10,46],[10,45]]]}',
        encoding="utf-8",
    )
    template = tmp_path / "template.blend"
    template.write_bytes(b"BLEND")
    dem = tmp_path / "dem.tif"
    dem.write_bytes(b"TIFF")
    out = tmp_path / "out.jpg"

    captured = {}

    def fake_render(**kwargs):
        captured.update(kwargs)

    with (
        patch("relievo.cli._find_blender", return_value="/usr/bin/blender"),
        patch("relievo.cli.extract_wgs84_bbox", return_value=(10.0, 45.0, 11.0, 46.0)),
        patch("relievo.cli.process_dem", return_value=_process_result(tmp_path)),
        patch("relievo.cli.render", side_effect=fake_render),
    ):
        result = runner.invoke(
            main,
            [
                "--bbox",
                str(bbox),
                "--template",
                str(template),
                "--output",
                str(out),
                "--dem",
                str(dem),
            ],
        )

    assert result.exit_code == 0
    assert captured.get("output_path", "").endswith("out.jpg")


def test_invalid_output_extension_is_rejected(tmp_path):
    runner = CliRunner()
    bbox = tmp_path / "bbox.geojson"
    bbox.write_text(
        '{"type":"Polygon","coordinates":[[[10,45],[11,45],[11,46],[10,46],[10,45]]]}',
        encoding="utf-8",
    )
    template = tmp_path / "template.blend"
    template.write_bytes(b"BLEND")
    dem = tmp_path / "dem.tif"
    dem.write_bytes(b"TIFF")

    result = runner.invoke(
        main,
        [
            "--bbox",
            str(bbox),
            "--template",
            str(template),
            "--output",
            str(tmp_path / "out.tif"),
            "--dem",
            str(dem),
        ],
    )

    assert result.exit_code != 0
    assert "Unsupported output format" in result.output
