# blender-relief

A CLI tool that automates the creation of shaded relief maps using Blender, following [Daniel Huffman's method](https://somethingaboutmaps.wordpress.com/2017/11/16/creating-shaded-relief-in-blender/). Inspired by [Nick Underwood's blenderize.sh](https://github.com/nunderwood6/blender_prep).

Given a bounding box, it downloads a DEM, processes it with GDAL, and renders a shaded relief PNG through a Blender template — fully headless.

```
bbox.geojson + template.blend  →  shaded_relief.png
```

## How it works

1. Reads a bounding box from a GeoJSON file (WGS84)
2. Downloads a DEM from [OpenTopography](https://portal.opentopography.org/) (or uses a local file)
3. Reprojects, crops, and rescales the DEM to 16-bit unsigned with GDAL
4. Loads the processed DEM into a Blender `.blend` template, adjusts the plane dimensions and camera, and renders a PNG

## Requirements

- Python ≥ 3.9
- GDAL Python bindings (`osgeo`)
- Blender 3.x

### Install dependencies

**With conda (recommended):**
```bash
conda env create -f environment.yml
conda activate blender-relief
```

**With pip (if GDAL is already installed system-wide, e.g. via Homebrew):**
```bash
pip install -e .
```

## Preparing your Blender template

The tool expects a `.blend` file set up following Huffman's shaded relief method:

- An object named **`Plane`** in the scene
- The Plane has a material with nodes enabled
- The material has an **Image Texture** node whose `Color` output feeds into the displacement chain — this node's image will be replaced with the processed DEM on each run
- Camera is **orthographic**, pointing straight down
- Sun lamp, render engine (Cycles), and other settings are pre-configured in the template

The tool automatically adjusts the plane's X/Y scale to match the DEM's aspect ratio, sets the camera's `Orthographic Scale` accordingly, and updates the render resolution to match the DEM pixel dimensions.

## Usage

```bash
blender-relief --bbox region.geojson --template relief.blend --output shaded_relief.png
```

### Download DEM automatically

```bash
blender-relief \
  --bbox region.geojson \
  --template relief.blend \
  --output shaded_relief.png \
  --api-key YOUR_OPENTOPO_KEY
```

### Use a local DEM

```bash
blender-relief \
  --dem local_dem.tif \
  --bbox region.geojson \
  --template relief.blend \
  --output shaded_relief.png
```

### Quick preview at 25% resolution

```bash
blender-relief \
  --bbox region.geojson \
  --template relief.blend \
  --output preview.png \
  --scale 25 \
  --api-key YOUR_OPENTOPO_KEY
```

### Custom projection (e.g. UTM zone 28N for the Canary Islands)

```bash
blender-relief \
  --bbox tenerife.geojson \
  --template relief.blend \
  --output tenerife_relief.png \
  --crs EPSG:32628 \
  --api-key YOUR_OPENTOPO_KEY
```

### Control sun direction

```bash
blender-relief \
  --bbox region.geojson \
  --template relief.blend \
  --output relief.png \
  --light-azimuth 315 \
  --light-altitude 35 \
  --api-key YOUR_OPENTOPO_KEY
```

### Apply hypsometric color tint

```bash
blender-relief \
  --bbox region.geojson \
  --template relief.blend \
  --output relief.png \
  --color-relief ramp.txt \
  --api-key YOUR_OPENTOPO_KEY
```

### Clip output to polygon shape

```bash
blender-relief \
  --bbox terrain.geojson \
  --template relief.blend \
  --output relief.png \
  --clip-mask \
  --api-key YOUR_OPENTOPO_KEY
```

### Preview without downloading or rendering

```bash
blender-relief \
  --bbox region.geojson \
  --template relief.blend \
  --output relief.png \
  --dry-run \
  --api-key YOUR_OPENTOPO_KEY
```

### Download and process DEM only (skip render)

```bash
blender-relief \
  --bbox region.geojson \
  --template relief.blend \
  --output relief.png \
  --no-render \
  --save-dem terrain.tif \
  --api-key YOUR_OPENTOPO_KEY
```

## Options

| Option | Default | Description |
|---|---|---|
| `--config PATH` | — | TOML file with default option values. Keys use underscores (e.g. `light_azimuth`). Command-line arguments always take precedence. See [Configuration file](#configuration-file). |
| `--bbox PATH` | *(required)* | GeoJSON file with bounding box polygon in WGS84. Supports `Feature`, `FeatureCollection`, `Polygon`, `MultiPolygon`, and `GeometryCollection`. |
| `--template PATH` | *(required)* | Path to the `.blend` template file. |
| `--output PATH` | *(required)* | Output PNG file path. |
| `--dem PATH` | — | Path to an existing DEM GeoTIFF. If omitted, the DEM is downloaded from OpenTopography. |
| `--save-dem PATH` | — | Save the downloaded DEM to this path after downloading. Useful to reuse it later with `--dem` and avoid re-downloading the same area. Ignored when `--dem` is provided. |
| `--buffer FLOAT` | `0.05` | Buffer applied to the bbox **before downloading**, as a fraction of its size (`0.05` = 5%). Prevents data loss at the edges after reprojection. Set to `0` to disable. Ignored when `--dem` is provided. |
| `--crs TEXT` | — | Target CRS for reprojection (e.g. `EPSG:3857`, `EPSG:32628`). If omitted, the DEM is used as downloaded with no reprojection. |
| `--demtype TEXT` | `SRTMGL1` | OpenTopography dataset. See table below or run `blender-relief --list-demtypes`. |
| `--list-demtypes` | — | Print all available DEM datasets with resolution and coverage, then exit. |
| `--api-key TEXT` | — | OpenTopography API key. Can also be set via the `OPENTOPO_API_KEY` environment variable. Get a free key at [portal.opentopography.org](https://portal.opentopography.org/requestService?service=api). |
| `--exaggeration FLOAT` | *(template)* | Vertical exaggeration factor, applied to the Displacement node's Scale input. Values below `1.0` flatten the terrain; values above `1.0` emphasise relief. If omitted, the template's value is preserved. |
| `--samples INTEGER` | *(template)* | Number of Cycles render samples. If omitted, the value from the `.blend` template is used. Lower values (e.g. `32`) speed up previews; higher values (e.g. `512`) give cleaner results. |
| `--max-size INTEGER` | *(template)* | Sets the longest side of the render output to exactly this many pixels, preserving the DEM aspect ratio. If omitted, the resolution from the `.blend` template is used. `--scale` is applied on top of this value. |
| `--scale INTEGER` | `100` | Render resolution percentage (1–100), applied after `--max-size`. Use `25` or `50` for quick previews. |
| `--light-azimuth FLOAT` | *(template)* | Sun azimuth in degrees (0 = North, clockwise). Overrides the sun lamp's direction in the template. |
| `--light-altitude FLOAT` | *(template)* | Sun altitude (elevation) in degrees (0 = horizon, 90 = directly overhead). Overrides the sun lamp's angle in the template. |
| `--color-relief PATH` | — | Path to a `gdaldem color-relief` ramp file. Composites a hypsometric color tint over the rendered PNG using multiply blending. Requires `gdaldem` on `PATH`. |
| `--clip-mask` | `false` | Clip the output PNG to the GeoJSON polygon shape. Pixels outside the polygon become transparent (RGBA output). |
| `--dry-run` | `false` | Print a summary of what would be downloaded and rendered, then exit without doing anything. |
| `--no-render` | `false` | Download and process the DEM but skip the Blender render step. Use `--save-dem` to keep the processed DEM. |
| `--blender TEXT` | `blender` | Path to the Blender executable. Useful if Blender is not on your `PATH` (e.g. `"/Applications/Blender.app/Contents/MacOS/Blender"` on macOS). |
| `--verbose` | `false` | Print detailed progress: GDAL steps, bounding box info, and the full Blender log. By default only the key pipeline steps are shown and Blender output is suppressed (shown only on error). |
| `--keep-workdir` | `false` | Do not delete the temporary working directory after the render. Useful for inspecting intermediate files (`reprojected.tif`, `dem_blender.tif`). |

## Available DEM datasets

The tool estimates the output pixel dimensions before downloading, based on the dataset's native resolution and the bounding box size. The formula is:

```
pixels_x = ceil((east - west) × 3600 / arcsec)
pixels_y = ceil((north - south) × 3600 / arcsec)
```

| Code | Name | Resolution | Max area |
|---|---|---|---|
| `SRTMGL1` | SRTM 30m | 1″ (~30m) | 450,000 km² |
| `SRTMGL1_E` | SRTM GL1 Ellipsoidal 30m | 1″ (~30m) | 450,000 km² |
| `SRTMGL3` | SRTM 90m | 3″ (~90m) | 4,050,000 km² |
| `AW3D30` | ALOS World 3D 30m | 1″ (~30m) | 450,000 km² |
| `AW3D30_E` | ALOS World 3D Ellipsoidal 30m | 1″ (~30m) | 450,000 km² |
| `SRTM15Plus` | Global Bathymetry SRTM15+ V2.1 | 15″ (~450m) | 125,000,000 km² |
| `COP30` | Copernicus DSM 30m | 1″ (~30m) | 450,000 km² |
| `COP90` | Copernicus DSM 90m | 3″ (~90m) | 4,050,000 km² |
| `NASADEM` | NASADEM 30m | 1″ (~30m) | 450,000 km² |
| `EU_DTM` | EU DTM 30m | 1″ (~30m) | 450,000 km² |
| `GEDI_L3` | GEDI L3 1km | 30″ (~1km) | 450,000 km² |
| `GEBCOIceTopo` | GEBCO IceTopo 500m | 15″ (~500m) | 4,050,000 km² |
| `GEBCOSubIceTopo` | GEBCO SubIceTopo 500m | 15″ (~500m) | 4,050,000 km² |

## Configuration file

You can store frequently used options in a TOML file and pass it with `--config`. Keys must use underscores (not hyphens) and match the option names exactly. Command-line arguments always take precedence over config file values.

**Example — `relief.toml`:**

```toml
# OpenTopography dataset
demtype        = "COP30"

# Vertical exaggeration (Displacement node Scale)
exaggeration   = 2.0

# Cycles render samples
samples        = 256

# Longest side of render output in pixels
max_size       = 3000

# Render resolution percentage (applied after max_size)
scale          = 100

# Sun direction
light_azimuth  = 315
light_altitude = 45

# Reproject DEM to this CRS before rendering
# crs = "EPSG:32628"
```

**Usage:**

```bash
blender-relief \
  --config relief.toml \
  --bbox tenerife.geojson \
  --template relief.blend \
  --output tenerife.png \
  --api-key YOUR_KEY
```

You can override any config value on the command line:

```bash
# Use the profile but render at a smaller size for a quick preview
blender-relief --config relief.toml --bbox region.geojson \
  --template relief.blend --output preview.png \
  --max-size 800 --scale 50
```

## Color relief

The `--color-relief` option overlays a hypsometric color tint on top of the Blender shaded relief. It uses `gdaldem color-relief` to colorize the processed DEM, then composites the result with the render using multiply blending — producing a colored shaded relief where the terrain texture is preserved.

The color ramp file follows the standard `gdaldem` format (elevation in metres, then R G B):

```
# ramp.txt
0     70  130 180
200   210 180 140
1000  34  139 34
3000  150 120 90
5000  255 255 255
nv    0   0   0
```

Apply it with:

```bash
blender-relief --bbox region.geojson --template relief.blend --output out.png \
  --color-relief ramp.txt --api-key YOUR_KEY
```

`gdaldem` must be installed and available on `PATH`. It is included with GDAL (conda-forge or system package).

## Clip mask

The `--clip-mask` flag clips the output PNG to the exact shape of the GeoJSON polygon. Pixels outside the polygon become transparent (the output PNG will have an alpha channel). This is useful when the bounding box is rectangular but you want to present a non-rectangular area — for example, clipping to a country or administrative boundary.

```bash
blender-relief --bbox boundary.geojson --template relief.blend --output out.png \
  --clip-mask --api-key YOUR_KEY
```

When combined with `--color-relief`, the color tint is applied first and then the clip mask is applied on top.

## Environment variables

| Variable | Description |
|---|---|
| `OPENTOPO_API_KEY` | OpenTopography API key. Equivalent to `--api-key`. |
