"""Invoke Blender headless to render the shaded relief."""

import pathlib
import subprocess
import sys

from . import log


def _opt(flag: str, value) -> list:
    """Return [flag, str(value)] if value is not None, else []."""
    return [flag, str(value)] if value is not None else []


def render(
    blend_template: str,
    blender_bin: str,
    dem_path: str,
    width_m: float,
    height_m: float,
    raster_x: int,
    raster_y: int,
    exaggeration: float,
    max_size: int,
    scale: int,
    samples: int,
    output_path: str,
    light_azimuth: float = None,
    light_altitude: float = None,
) -> None:
    """Run Blender in background mode to render the shaded relief image.

    The blender_script.py bundled with this package is passed to Blender
    via --python and receives parameters after the '--' separator.
    All paths must be absolute.
    """
    script_path = pathlib.Path(__file__).parent / "blender_script.py"
    if not script_path.exists():
        raise FileNotFoundError(f"blender_script.py not found at {script_path}")

    cmd = [
        blender_bin,
        "--background",
        str(pathlib.Path(blend_template).resolve()),
        "--python",
        str(script_path),
        "--",
        "--dem-path",
        str(pathlib.Path(dem_path).resolve()),
        "--width-m",
        str(width_m),
        "--height-m",
        str(height_m),
        "--raster-x",
        str(raster_x),
        "--raster-y",
        str(raster_y),
        "--max-size",
        str(max_size or 0),
        "--scale",
        str(scale),
        *_opt("--exaggeration", exaggeration),
        *_opt("--samples", samples),
        *_opt("--light-azimuth", light_azimuth),
        *_opt("--light-altitude", light_altitude),
        "--output",
        str(pathlib.Path(output_path).resolve()),
    ]

    log.info("Rendering...")
    log.debug(f"Command: {' '.join(cmd)}")

    capture = not log._verbose
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )
    if result.returncode != 0:
        if capture:
            sys.stderr.buffer.write(result.stdout or b"")
            sys.stderr.buffer.write(result.stderr or b"")
        raise RuntimeError(
            f"Blender exited with code {result.returncode}. Check the output above for details."
        )
