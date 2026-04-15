import json

from click.testing import CliRunner

from relievo.cli import main


def test_dry_run_shows_area_and_dataset_limit(tmp_path):
    bbox = {
        "type": "Polygon",
        "coordinates": [
            [[-10.0, -10.0], [120.0, -10.0], [120.0, 60.0], [-10.0, 60.0], [-10.0, -10.0]]
        ],
    }
    bbox_path = tmp_path / "bbox.geojson"
    bbox_path.write_text(json.dumps(bbox))

    template_path = tmp_path / "template.blend"
    template_path.write_bytes(b"BLEND")

    output_path = tmp_path / "out.png"

    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--bbox",
            str(bbox_path),
            "--template",
            str(template_path),
            "--output",
            str(output_path),
            "--api-key",
            "TESTKEY",
            "--demtype",
            "SRTMGL1",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert "Estimated area" in result.output
    assert "Dataset limit" in result.output
    assert "Warning" in result.output
