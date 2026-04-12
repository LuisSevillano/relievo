"""GDAL-based DEM processing pipeline.

Replicates the logic of blenderize.sh by Nick Underwood:
  1. Optionally reproject DEM to target CRS
  2. Compute min/max elevation statistics
  3. Crop to bounding box and rescale to UInt16 (0–65535)
  4. Return real-world dimensions from geotransform
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

try:
    from osgeo import gdal
    gdal.UseExceptions()
    _GDAL_AVAILABLE = True
except ImportError:
    gdal = None  # type: ignore[assignment]
    _GDAL_AVAILABLE = False

from pyproj import Transformer

from . import log


def _require_gdal():
    if not _GDAL_AVAILABLE:
        raise ImportError(
            "GDAL Python bindings not found. Install via:\n"
            "  conda install -c conda-forge gdal"
        )


@dataclass
class ProcessResult:
    dem_path: str        # UInt16 rescaled DEM for Blender
    source_dem_path: str # DEM with real elevation values in metres
    width_m: float
    height_m: float
    raster_x: int
    raster_y: int
    src_min: float = 0.0   # minimum elevation (metres) used for UInt16 scaling
    src_max: float = 0.0   # maximum elevation (metres) used for UInt16 scaling


def reproject_bbox(
    west: float, south: float, east: float, north: float,
    src_crs: str, dst_crs: str,
) -> tuple:
    """Reproject bbox corners from src_crs to dst_crs.

    Transforms all four corners to handle non-rectangular projections.
    Returns (west, south, east, north) in dst_crs units.
    """
    transformer = Transformer.from_crs(src_crs, dst_crs, always_xy=True)
    corners = [
        transformer.transform(west, south),
        transformer.transform(east, south),
        transformer.transform(west, north),
        transformer.transform(east, north),
    ]
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    return (min(xs), min(ys), max(xs), max(ys))


def process_dem(
    input_dem: str,
    bbox_wgs84: tuple | None,
    target_crs: str | None,
    output_path: str,
    workdir: str,
    save_processed_dem: str | None = None,
) -> ProcessResult:
    """Reproject (optional), crop (optional), and rescale a DEM for Blender rendering.

    Args:
        input_dem: Path to input DEM GeoTIFF (any CRS).
        bbox_wgs84: (west, south, east, north) in WGS84 degrees, or None to use the full DEM.
        target_crs: Target CRS string (e.g. 'EPSG:3857'), or None to skip reprojection.
        output_path: Absolute path for the output dem_blender.tif (UInt16).
        workdir: Temporary directory for intermediate files.
        save_processed_dem: If given, copy the cropped/reprojected DEM (real metres) here.
    """
    _require_gdal()
    if target_crs:
        reprojected_path = str(Path(workdir) / "a2_reprojected.tif")
        log.info(f"Processing DEM  →  {target_crs}")
        log.debug("Reprojecting...")
        result = gdal.Warp(
            reprojected_path, input_dem,
            options=gdal.WarpOptions(dstSRS=target_crs, resampleAlg="bilinear", format="GTiff"),
        )
        if result is None:
            raise RuntimeError(f"gdal.Warp failed on {input_dem}")
        result = None
    else:
        reprojected_path = input_dem
        log.info("Processing DEM  (no reprojection)")

    # Crop first (if bbox given) so that src_min/src_max reflect only the
    # area of interest — not the entire raster extent.
    if bbox_wgs84 is not None:
        west_wgs, south_wgs, east_wgs, north_wgs = bbox_wgs84
        if target_crs:
            west_m, south_m, east_m, north_m = reproject_bbox(
                west_wgs, south_wgs, east_wgs, north_wgs, "EPSG:4326", target_crs,
            )
        else:
            west_m, south_m, east_m, north_m = west_wgs, south_wgs, east_wgs, north_wgs
        proj_win = [west_m, north_m, east_m, south_m]

        cropped_path = str(Path(workdir) / "a3_cropped.tif")
        log.debug("Cropping to bbox...")
        ds_crop = gdal.Translate(
            cropped_path, reprojected_path,
            options=gdal.TranslateOptions(projWin=proj_win, format="GTiff"),
        )
        if ds_crop is None:
            raise RuntimeError("gdal.Translate (crop) failed")
        ds_crop = None
        stats_path = cropped_path
    else:
        proj_win = None
        stats_path = reprojected_path

    log.debug("Computing elevation statistics on cropped area...")
    ds = gdal.Open(stats_path)
    band = ds.GetRasterBand(1)
    band.ComputeStatistics(False)
    src_min = band.GetMinimum()
    src_max = band.GetMaximum()
    ds = None

    # Optionally save the processed (cropped + reprojected, real metres) DEM
    if save_processed_dem:
        import shutil as _shutil
        _shutil.copy2(stats_path, save_processed_dem)
        log.info(f"Processed DEM saved  →  {save_processed_dem}")

    log.debug(f"Elevation range: {src_min:.1f} – {src_max:.1f} m")
    log.debug("Rescaling to 16-bit...")

    # Map real data to [1, 65535], reserving UInt16=0 as the nodata sentinel.
    # This avoids collisions between nodata pixels and actual minimum-elevation
    # pixels, and prevents the "nodata rounded/clamped" GDAL warning that occurs
    # when the source nodata value (e.g. -32768 in SRTM15Plus) is out of the
    # UInt16 range.
    ds = gdal.Translate(
        output_path, stats_path,
        options=gdal.TranslateOptions(
            outputType=gdal.GDT_UInt16,
            scaleParams=[[src_min, src_max, 1, 65535]],
            format="GTiff",
            noData=0,
        ),
    )
    if ds is None:
        raise RuntimeError(f"gdal.Translate failed, output: {output_path}")

    gt = ds.GetGeoTransform()
    raster_x = ds.RasterXSize
    raster_y = ds.RasterYSize
    width_m = abs(gt[1]) * raster_x
    height_m = abs(gt[5]) * raster_y
    ds = None

    log.debug(f"DEM processed: {width_m:.1f} x {height_m:.1f} CRS units  ({raster_x}×{raster_y} px)")
    return ProcessResult(
        dem_path=output_path,
        source_dem_path=stats_path,  # real elevation values in metres
        width_m=width_m,
        height_m=height_m,
        raster_x=raster_x,
        raster_y=raster_y,
        src_min=src_min,
        src_max=src_max,
    )
