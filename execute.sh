#!/usr/bin/env bash
# Example render commands for blender-relief
# Copy and adapt to your own paths and bounding boxes.

BLENDER="/Applications/Blender 3.3.app/Contents/MacOS/Blender"
TEMPLATE="template.blend"
export OPENTOPO_API_KEY="REDACTED_OPENTOPO_API_KEY"

# ---------------------------------------------------------------------------
# 1. Tenerife — local DEM + hypsometric tint
# ---------------------------------------------------------------------------
blender-relief \
  --blender "$BLENDER" \
  --bbox examples/tenerife_bbox.geojson \
  --template "$TEMPLATE" \
  --dem dem.tif \
  --color-relief examples/ramp_terrain.txt \
  --color-relief-mode both \
  --output relieve.png \
  --scale 100

# ---------------------------------------------------------------------------
# 2. Gulf of Oman — download SRTM15Plus (bathymetry), Albers Equal Area CRS,
#    save processed DEM for quick re-renders
# ---------------------------------------------------------------------------
# blender-relief \
#   --blender "$BLENDER" \
#   --bbox rectangle_iran.geojson \
#   --template "$TEMPLATE" \
#   --api-key "$OPENTOPO_API_KEY" \
#   --demtype SRTM15Plus \
#   --crs "+proj=aea +lat_1=27 +lat_2=37 +lat_0=32 +lon_0=54 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs" \
#   --save-dem examples/hormuz_raw.tif \
#   --save-processed-dem examples/hormuz.tif \
#   --color-relief examples/ramp_hormuz.txt \
#   --color-relief-mode both \
#   --output docs/images/hormuz_relief.png \
#   --scale 100

# Re-render using saved DEM (no download, no re-crop):
# blender-relief \
#   --blender "$BLENDER" \
#   --bbox rectangle_iran.geojson \
#   --template "$TEMPLATE" \
#   --dem examples/hormuz.tif \
#   --crs "+proj=aea +lat_1=27 +lat_2=37 +lat_0=32 +lon_0=54 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs" \
#   --color-relief examples/ramp_hormuz.txt \
#   --color-relief-mode both \
#   --output docs/images/hormuz_relief.png \
#   --scale 100
