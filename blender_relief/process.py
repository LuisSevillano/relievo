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
    dem_path: str
    width_m: float
    height_m: float
    raster_x: int
    raster_y: int


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
    bbox_wgs84: tuple,
    target_crs: str | None,
    output_path: str,
    workdir: str,
) -> ProcessResult:
    """Reproject (optional), crop, and rescale a DEM for Blender rendering.

    Args:
        input_dem: Path to input DEM GeoTIFF (any CRS).
        bbox_wgs84: (west, south, east, north) in WGS84 degrees.
        target_crs: Target CRS string (e.g. 'EPSG:3857'), or None to skip reprojection.
        output_path: Absolute path for the output dem_blender.tif.
        workdir: Temporary directory for intermediate files.
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

    log.debug("Computing elevation statistics...")
    ds = gdal.Open(reprojected_path)
    band = ds.GetRasterBand(1)
    band.ComputeStatistics(False)
    src_min = band.GetMinimum()
    src_max = band.GetMaximum()
    ds = None

    west_wgs, south_wgs, east_wgs, north_wgs = bbox_wgs84
    if target_crs:
        west_m, south_m, east_m, north_m = reproject_bbox(
            west_wgs, south_wgs, east_wgs, north_wgs, "EPSG:4326", target_crs,
        )
    else:
        west_m, south_m, east_m, north_m = west_wgs, south_wgs, east_wgs, north_wgs

    log.debug("Cropping and rescaling to 16-bit...")
    ds = gdal.Translate(
        output_path, reprojected_path,
        options=gdal.TranslateOptions(
            outputType=gdal.GDT_UInt16,
            scaleParams=[[src_min, src_max, 0, 65535]],
            projWin=[west_m, north_m, east_m, south_m],
            format="GTiff",
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
        width_m=width_m,
        height_m=height_m,
        raster_x=raster_x,
        raster_y=raster_y,
    )
