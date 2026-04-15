"""Download DEM from OpenTopography API."""

import json
import math

import requests

from . import log

OPENTOPO_ENDPOINT = "https://portal.opentopography.org/API/globaldem"

# Dataset registry: code → (display name, resolution in arc-seconds, max area km²)
# Source: OpenTopography DEM Downloader QGIS plugin + OpenTopography docs
DEM_DATASETS = {
    "SRTMGL1": ("SRTM 30m", 1, 450_000),
    "SRTMGL1_E": ("SRTM GL1 Ellipsoidal 30m", 1, 450_000),
    "SRTMGL3": ("SRTM 90m", 3, 4_050_000),
    "AW3D30": ("ALOS World 3D 30m", 1, 450_000),
    "AW3D30_E": ("ALOS World 3D Ellipsoidal 30m", 1, 450_000),
    "SRTM15Plus": ("Global Bathymetry SRTM15+ V2.1", 15, 125_000_000),
    "COP30": ("Copernicus DSM 30m", 1, 450_000),
    "COP90": ("Copernicus DSM 90m", 3, 4_050_000),
    "NASADEM": ("NASADEM 30m", 1, 450_000),
    "EU_DTM": ("EU DTM 30m", 1, 450_000),
    "GEDI_L3": ("GEDI L3 1km", 30, 450_000),
    "GEBCOIceTopo": ("GEBCO IceTopo 500m", 15, 4_050_000),
    "GEBCOSubIceTopo": ("GEBCO SubIceTopo 500m", 15, 4_050_000),
}


def estimate_pixels(bbox_wgs84: tuple, demtype: str) -> tuple:
    """Estimate output pixel dimensions for a given bbox and DEM dataset.

    Returns (pixels_x, pixels_y). Based on the dataset's native arc-second
    resolution: pixels = bbox_degrees × (3600 / arcsec).
    """
    west, south, east, north = bbox_wgs84
    if demtype not in DEM_DATASETS:
        return (None, None)
    _, arcsec, _ = DEM_DATASETS[demtype]
    px_per_degree = 3600 / arcsec
    pixels_x = math.ceil((east - west) * px_per_degree)
    pixels_y = math.ceil((north - south) * px_per_degree)
    return (pixels_x, pixels_y)


def estimate_bbox_area_km2(bbox_wgs84: tuple) -> float:
    """Estimate bbox area in km² using a geographic approximation.

    Uses 111.32 km/deg latitude and scales longitude by cos(mid_lat).
    This is sufficient for request-size validation and user guidance.
    """
    west, south, east, north = bbox_wgs84
    dlon = abs(east - west)
    dlat = abs(north - south)
    mid_lat = (south + north) / 2.0

    km_per_deg_lat = 111.32
    km_per_deg_lon = 111.32 * math.cos(math.radians(mid_lat))
    return (dlat * km_per_deg_lat) * (dlon * km_per_deg_lon)


def buffer_bbox(bbox_wgs84: tuple, pct: float) -> tuple:
    """Expand a WGS84 bbox by a percentage of its dimensions.

    Args:
        bbox_wgs84: (west, south, east, north) in degrees.
        pct: buffer as a fraction (e.g. 0.05 = 5%).

    Returns expanded (west, south, east, north), clamped to valid WGS84 range.
    """
    west, south, east, north = bbox_wgs84
    dx = (east - west) * pct
    dy = (north - south) * pct
    return (
        max(west - dx, -180.0),
        max(south - dy, -90.0),
        min(east + dx, 180.0),
        min(north + dy, 90.0),
    )


def extract_wgs84_bbox(geojson_path: str) -> tuple:
    """Return (west, south, east, north) from a GeoJSON file in WGS84.

    Works with Feature, FeatureCollection, Polygon, MultiPolygon,
    and GeometryCollection.
    """
    with open(geojson_path) as f:
        data = json.load(f)

    coords = _collect_coords(data)
    if not coords:
        raise ValueError(f"No coordinates found in {geojson_path}")

    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return (min(lons), min(lats), max(lons), max(lats))


def _collect_coords(obj: dict) -> list:
    geom_type = obj.get("type", "")

    if geom_type == "FeatureCollection":
        coords = []
        for feature in obj.get("features", []):
            coords.extend(_collect_coords(feature))
        return coords

    if geom_type == "Feature":
        return _collect_coords(obj.get("geometry", {}))

    if geom_type == "Polygon":
        return [pt for ring in obj.get("coordinates", []) for pt in ring]

    if geom_type == "MultiPolygon":
        return [pt for poly in obj.get("coordinates", []) for ring in poly for pt in ring]

    if geom_type == "GeometryCollection":
        coords = []
        for geom in obj.get("geometries", []):
            coords.extend(_collect_coords(geom))
        return coords

    return []


def download_dem(
    bbox_wgs84: tuple,
    demtype: str,
    api_key: str,
    output_path: str,
    timeout: int = 300,
) -> str:
    """Download a DEM GeoTIFF from OpenTopography.

    Returns output_path on success. Raises RuntimeError on failure.
    """
    west, south, east, north = bbox_wgs84

    if demtype in DEM_DATASETS:
        name, arcsec, _ = DEM_DATASETS[demtype]
        px_x, px_y = estimate_pixels(bbox_wgs84, demtype)
        log.info(f'Downloading DEM  {demtype} ({arcsec}" / ~{arcsec * 30}m)  est. {px_x}×{px_y} px')
        log.debug(f"Dataset: {name}")

    params = {
        "demtype": demtype,
        "south": south,
        "north": north,
        "west": west,
        "east": east,
        "outputFormat": "GTiff",
        "API_Key": api_key,
    }

    log.debug("Sending request to OpenTopography...")
    response = requests.get(OPENTOPO_ENDPOINT, params=params, stream=True, timeout=timeout)

    content_type = response.headers.get("Content-Type", "")
    if response.status_code == 401 or "text/html" in content_type and response.status_code != 200:
        raise RuntimeError(
            f"OpenTopography API error (HTTP {response.status_code}). "
            "Check your API key (--api-key or OPENTOPO_API_KEY env var)."
        )
    if response.status_code == 400:
        raise RuntimeError(
            f"OpenTopography returned HTTP 400. The bounding box may be too large "
            f"or the dataset '{demtype}' may not cover the requested area."
        )
    if not response.ok:
        raise RuntimeError(
            f"OpenTopography returned HTTP {response.status_code}: {response.text[:200]}"
        )

    if "text/html" in content_type:
        raise RuntimeError(
            f"OpenTopography returned HTML instead of a GeoTIFF. "
            f"Response preview: {response.text[:300]}"
        )

    with open(output_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)

    log.debug(f"DEM saved to {output_path}")
    return output_path
