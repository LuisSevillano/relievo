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
from .download import DEM_DATASETS, buffer_bbox, download_dem, estimate_pixels, extract_wgs84_bbox
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


def _load_config(ctx, param, value):
    """Read a TOML config file and populate ctx.default_map with its values."""
    if value is None or ctx.resilient_parsing:
        return
    try:
        import tomllib          # Python 3.11+
    except ImportError:
        try:
            import tomli as tomllib   # pip install tomli
        except ImportError:
            raise click.UsageError(
                "tomli is required to read config files on Python < 3.11. "
                "Install it with: pip install tomli"
            )
    try:
        with open(value, "rb") as f:
            data = tomllib.load(f)
    except Exception as exc:
        raise click.UsageError(f"Could not parse config file '{value}': {exc}")
    ctx.default_map = ctx.default_map or {}
    ctx.default_map.update(data)


def _find_blender(blender_bin: str) -> str:
    """Resolve blender binary path. Raises UsageError if not found."""
    resolved = shutil.which(blender_bin)
    if resolved:
        return resolved
    if os.path.isfile(blender_bin) and os.access(blender_bin, os.X_OK):
        return blender_bin
    raise click.UsageError(
        f"Blender executable not found: '{blender_bin}'. "
        "Install Blender and ensure it is on your PATH, or use --blender /path/to/blender."
    )


