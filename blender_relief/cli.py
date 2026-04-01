"""Main CLI entry point for blender-relief."""

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

import click

from . import log
from .download import DEM_DATASETS, buffer_bbox, download_dem, extract_wgs84_bbox
from .process import process_dem
from .render import render


def _print_demtypes(ctx, param, value):
    if not value or ctx.resilient_parsing:
        return
    header = f"{'Code':<20} {'Name':<38} {'Resolution':>12}  {'Max area':>18}"
    click.echo(header)
    click.echo("-" * len(header))
    for code, (name, arcsec, max_area) in DEM_DATASETS.items():
        res = f'{arcsec}" (~{arcsec * 30}m)'
        click.echo(f"{code:<20} {name:<38} {res:>12}  {max_area:>15,} km²")
    ctx.exit()


def _find_blender(blender_bin: str) -> str:
    """Resolve blender binary path. Raises UsageError if not found."""
    resolved = shutil.which(blender_bin)
    if resolved:
        return resolved
    # Also try as a direct path
    if os.path.isfile(blender_bin) and os.access(blender_bin, os.X_OK):
        return blender_bin
    raise click.UsageError(
        f"Blender executable not found: '{blender_bin}'. "
        "Install Blender and ensure it is on your PATH, or use --blender /path/to/blender."
    )


@click.command()
@click.option(
    "--list-demtypes", is_flag=True, default=False, expose_value=False,
    is_eager=True, callback=_print_demtypes,
    help="List all available DEM datasets and exit.",
)
@click.option(
    "--bbox", required=True, type=click.Path(exists=True),
    help="GeoJSON file with bounding box polygon in WGS84.",
)
@click.option(
    "--template", required=True, type=click.Path(exists=True),
    help="Path to the .blend template file.",
)
@click.option(
    "--output", required=True, type=click.Path(),
    help="Output PNG file path.",
)
@click.option(
    "--buffer", default=0.05, show_default=True, type=click.FloatRange(0.0, 1.0),
    help="Buffer applied to the bbox before downloading, as a fraction of its size (e.g. 0.05 = 5%). Ensures no data is lost at the edges after reprojection.",
)
@click.option(
    "--dem", type=click.Path(exists=True), default=None,
    help="Path to an existing DEM GeoTIFF. If omitted, downloads from OpenTopography.",
)
@click.option(
    "--crs", default=None,
    help="Target CRS for DEM reprojection (e.g. EPSG:3857, EPSG:32628). If omitted, the DEM is used as-is with no reprojection.",
)
@click.option(
    "--demtype", default="SRTMGL1", show_default=True,
    type=click.Choice(list(DEM_DATASETS.keys()), case_sensitive=True),
    help=(
        "OpenTopography dataset. Available options:\n\n"
        + "\n".join(
            f"  {code:<18} {name}  ({arcsec}\" / ~{arcsec * 30}m)"
            for code, (name, arcsec, _) in DEM_DATASETS.items()
        )
    ),
)
@click.option(
    "--api-key", envvar="OPENTOPO_API_KEY", default=None,
    help="OpenTopography API key. Can also be set via OPENTOPO_API_KEY env var.",
)
@click.option(
    "--exaggeration", default=None, type=float,
    help="Vertical exaggeration factor (Displacement node Scale). If omitted, uses the template's value. Try 0.5 to flatten, 2.0 to emphasise relief.",
)
@click.option(
    "--samples", default=None, type=int,
    help="Number of Cycles render samples. If omitted, uses the template's value.",
)
@click.option(
    "--max-size", default=None, type=int,
    help="Maximum pixels on the longest side of the render output. If omitted, uses the resolution set in the .blend template. The aspect ratio is always preserved.",
)
@click.option(
    "--scale", default=100, show_default=True, type=click.IntRange(1, 100),
    help="Render resolution percentage (1–100), applied after --max-size. Use 25 or 50 for quick previews.",
)
@click.option(
    "--blender", "blender_bin", default="blender", show_default=True,
    help="Path to the Blender executable.",
)
@click.option(
    "--verbose", is_flag=True, default=False,
    help="Print detailed progress, including GDAL steps and the full Blender log.",
)
@click.option(
    "--keep-workdir", is_flag=True, default=False,
    help="Do not delete the temporary working directory after render.",
)
def main(bbox, template, output, buffer, dem, crs, demtype, api_key, exaggeration, samples, max_size, scale, verbose, blender_bin, keep_workdir):
    """Create a shaded relief PNG from a DEM using Blender.

    Automates the Daniel Huffman shaded relief workflow:
    download DEM → process with GDAL → render in Blender.

    \b
    Examples:
      blender-relief --bbox region.geojson --template relief.blend --output out.png
      blender-relief --dem local.tif --bbox region.geojson --template relief.blend --output out.png
    """
    log.setup(verbose)

    # Validate blender early, before any slow operations
    blender_path = _find_blender(blender_bin)

    # Validate API key requirement
    if dem is None and not api_key:
        raise click.UsageError(
            "An OpenTopography API key is required when --dem is not provided. "
            "Use --api-key or set the OPENTOPO_API_KEY environment variable.\n"
            "Get a free key at: https://portal.opentopography.org/requestService?service=api"
        )

    # Resolve output to absolute path before changing directories
    output_abs = str(pathlib.Path(output).resolve())

    t_start = time.monotonic()
    workdir = tempfile.mkdtemp(prefix="blender-relief-")
    log.debug(f"Working directory: {workdir}")

    try:
        # Extract bbox coordinates from GeoJSON
        bbox_wgs84 = extract_wgs84_bbox(bbox)
        west, south, east, north = bbox_wgs84
        log.debug(f"Bounding box (WGS84): W={west:.4f} S={south:.4f} E={east:.4f} N={north:.4f}")

        # Download DEM if not provided
        if dem is None:
            raw_dem_path = os.path.join(workdir, "raw_dem.tif")
            download_bbox = buffer_bbox(bbox_wgs84, buffer) if buffer > 0 else bbox_wgs84
            log.debug(f"Buffer: {buffer*100:.0f}% applied to download bbox")
            download_dem(download_bbox, demtype, api_key, raw_dem_path)
            input_dem = raw_dem_path
        else:
            input_dem = str(pathlib.Path(dem).resolve())

        # Process DEM: reproject, crop, rescale
        dem_blender_path = os.path.join(workdir, "dem_blender.tif")
        result = process_dem(
            input_dem=input_dem,
            bbox_wgs84=bbox_wgs84,
            target_crs=crs,
            output_path=dem_blender_path,
            workdir=workdir,
        )

        # Render in Blender
        render(
            blend_template=str(pathlib.Path(template).resolve()),
            blender_bin=blender_path,
            dem_path=result.dem_path,
            width_m=result.width_m,
            height_m=result.height_m,
            raster_x=result.raster_x,
            raster_y=result.raster_y,
            exaggeration=exaggeration,
            max_size=max_size,
            scale=scale,
            samples=samples,
            output_path=output_abs,
        )

        elapsed = time.monotonic() - t_start
        m, s = divmod(int(elapsed), 60)
        duration = f"{m}m {s}s" if m else f"{s}s"
        log.info(f"Done  →  {output_abs}  ({duration})")

    finally:
        if keep_workdir:
            log.debug(f"Kept working directory: {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)
