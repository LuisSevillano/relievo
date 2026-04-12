"""Blender bpy script — runs inside Blender's Python interpreter.

This script is passed to Blender via:
  blender --background template.blend --python blender_script.py -- [args]

It must be entirely self-contained: no access to the blender_relief package
or its pip dependencies, since it runs inside Blender's bundled Python.

Template assumptions (must be set up once manually in Blender GUI):
  - An object named "Plane" exists in the scene
  - The Plane has a material with use_nodes=True
  - The material has a ShaderNodeTexImage node whose Color output feeds displacement
  - Camera is orthographic, pointing straight down
  - Lighting and render settings are pre-configured in the template
"""

import argparse
import sys

import bpy


def parse_args() -> argparse.Namespace:
    # Blender's sys.argv: ['blender', ..., '--', '--dem-path', ...]
    argv = sys.argv
    argv = argv[argv.index("--") + 1:] if "--" in argv else []

    parser = argparse.ArgumentParser()
    parser.add_argument("--dem-path", required=True)
    parser.add_argument("--width-m", type=float, required=True)
    parser.add_argument("--height-m", type=float, required=True)
    parser.add_argument("--raster-x", type=int, required=True)
    parser.add_argument("--raster-y", type=int, required=True)
    parser.add_argument("--max-size", type=int, default=0)
    parser.add_argument("--scale", type=int, default=100)
    parser.add_argument("--exaggeration", type=float, default=None)
    parser.add_argument("--samples", type=int, default=None)
    parser.add_argument("--light-azimuth", type=float, default=None)
    parser.add_argument("--light-altitude", type=float, default=None)
    parser.add_argument("--output", required=True)
    return parser.parse_args(argv)


def get_plane_material(obj_name: str = "Plane") -> "bpy.types.Material":
    if obj_name not in bpy.data.objects:
        raise RuntimeError(
            f"Object '{obj_name}' not found. "
            f"Available: {list(bpy.data.objects.keys())}"
        )
    obj = bpy.data.objects[obj_name]
    if not obj.material_slots:
        raise RuntimeError(f"Object '{obj_name}' has no material slots.")
    mat = obj.material_slots[0].material
    mat.use_nodes = True
    return mat


def find_dem_texture_node(mat: "bpy.types.Material") -> "bpy.types.ShaderNodeTexImage":
    """Find the Image Texture node whose Color output feeds into displacement.

    In Huffman's setup there are typically two TEX_IMAGE nodes: one for the DEM
    (Color output connected) and one for a mask (only Alpha used). We pick the
    node with a connected Color socket, falling back to the first one found.
    """
    tex_nodes = [n for n in mat.node_tree.nodes if n.type == "TEX_IMAGE"]
    if not tex_nodes:
        raise RuntimeError(f"No Image Texture node found in material '{mat.name}'.")

    for node in tex_nodes:
        color_socket = node.outputs.get("Color")
        if color_socket and color_socket.links:
            return node

    return tex_nodes[0]


def set_plane_exact(obj_name: str, plane_x: float, plane_y: float) -> None:
    """Scale the plane to exact Blender unit dimensions (Huffman's method).

    plane size (Blender units) = DEM pixel dimensions / 1000
    A default plane has vertices at ±1 (2×2 units), so divide by 2 for scale factor.
    """
    obj = bpy.data.objects[obj_name]
    obj.scale.x = plane_x / 2.0
    obj.scale.y = plane_y / 2.0
    obj.scale.z = 1.0
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.transform_apply(scale=True)


def main() -> None:
    args = parse_args()

    mat = get_plane_material("Plane")

    img = bpy.data.images.load(args.dem_path, check_existing=False)
    img.colorspace_settings.name = "Non-Color"
    find_dem_texture_node(mat).image = img

    plane_y = args.raster_y / 1000.0
    plane_x = args.raster_x / 1000.0

    scene = bpy.context.scene

    # La resolución del render se adapta al aspect ratio del DEM para que el
    # plano rellene el frame exacto sin bandas ni distorsión.
    # Sin --max-size se usa el lado mayor del template como referencia de tamaño.
    if args.max_size > 0:
        max_px = args.max_size
    else:
        max_px = max(scene.render.resolution_x, scene.render.resolution_y)

    factor = max_px / max(args.raster_x, args.raster_y)
    rx = max(1, round(args.raster_x * factor))
    ry = max(1, round(args.raster_y * factor))
    scene.render.resolution_x = rx
    scene.render.resolution_y = ry
    print(f"Render resolution: {rx}×{ry} px  (DEM aspect {args.raster_x/args.raster_y:.3f})")

    scene.render.resolution_percentage = args.scale

    if scene.camera:
        # Plano con las proporciones exactas del DEM → sin bandas, sin distorsión.
        set_plane_exact("Plane", plane_x, plane_y)

        scene.camera.data.sensor_fit = 'VERTICAL'
        scene.camera.data.ortho_scale = plane_y
        print(f"Camera: ortho_scale={plane_y:.4f}  plane={plane_x:.3f}×{plane_y:.3f}")

    if args.exaggeration is not None:
        for node in mat.node_tree.nodes:
            if node.type == "DISPLACEMENT":
                node.inputs["Scale"].default_value = args.exaggeration
                break

    if args.samples is not None:
        scene.cycles.samples = args.samples

    if args.light_azimuth is not None or args.light_altitude is not None:
        import math
        sun = next(
            (o for o in bpy.data.objects if o.type == "LIGHT" and o.data.type == "SUN"),
            None,
        )
        if sun:
            cur_az = math.degrees(sun.rotation_euler[2])
            cur_alt = 90.0 - math.degrees(sun.rotation_euler[0])
            az = args.light_azimuth if args.light_azimuth is not None else cur_az
            alt = args.light_altitude if args.light_altitude is not None else cur_alt
            sun.rotation_euler = (math.radians(90.0 - alt), 0.0, math.radians(az))
            print(f"Sun light: azimuth={az:.1f}° altitude={alt:.1f}°")
        else:
            print("Warning: no SUN lamp found in template — --light-azimuth/--light-altitude ignored.")

    scene.render.filepath = args.output
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_depth = "16"

    bpy.ops.render.render(write_still=True)


if __name__ == "__main__":
    main()
