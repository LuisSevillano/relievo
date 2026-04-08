"""Post-processing operations applied to the rendered PNG.

apply_clip_mask  — clips the output to the GeoJSON polygon shape (alpha mask)
apply_color_relief — composites a hypsometric color tint over the render
"""

import json
import pathlib
import subprocess
import tempfile

from PIL import Image, ImageChops, ImageDraw

try:
    from osgeo import gdal
    _GDAL_AVAILABLE = True
except ImportError:
    gdal = None  # type: ignore[assignment]
    _GDAL_AVAILABLE = False

from . import log


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _collect_exterior_ring(data: dict) -> list:
    """Return the exterior ring of the first Polygon found in a GeoJSON dict.

    Supports Feature, FeatureCollection, Polygon, and MultiPolygon.
    Returns a list of (lon, lat) tuples, or an empty list if none found.
    """
    geom_type = data.get("type", "")
    if geom_type == "FeatureCollection":
        for feat in data.get("features", []):
            ring = _collect_exterior_ring(feat)
            if ring:
                return ring
        return []
    if geom_type == "Feature":
        return _collect_exterior_ring(data.get("geometry") or {})
    if geom_type == "Polygon":
        coords = data.get("coordinates", [])
        return [(pt[0], pt[1]) for pt in coords[0]] if coords else []
    if geom_type == "MultiPolygon":
        coords = data.get("coordinates", [])
        if coords and coords[0]:
            return [(pt[0], pt[1]) for pt in coords[0][0]]
        return []
    return []


def _open_as_rgb8(path: str) -> Image.Image:
    """Open an image file and return it as an 8-bit RGB PIL image.

    Handles 16-bit grayscale PNGs produced by Blender by rescaling the
    pixel values into the 0–255 range.
    """
    img = Image.open(path)
    if img.mode in ("I", "I;16", "I;16B"):
        # 16-bit grayscale: rescale to 8-bit
        img = img.point(lambda x: x >> 8).convert("RGB")
    elif img.mode == "L":
        img = img.convert("RGB")
    elif img.mode == "RGBA":
        img = img.convert("RGB")
    elif img.mode != "RGB":
        img = img.convert("RGB")
    return img


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def apply_clip_mask(
    render_png: str,
    dem_path: str,
    geojson_path: str,
    output_path: str,
) -> None:
    """Clip the rendered PNG to the GeoJSON polygon shape.

    Pixels outside the polygon are made fully transparent. The result is
    saved as an RGBA PNG to *output_path* (which may be the same as *render_png*).

    The polygon coordinates are assumed to be in WGS84 (EPSG:4326). If the
    processed DEM uses a projected CRS they are reprojected automatically
    using pyproj before being mapped to pixel space.

    Args:
        render_png: Path to the rendered PNG produced by Blender.
        dem_path: Path to the processed DEM GeoTIFF (dem_blender.tif).
                  Used to obtain the geotransform and CRS.
        geojson_path: Path to the GeoJSON file containing the clip polygon.
        output_path: Destination path for the masked PNG.
    """
    # --- Load polygon ---
    with open(geojson_path) as f:
        data = json.load(f)
    ring_wgs84 = _collect_exterior_ring(data)
    if not ring_wgs84:
        raise ValueError(f"No polygon found in {geojson_path}")

    # --- Read DEM geotransform and CRS ---
    ds = gdal.Open(dem_path)
    if ds is None:
        raise RuntimeError(f"Cannot open DEM: {dem_path}")
    gt = ds.GetGeoTransform()   # (x0, dx, 0, y0, 0, dy)
    dem_w = ds.RasterXSize
    dem_h = ds.RasterYSize
    dem_crs_wkt = ds.GetProjection()
    ds = None

    # --- Reproject ring if DEM is in a projected CRS ---
    if dem_crs_wkt:
        from pyproj import CRS, Transformer
        try:
            dem_crs = CRS.from_wkt(dem_crs_wkt)
            wgs84 = CRS.from_epsg(4326)
            if not dem_crs.equals(wgs84):
                transformer = Transformer.from_crs(wgs84, dem_crs, always_xy=True)
                ring = [transformer.transform(lon, lat) for lon, lat in ring_wgs84]
            else:
                ring = ring_wgs84
        except Exception:
            # If CRS parsing fails, assume WGS84
            ring = ring_wgs84
    else:
        ring = ring_wgs84

    # --- Convert geographic coords → DEM pixel coords ---
    # col = (x - x0) / dx,   row = (y - y0) / dy
    dem_pixels = [
        ((x - gt[0]) / gt[1], (y - gt[3]) / gt[5])
        for x, y in ring
    ]

    # --- Scale DEM pixels → render pixels ---
    img = Image.open(render_png)
    render_w, render_h = img.size
    scale_x = render_w / dem_w
    scale_y = render_h / dem_h
    render_pixels = [(col * scale_x, row * scale_y) for col, row in dem_pixels]

    # --- Draw mask ---
    mask = Image.new("L", (render_w, render_h), 0)
    ImageDraw.Draw(mask).polygon(render_pixels, fill=255)

    # --- Apply mask as alpha channel ---
    img_rgba = img.convert("RGBA")
    r, g, b, a = img_rgba.split()
    new_alpha = ImageChops.multiply(a, mask)
    Image.merge("RGBA", (r, g, b, new_alpha)).save(output_path, format="PNG")
    log.info(f"Clip mask applied  →  {output_path}")