@click.command()
@click.option(
    "--config", type=click.Path(exists=True), is_eager=True, expose_value=False,
    callback=_load_config,
    help="TOML config file with default option values. Keys must match option names (underscores). "
         "Command-line arguments always take precedence.",
)
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
    help="Buffer applied to the bbox before downloading, as a fraction of its size (e.g. 0.05 = 5%). "
         "Ensures no data is lost at the edges after reprojection.",
)
@click.option(
    "--dem", type=click.Path(exists=True), default=None,
    help="Path to an existing DEM GeoTIFF. If omitted, downloads from OpenTopography.",
)
@click.option(
    "--save-dem", type=click.Path(), default=None,
    help="Save the downloaded DEM to this path. Useful to reuse it later with --dem and skip re-downloading.",
)
@click.option(
    "--crs", default=None,
    help="Target CRS for DEM reprojection (e.g. EPSG:3857, EPSG:32628). "
         "If omitted, the DEM is used as-is with no reprojection.",
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
    help="Vertical exaggeration factor (Displacement node Scale). "
         "If omitted, uses the template's value. Try 0.5 to flatten, 2.0 to emphasise relief.",
)
@click.option(
    "--samples", default=None, type=int,
    help="Number of Cycles render samples. If omitted, uses the template's value.",
)
@click.option(
    "--max-size", default=None, type=int,
    help="Maximum pixels on the longest side of the render output. "
         "If omitted, uses the resolution set in the .blend template. Aspect ratio is always preserved.",
)
@click.option(
    "--scale", default=100, show_default=True, type=click.IntRange(1, 100),
    help="Render resolution percentage (1–100), applied after --max-size. Use 25 or 50 for quick previews.",
)
@click.option(
    "--light-azimuth", default=None, type=float,
    help="Sun azimuth in degrees (0 = North, clockwise). If omitted, uses the template's value.",
)
@click.option(
    "--light-altitude", default=None, type=float,
    help="Sun altitude (elevation) in degrees (0 = horizon, 90 = overhead). If omitted, uses the template's value.",
)
@click.option(
    "--color-relief", "color_ramp", type=click.Path(exists=True), default=None,
    help="Path to a gdaldem color ramp file. Composites a hypsometric color tint over the rendered PNG "
         "using multiply blending. Requires gdaldem on PATH.",
)
@click.option(
    "--clip-mask", is_flag=True, default=False,
    help="Clip the output PNG to the GeoJSON polygon shape. "
         "Pixels outside the polygon become transparent (RGBA output).",
)
@click.option(
    "--dry-run", is_flag=True, default=False,
    help="Print what would be downloaded and rendered, then exit without doing anything.",
)
@click.option(
    "--no-render", is_flag=True, default=False,
    help="Download and process the DEM but skip the Blender render step. "
         "Use --save-dem to keep the processed DEM.",
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
def main(
    bbox, template, output, buffer, dem, save_dem, crs, demtype, api_key,
    exaggeration, samples, max_size, scale,
    light_azimuth, light_altitude, color_ramp, clip_mask,
    dry_run, no_render,
    verbose, blender_bin, keep_workdir,
):
    """Create a shaded relief PNG from a DEM using Blender.

    Automates the Daniel Huffman shaded relief workflow:
    download DEM → process with GDAL → render in Blender.

    \b
    Examples:
      blender-relief --bbox region.geojson --template relief.blend --output out.png
      blender-relief --dem local.tif --bbox region.geojson --template relief.blend --output out.png
      blender-relief --config myprofile.toml --bbox region.geojson --template relief.blend --output out.png
    """
    log.setup(verbose)

    # Resolve absolute paths early
    output_abs = str(pathlib.Path(output).resolve())
    bbox_abs = str(pathlib.Path(bbox).resolve())

    # --- Dry run ---
    if dry_run:
        _print_dry_run(bbox_abs, dem, demtype, crs, buffer, output_abs,
                       max_size, scale, light_azimuth, light_altitude,
                       color_ramp, clip_mask, no_render)
        return

    # Validate blender early (skip for --no-render)
    if not no_render:
        blender_path = _find_blender(blender_bin)
    else:
        blender_path = blender_bin  # not used, but keep the variable

    # Validate API key requirement
    if dem is None and not api_key:
        raise click.UsageError(
            "An OpenTopography API key is required when --dem is not provided. "
            "Use --api-key or set the OPENTOPO_API_KEY environment variable.\n"
            "Get a free key at: https://portal.opentopography.org/requestService?service=api"
        )

    if no_render and not save_dem:
        log.info("Note: --no-render is set but --save-dem was not provided. "
                 "The processed DEM will be lost when the working directory is cleaned up.")

    if no_render and clip_mask:
        log.info("Note: --clip-mask has no effect when --no-render is set (nothing to clip).")

    t_start = time.monotonic()
    workdir = tempfile.mkdtemp(prefix="blender-relief-")
    log.debug(f"Working directory: {workdir}")

    try:
        # Extract bbox coordinates from GeoJSON
        bbox_wgs84 = extract_wgs84_bbox(bbox_abs)
        west, south, east, north = bbox_wgs84
        log.debug(f"Bounding box (WGS84): W={west:.4f} S={south:.4f} E={east:.4f} N={north:.4f}")

        # Download DEM if not provided
        if dem is None:
            raw_dem_path = os.path.join(workdir, "raw_dem.tif")
            download_bbox = buffer_bbox(bbox_wgs84, buffer) if buffer > 0 else bbox_wgs84
            log.debug(f"Buffer: {buffer * 100:.0f}% applied to download bbox")
            download_dem(download_bbox, demtype, api_key, raw_dem_path)
            if save_dem:
                save_dem_abs = str(pathlib.Path(save_dem).resolve())
                shutil.copy2(raw_dem_path, save_dem_abs)
                log.info(f"DEM saved  →  {save_dem_abs}")
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

        if no_render:
            elapsed = time.monotonic() - t_start
            m, s = divmod(int(elapsed), 60)
            log.info(f"DEM processed  →  {result.dem_path}  ({m}m {s}s)" if m else
                     f"DEM processed  →  {result.dem_path}  ({s}s)")
            return

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
            light_azimuth=light_azimuth,
            light_altitude=light_altitude,
        )

        # Post-processing: color relief first, then clip mask
        if color_ramp:
            from .mask import apply_color_relief
            color_ramp_abs = str(pathlib.Path(color_ramp).resolve())
            log.info("Applying color relief...")
            apply_color_relief(output_abs, result.dem_path, color_ramp_abs, output_abs)

        if clip_mask:
            from .mask import apply_clip_mask
            log.info("Applying clip mask...")
            apply_clip_mask(output_abs, result.dem_path, bbox_abs, output_abs)

        elapsed = time.monotonic() - t_start
        m, s = divmod(int(elapsed), 60)
        duration = f"{m}m {s}s" if m else f"{s}s"
        log.info(f"Done  →  {output_abs}  ({duration})")

    finally:
        if keep_workdir:
            log.debug(f"Kept working directory: {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)


def _print_dry_run(
    bbox_abs, dem, demtype, crs, buffer, output_abs,
    max_size, scale, light_azimuth, light_altitude,
    color_ramp, clip_mask, no_render,
):
    """Print a summary of what would happen, then exit."""
    from .download import estimate_pixels, extract_wgs84_bbox, buffer_bbox, DEM_DATASETS

    click.echo("Dry run — nothing will be downloaded or rendered.")
    click.echo()

    bbox_wgs84 = extract_wgs84_bbox(bbox_abs)
    west, south, east, north = bbox_wgs84

    if dem:
        click.echo(f"  DEM source:        Local file: {dem}")
        px_info = "(use --verbose or omit --dry-run to see pixel dimensions)"
    else:
        download_bbox = buffer_bbox(bbox_wgs84, buffer) if buffer > 0 else bbox_wgs84
        bw, bs, be, bn = download_bbox
        click.echo(f"  BBox (original):   W={west:.4f}  S={south:.4f}  E={east:.4f}  N={north:.4f}")
        if buffer > 0:
            click.echo(f"  BBox (buffered):   W={bw:.4f}  S={bs:.4f}  E={be:.4f}  N={bn:.4f}  (+{buffer*100:.0f}%)")
        px_x, px_y = estimate_pixels(download_bbox, demtype)
        name, arcsec, _ = DEM_DATASETS.get(demtype, (demtype, "?", 0))
        click.echo(f"  DEM type:          {demtype}  ({name}, {arcsec}\" / ~{arcsec*30}m)")
        click.echo(f"  Estimated pixels:  {px_x} × {px_y}")
        plane_x = px_x / 1000.0
        plane_y = px_y / 1000.0
        click.echo(f"  Blender plane:     {plane_x:.3f} × {plane_y:.3f} units")

    click.echo(f"  CRS:               {crs if crs else '(none — no reprojection)'}")

    res_note = f"{max_size}px longest side" if max_size else "(from template)"
    click.echo(f"  Render resolution: {res_note}  @ {scale}%")

    if light_azimuth is not None or light_altitude is not None:
        click.echo(f"  Sun:               azimuth={light_azimuth}°  altitude={light_altitude}°")

    if color_ramp:
        click.echo(f"  Color relief:      {color_ramp}")
    if clip_mask:
        click.echo(f"  Clip mask:         enabled")
    if no_render:
        click.echo(f"  Render:            skipped (--no-render)")

    click.echo()
    click.echo(f"  Output:            {output_abs}")
