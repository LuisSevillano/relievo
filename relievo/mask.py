"""Post-processing operations applied to the rendered PNG.

apply_clip_mask  - clips the output to the GeoJSON polygon shape (alpha mask)
apply_color_relief - composites a hypsometric color tint over the render
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


def _rescale_ramp(ramp_path: str, src_min: float, src_max: float, out_path: str) -> None:
    """Write a new gdaldem color ramp file with elevations rescaled to UInt16 [1–65535].

    ``dem_blender.tif`` stores rescaled values where:
        uint16 = 1 + (elev - src_min) / (src_max - src_min) * 65534

    UInt16 value 0 is reserved as the nodata sentinel (pixels with no
    elevation data, e.g. outside the DEM extent). The ``nv`` entry in the
    colour ramp handles those.

    Lines starting with ``#`` are preserved as comments.
    """
    scale = src_max - src_min if (src_max - src_min) != 0 else 1.0
    lines_out = []
    with open(ramp_path) as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                lines_out.append(line)
                continue
            parts = stripped.split()
            if parts[0].lower() == "nv":
                lines_out.append(line)
                continue
            try:
                elev = float(parts[0])
            except ValueError:
                lines_out.append(line)
                continue
            uint16_val = 1.0 + (elev - src_min) / scale * 65534.0
            uint16_val = max(1.0, min(65535.0, uint16_val))
            rest = " ".join(parts[1:])
            lines_out.append(f"{uint16_val:.2f} {rest}\n")
    with open(out_path, "w") as f:
        f.writelines(lines_out)


def apply_color_relief(
    render_png: str,
    dem_path: str,
    dem_blender_path: str,
    color_ramp: str,
    output_path: str,
    src_min: float = 0.0,
    src_max: float = 65535.0,
    mode: str = "overlay",
) -> None:
    """Genera un tint hipsométrico a partir de dem_blender.tif y lo combina con el render.

    Ejecuta ``gdaldem color-relief`` sobre ``dem_blender_path`` (el raster
    exacto que Blender ha desplazado) usando un ramp reescalado a UInt16.
    El resultado se redimensiona al tamaño del render y se combina según ``mode``.

    El fichero de ramp usa elevaciones en metros (formato estándar gdaldem).
    Se reescalan automáticamente a UInt16 usando ``src_min`` / ``src_max``::

        # elevation_m  R   G   B
        0              70  130 180
        500            210 180 140
        2000           34  139 34
        4000           255 255 255
        nv             0   0   0

    Args:
        render_png: Ruta al PNG renderizado por Blender.
        dem_path: No usado (compatibilidad). Puede pasarse ``""`` o cualquier valor.
        dem_blender_path: DEM reescalado a UInt16 (dem_blender.tif).
        color_ramp: Ruta al fichero de ramp de gdaldem (elevaciones en m).
        output_path: Ruta de salida para el PNG combinado.
        src_min: Elevación mínima (m) usada al reescalar a UInt16.
        src_max: Elevación máxima (m) usada al reescalar a UInt16.
        mode: ``"overlay"`` - sobreescribe render_png con el resultado combinado;
              ``"separate"`` - guarda sólo el color sin combinar en output_path;
              ``"both"`` - guarda el combinado en output_path y el color puro
              en ``<output_path_sin_ext>_color.<ext>``.
    """
    if not _GDAL_AVAILABLE:
        raise ImportError("GDAL is required for --color-relief. Install via conda-forge.")

    # -----------------------------------------------------------------------
    # El plano de Blender siempre se escala a plane_y × (plane_y * render_aspect)
    # para rellenar el frame sin bandas. La textura DEM ocupa UV 0-1 sobre
    # ese plano, igual que el color image ocupa el DEM completo.
    # Por tanto: resize directo DEM color → render_w × render_h. Sin crop.
    # -----------------------------------------------------------------------
    render_img = Image.open(render_png)
    render_w, render_h = render_img.size

    with tempfile.TemporaryDirectory(prefix="relievo-cr-") as tmpdir:
        rescaled_ramp = str(pathlib.Path(tmpdir) / "ramp_uint16.txt")
        color_tif     = str(pathlib.Path(tmpdir) / "color_relief.tif")
        color_png_tmp = str(pathlib.Path(tmpdir) / "color_relief.png")

        # 1. Reescribir el ramp de metros a valores UInt16 del dem_blender.tif
        _rescale_ramp(color_ramp, src_min, src_max, rescaled_ramp)

        # 2. Generar color sobre dem_blender.tif (mismo grid que usa Blender)
        cmd = ["gdaldem", "color-relief", dem_blender_path, rescaled_ramp, color_tif]
        log.debug(f"Ejecutando: {' '.join(cmd)}")
        proc = subprocess.run(cmd, capture_output=True)
        if proc.returncode != 0:
            raise RuntimeError(
                f"gdaldem color-relief failed:\n{proc.stderr.decode(errors='replace')}"
            )

        # 3. GeoTIFF → PNG (Pillow no lee bien los canales alpha de GDAL GeoTIFF)
        subprocess.run(
            ["gdal_translate", "-of", "PNG", color_tif, color_png_tmp],
            capture_output=True, check=True,
        )

        # 4. Resize directo al tamaño del render (sin crop ni padding)
        color_full = Image.open(color_png_tmp).convert("RGB")
        color_final = color_full.resize((render_w, render_h), Image.LANCZOS)
        log.debug(f"Color relief: {color_full.size} → {render_w}×{render_h}")

        # 4. Guardar según el modo
        out_path = pathlib.Path(output_path)
        separate_path = str(out_path.with_name(out_path.stem + "_color" + out_path.suffix))

        if mode == "separate":
            color_final.save(separate_path, format="PNG")
            log.info(f"Color relief guardado  →  {separate_path}")
        elif mode == "both":
            color_final.save(separate_path, format="PNG")
            log.info(f"Color relief guardado  →  {separate_path}")
            blended = ImageChops.multiply(_open_as_rgb8(render_png), color_final)
            blended.save(output_path, format="PNG")
            log.info(f"Color relief aplicado  →  {output_path}")
        else:  # overlay
            blended = ImageChops.multiply(_open_as_rgb8(render_png), color_final)
            blended.save(output_path, format="PNG")
            log.info(f"Color relief aplicado  →  {output_path}")
