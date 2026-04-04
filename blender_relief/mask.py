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
    color_ramp: str,
    output_path: str,
) -> None:
    """Composite a hypsometric color tint over the rendered shaded relief.

    Runs ``gdaldem color-relief`` on the processed DEM, resizes the result to
    match the render dimensions, and blends it with the render using multiply
    mode. The output is an RGBA PNG (the alpha channel comes from the color
    relief, so nodata areas become transparent).

    The color ramp file follows the standard ``gdaldem color-relief`` format::

        # elevation_m  R   G   B
        0              70  130 180
        500            210 180 140
        2000           34  139 34
        4000           255 255 255
        nv             0   0   0

    Args:
        render_png: Path to the rendered PNG produced by Blender.
        dem_path: Path to the processed DEM GeoTIFF (dem_blender.tif).
        color_ramp: Path to a gdaldem color ramp text file.
        output_path: Destination path for the composited PNG.
    """
    with tempfile.TemporaryDirectory(prefix="blender-relief-cr-") as tmpdir:
        color_tif = str(pathlib.Path(tmpdir) / "color_relief.tif")

        cmd = [
            "gdaldem", "color-relief",
            dem_path, color_ramp, color_tif,
            "-alpha",
        ]
        log.debug(f"Running: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode != 0:
            raise RuntimeError(
                f"gdaldem color-relief failed:\n{result.stderr.decode(errors='replace')}"
            )

        # Load color relief (RGBA) and resize to match render
        color_img = Image.open(color_tif).convert("RGBA")
        render_img = Image.open(render_png)
        render_w, render_h = render_img.size
        color_resized = color_img.resize((render_w, render_h), Image.LANCZOS)

        # Multiply blend: render_rgb * color_rgb / 255
        render_rgb = _open_as_rgb8(render_png)
        color_rgb = color_resized.convert("RGB")
        blended = ImageChops.multiply(render_rgb, color_rgb)

        # Restore alpha from color relief (nodata → transparent)
        _, _, _, color_alpha = color_resized.split()
        blended_rgba = blended.convert("RGBA")
        blended_rgba.putalpha(color_alpha)

        blended_rgba.save(output_path, format="PNG")
    log.info(f"Color relief applied  →  {output_path}")