def apply_color_relief(
    render_png: str,
    dem_path: str,
    dem_blender_path: str,
    color_ramp: str,
    output_path: str,
) -> None:
    """Composite a hypsometric color tint over the rendered shaded relief.

    Runs ``gdaldem color-relief`` on the source DEM (real elevation values in
    metres), resizes the result to match the render dimensions, and blends it
    with the render using multiply mode.

    The source DEM may have a larger extent than the render (e.g. download
    buffer). ``dem_blender_path`` is used as the reference to crop the source
    DEM to the exact same geographic extent before running gdaldem, ensuring
    pixel-perfect alignment.

    The color ramp file follows the standard ``gdaldem color-relief`` format::

        # elevation_m  R   G   B
        0              70  130 180
        500            210 180 140
        2000           34  139 34
        4000           255 255 255
        nv             0   0   0

    Args:
        render_png: Path to the rendered PNG produced by Blender.
        dem_path: Source DEM with real elevation values in metres.
        dem_blender_path: UInt16-rescaled DEM (same extent as render). Used
            only to read the geographic extent for cropping dem_path.
        color_ramp: Path to a gdaldem color ramp text file.
        output_path: Destination path for the composited PNG.
    """
    if not _GDAL_AVAILABLE:
        raise ImportError("GDAL is required for --color-relief. Install via conda-forge.")

    # -----------------------------------------------------------------------
    # Compute the geographic extent that Blender actually rendered.
    #
    # Blender uses an ortho camera with ortho_scale = plane_y = dem_h / 1000
    # units, which covers the full DEM height vertically. The horizontal
    # coverage depends on the render aspect ratio:
    #
    #   rendered_width_geo = dem_height_geo * (render_w / render_h)
    #
    # This may be narrower or wider than the DEM width. We use this extent to
    # crop the source DEM so gdaldem produces colours aligned pixel-perfectly
    # with the render.
    # -----------------------------------------------------------------------
    ds = gdal.Open(dem_blender_path)
    if ds is None:
        raise RuntimeError(f"Cannot open DEM: {dem_blender_path}")
    gt     = ds.GetGeoTransform()
    dem_w  = ds.RasterXSize
    dem_h  = ds.RasterYSize
    ds     = None

    pixel_x = gt[1]           # positive
    pixel_y = abs(gt[5])      # positive

    # DEM geographic extent
    dem_west  = gt[0]
    dem_north = gt[3]
    dem_east  = dem_west  + pixel_x * dem_w
    dem_south = dem_north - pixel_y * dem_h
    dem_cx    = (dem_west + dem_east) / 2   # geographic centre

    # Render pixel dimensions
    render_img = Image.open(render_png)
    render_w, render_h = render_img.size

    # Geographic width covered by the render:
    #   dem_height_geo * render_aspect = pixel_y * dem_h * render_w / render_h
    rendered_geo_width = pixel_y * dem_h * render_w / render_h

    render_west  = dem_cx - rendered_geo_width / 2
    render_east  = dem_cx + rendered_geo_width / 2
    render_north = dem_north
    render_south = dem_south

    log.debug(
        f"Render extent (geo): W={render_west:.6f} E={render_east:.6f} "
        f"N={render_north:.6f} S={render_south:.6f}"
    )

    with tempfile.TemporaryDirectory(prefix="blender-relief-cr-") as tmpdir:
        cropped_tif = str(pathlib.Path(tmpdir) / "dem_cropped.tif")
        color_tif   = str(pathlib.Path(tmpdir) / "color_relief.tif")
        color_png   = str(pathlib.Path(tmpdir) / "color_relief.png")

        # Step 1: crop source DEM to the exact geographic extent of the render,
        # resampled to the exact render pixel dimensions so no further
        # rescaling is needed.
        cmd_crop = [
            "gdalwarp",
            "-te", str(render_west), str(render_south),
                   str(render_east), str(render_north),
            "-ts", str(render_w), str(render_h),
            "-r",  "bilinear",
            dem_path, cropped_tif,
        ]
        log.debug(f"Warping DEM to render grid: {' '.join(cmd_crop)}")
        proc = subprocess.run(cmd_crop, capture_output=True)
        if proc.returncode != 0:
            raise RuntimeError(
                f"gdalwarp (crop) failed:\n{proc.stderr.decode(errors='replace')}"
            )

        # Step 2: generate color relief as RGB GeoTIFF at render pixel size
        cmd = ["gdaldem", "color-relief", cropped_tif, color_ramp, color_tif]
        log.debug(f"Running: {' '.join(cmd)}")
        proc = subprocess.run(cmd, capture_output=True)
        if proc.returncode != 0:
            raise RuntimeError(
                f"gdaldem color-relief failed:\n{proc.stderr.decode(errors='replace')}"
            )

        # Step 3: convert GeoTIFF → PNG so Pillow reads it reliably
        cmd2 = ["gdal_translate", "-of", "PNG", color_tif, color_png]
        subprocess.run(cmd2, capture_output=True, check=True)

        # Step 4: multiply-blend — no resize needed, already at render size
        render_rgb = _open_as_rgb8(render_png)
        color_rgb  = Image.open(color_png).convert("RGB")
        blended    = ImageChops.multiply(render_rgb, color_rgb)
        blended.save(output_path, format="PNG")

    log.info(f"Color relief applied  →  {output_path}")
