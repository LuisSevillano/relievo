# Examples data layout

- `tenerife_bbox.geojson`: primary quick-start bounding box.
- `bboxes/*.geojson`: reusable bounding boxes used by extended examples.
- `ramp_terrain.txt` and `ramp_hormuz.txt`: color relief ramps for `gdaldem color-relief`.

Keep new GeoJSON example inputs inside `examples/` so command examples remain portable and the repository root stays clean.
