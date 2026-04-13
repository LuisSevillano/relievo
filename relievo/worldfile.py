"""World file generation utilities."""

from __future__ import annotations

import pathlib

from PIL import Image

try:
    from osgeo import gdal
except ImportError:  # pragma: no cover - guarded at runtime
    gdal = None  # type: ignore[assignment]


def default_worldfile_path(image_path: str) -> str:
    """Return the default world file path for an output image."""
    p = pathlib.Path(image_path)
    suffix = p.suffix.lower()
    if suffix == ".png":
        return str(p.with_suffix(".pgw"))
    if suffix in (".jpg", ".jpeg"):
        return str(p.with_suffix(".jgw"))
    return str(p.with_suffix(p.suffix + ".wld"))


def default_prj_path(image_path: str) -> str:
    """Return sidecar .prj path for an output image."""
    return str(pathlib.Path(image_path).with_suffix(".prj"))


def _legacy_worldfile_path(image_path: str) -> str:
    p = pathlib.Path(image_path)
    return str(p.with_suffix(p.suffix + ".wld"))


def write_worldfile(
    image_path: str, reference_dem_path: str, worldfile_path: str | None = None
) -> str:
    """Write a world file matching *image_path* using *reference_dem_path* georeferencing.

    The reference DEM geotransform is scaled to the final output image size.
    """
    if gdal is None:
        raise ImportError("GDAL is required to generate world files.")

    out_path = worldfile_path or default_worldfile_path(image_path)

    ds = gdal.Open(reference_dem_path)
    if ds is None:
        raise RuntimeError(f"Cannot open DEM for world file: {reference_dem_path}")

    gt = ds.GetGeoTransform()
    dem_w = ds.RasterXSize
    dem_h = ds.RasterYSize
    projection_wkt = ds.GetProjection()
    ds = None

    if dem_w <= 0 or dem_h <= 0:
        raise RuntimeError("Invalid DEM size while generating world file.")

    with Image.open(image_path) as img:
        out_w, out_h = img.size

    if out_w <= 0 or out_h <= 0:
        raise RuntimeError("Invalid output image size while generating world file.")

    sx = dem_w / out_w
    sy = dem_h / out_h

    # Pixel vectors for resized output.
    a = gt[1] * sx
    d = gt[4] * sx
    b = gt[2] * sy
    e = gt[5] * sy

    # Center of upper-left pixel.
    c = gt[0] + (a + b) / 2.0
    f = gt[3] + (d + e) / 2.0

    with open(out_path, "w", encoding="utf-8") as fp:
        fp.write(f"{a:.15f}\n")
        fp.write(f"{d:.15f}\n")
        fp.write(f"{b:.15f}\n")
        fp.write(f"{e:.15f}\n")
        fp.write(f"{c:.15f}\n")
        fp.write(f"{f:.15f}\n")

    if projection_wkt:
        with open(default_prj_path(image_path), "w", encoding="utf-8") as fp:
            fp.write(projection_wkt)

    if worldfile_path is None:
        legacy = _legacy_worldfile_path(image_path)
        if legacy != out_path:
            legacy_path = pathlib.Path(legacy)
            if legacy_path.exists():
                legacy_path.unlink()

    return out_path
