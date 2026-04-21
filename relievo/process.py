"""GDAL-based DEM processing pipeline.

Replicates the logic of blenderize.sh by Nick Underwood:
  1. Optionally reproject DEM to target CRS
  2. Compute min/max elevation statistics
  3. Crop to bounding box and rescale to UInt16 (0–65535)
  4. Return real-world dimensions from geotransform
"""

from __future__ import annotations

from array import array
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
            "GDAL Python bindings not found. Install via:\n  conda install -c conda-forge gdal"
        )


@dataclass
class ProcessResult:
    dem_path: str  # UInt16 rescaled DEM for Blender
    source_dem_path: str  # DEM with real elevation values in metres
    width_m: float
    height_m: float
    raster_x: int
    raster_y: int
    src_min: float = 0.0  # minimum elevation (metres) used for UInt16 scaling
    src_max: float = 0.0  # maximum elevation (metres) used for UInt16 scaling


def reproject_bbox(
    west: float,
    south: float,
    east: float,
    north: float,
    src_crs: str,
    dst_crs: str,
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


def _smooth_dem(input_path: str, output_path: str, factor: float, workdir: str) -> None:
    """Apply a low-pass filter by downsampling and upsampling.

    Downsamples the DEM by *factor* (using average resampling) then
    upsamples back to the original resolution (bilinear).  The result
    is a spatially smoothed DEM that merges minor peaks and noise into
    broader landform structures - useful for regional/national-scale
    renders following Huffman's "less Blender-y" recommendations.

    Args:
        input_path: Source GeoTIFF (any float type).
        output_path: Destination for the smoothed GeoTIFF.
        factor: Downsampling factor (e.g. 4 → pixels 4× larger in both axes
                before upsampling back).  Values between 2 and 10 are typical.
        workdir: Directory for the intermediate coarse file.
    """
    ds = gdal.Open(input_path)
    gt = ds.GetGeoTransform()
    orig_x_res = abs(gt[1])
    orig_y_res = abs(gt[5])
    ds = None

    coarse_path = str(Path(workdir) / "_smooth_coarse.tif")
    # Step 1 - average-downsample to coarse resolution
    gdal.Warp(
        coarse_path,
        input_path,
        options=gdal.WarpOptions(
            xRes=orig_x_res * factor,
            yRes=orig_y_res * factor,
            resampleAlg="average",
            format="GTiff",
        ),
    )
    # Step 2 - bilinear-upsample back to original resolution
    gdal.Warp(
        output_path,
        coarse_path,
        options=gdal.WarpOptions(
            xRes=orig_x_res,
            yRes=orig_y_res,
            resampleAlg="bilinear",
            format="GTiff",
        ),
    )
    import os as _os

    _os.remove(coarse_path)


def _default_nodata_for_type(data_type: int):
    if data_type in (gdal.GDT_Byte, gdal.GDT_UInt16, gdal.GDT_UInt32):
        return 0
    if data_type == gdal.GDT_Int16:
        return -32768
    if data_type == gdal.GDT_Int32:
        return -2147483648
    return -9999.0


def _array_typecode_for_gdal(data_type: int) -> str:
    mapping = {
        gdal.GDT_Byte: "B",
        gdal.GDT_UInt16: "H",
        gdal.GDT_Int16: "h",
        gdal.GDT_UInt32: "I",
        gdal.GDT_Int32: "i",
        gdal.GDT_Float32: "f",
        gdal.GDT_Float64: "d",
    }
    if data_type not in mapping:
        raise RuntimeError(f"Unsupported GDAL data type for filtering: {data_type}")
    return mapping[data_type]


def _filter_dem_values(
    input_path: str,
    output_path: str,
    filter_min: float | None,
    filter_max: float | None,
) -> None:
    ds = gdal.Open(input_path)
    if ds is None:
        raise RuntimeError(f"Cannot open DEM for filtering: {input_path}")
    band = ds.GetRasterBand(1)
    data_type = band.DataType
    nodata = band.GetNoDataValue()
    if nodata is None:
        nodata = _default_nodata_for_type(data_type)

    driver = gdal.GetDriverByName("GTiff")
    out_ds = driver.Create(output_path, ds.RasterXSize, ds.RasterYSize, 1, data_type)
    out_ds.SetGeoTransform(ds.GetGeoTransform())
    out_ds.SetProjection(ds.GetProjection())
    out_band = out_ds.GetRasterBand(1)
    out_band.SetNoDataValue(nodata)

    typecode = _array_typecode_for_gdal(data_type)
    nodata_write = int(nodata) if typecode in {"B", "H", "h", "I", "i"} else float(nodata)
    xsize = ds.RasterXSize
    ysize = ds.RasterYSize

    for y in range(ysize):
        raw = band.ReadRaster(0, y, xsize, 1, buf_type=data_type)
        values = array(typecode)
        values.frombytes(raw)
        for i, v in enumerate(values):
            if filter_min is not None and v < filter_min:
                values[i] = nodata_write
                continue
            if filter_max is not None and v > filter_max:
                values[i] = nodata_write
        out_band.WriteRaster(0, y, xsize, 1, values.tobytes(), buf_type=data_type)

    out_band.FlushCache()
    out_ds.FlushCache()
    out_ds = None
    ds = None


def process_dem(
    input_dem: str,
    bbox_wgs84: tuple | None,
    target_crs: str | None,
    output_path: str,
    workdir: str,
    save_processed_dem: str | None = None,
    smooth: float | None = None,
    filter_values: tuple | None = None,
) -> ProcessResult:
    """Reproject (optional), smooth (optional), crop (optional), and rescale a DEM for Blender.

    Args:
        input_dem: Path to input DEM GeoTIFF (any CRS).
        bbox_wgs84: (west, south, east, north) in WGS84 degrees, or None to use the full DEM.
        target_crs: Target CRS string (e.g. 'EPSG:3857'), or None to skip reprojection.
        output_path: Absolute path for the output dem_blender.tif (UInt16).
        workdir: Temporary directory for intermediate files.
        save_processed_dem: If given, copy the cropped/reprojected DEM (real metres) here.
        smooth: Smoothing factor (e.g. 4). Downsamples by this factor then upsamples back
                to blur the DEM before rendering. None or 0 means no smoothing.
        filter_values: (min, max) tuple to filter elevation values. Values outside this
                range become NoData. None means no filtering.
    """
    _require_gdal()
    if target_crs:
        reprojected_path = str(Path(workdir) / "a2_reprojected.tif")
        log.info(f"Processing DEM  →  {target_crs}")
        log.debug("Reprojecting...")
        result = gdal.Warp(
            reprojected_path,
            input_dem,
            options=gdal.WarpOptions(dstSRS=target_crs, resampleAlg="bilinear", format="GTiff"),
        )
        if result is None:
            raise RuntimeError(f"gdal.Warp failed on {input_dem}")
        result = None
    else:
        reprojected_path = input_dem
        log.info("Processing DEM  (no reprojection)")

    # Smooth DEM (optional) - low-pass filter via downsample + upsample
    if smooth and smooth > 1.0:
        smoothed_path = str(Path(workdir) / "a2b_smoothed.tif")
        log.info(f"Smoothing DEM  (factor {smooth:.1f}×)…")
        _smooth_dem(reprojected_path, smoothed_path, smooth, workdir)
        reprojected_path = smoothed_path

    # Filter by elevation range (optional) - values outside range become NoData
    if filter_values is not None:
        filter_min, filter_max = filter_values
        filtered_path = str(Path(workdir) / "a2c_filtered.tif")
        min_label = "" if filter_min is None else f"{filter_min:g}"
        max_label = "" if filter_max is None else f"{filter_max:g}"
        log.info(f"Filtering DEM values: {min_label}:{max_label}")
        log.debug(f"Filtering DEM to elevation range: {filter_values}...")
        _filter_dem_values(reprojected_path, filtered_path, filter_min, filter_max)
        reprojected_path = filtered_path

    # Crop first (if bbox given) so that src_min/src_max reflect only the
    # area of interest - not the entire raster extent.
    if bbox_wgs84 is not None:
        west_wgs, south_wgs, east_wgs, north_wgs = bbox_wgs84
        if target_crs:
            west_m, south_m, east_m, north_m = reproject_bbox(
                west_wgs,
                south_wgs,
                east_wgs,
                north_wgs,
                "EPSG:4326",
                target_crs,
            )
        else:
            west_m, south_m, east_m, north_m = west_wgs, south_wgs, east_wgs, north_wgs
        proj_win = [west_m, north_m, east_m, south_m]

        cropped_path = str(Path(workdir) / "a3_cropped.tif")
        log.debug("Cropping to bbox...")
        ds_crop = gdal.Translate(
            cropped_path,
            reprojected_path,
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
    try:
        band.ComputeStatistics(False)
    except RuntimeError as exc:
        raise ValueError(
            "No valid DEM pixels after filtering/cropping. Expand the bbox or relax --filter-values."
        ) from exc
    src_min = band.GetMinimum()
    src_max = band.GetMaximum()
    if src_min is None or src_max is None:
        raise ValueError(
            "No valid DEM pixels after filtering/cropping. Expand the bbox or relax --filter-values."
        )
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
        output_path,
        stats_path,
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

    log.debug(
        f"DEM processed: {width_m:.1f} x {height_m:.1f} CRS units  ({raster_x}×{raster_y} px)"
    )
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
