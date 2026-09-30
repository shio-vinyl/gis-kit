# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "geopandas>=1.0",
#     "pyogrio>=0.10",
#     "tabulate",
# ]
# ///

import argparse
import sys
from pathlib import Path

import geopandas as gpd
import pyogrio
from _table import tabulate


def resolve_path(raw: str, must_exist: bool = True) -> Path:
    p = Path(raw).expanduser().resolve()
    if must_exist and not p.exists():
        sys.exit(f"Error: file not found: {p}")
    return p


def is_gdb(path: Path) -> bool:
    return path.suffix.lower() == ".gdb" or str(path).lower().endswith(".gdb")


def guard_gdb_write(path: Path, operation: str = "write") -> None:
    if is_gdb(path):
        sys.exit(f"Error: {operation} to File GDB is not supported. GDB files are read-only via GDAL/OGR open-source drivers. Use GPKG instead.")


def looks_like_sql(query: str) -> bool:
    return query.lstrip().upper().startswith(("SELECT", "WITH"))


def cmd_layers(args: argparse.Namespace) -> None:
    path = resolve_path(args.input)
    try:
        info = pyogrio.list_layers(path)
    except Exception as e:
        sys.exit(f"Error reading layers: {e}")

    rows = []
    for name, geom_type in info:
        try:
            meta = pyogrio.read_info(path, layer=name)
            rows.append({
                "layer": name,
                "features": meta.get("features", "?"),
                "geometry_type": geom_type or "None",
                "crs": str(meta.get("crs", "Unknown")),
            })
        except Exception:
            rows.append({
                "layer": name,
                "features": "?",
                "geometry_type": geom_type or "None",
                "crs": "?",
            })

    if not rows:
        print("No layers found.")
        return
    print(tabulate(rows, headers="keys", tablefmt="simple"))


def cmd_import(args: argparse.Namespace) -> None:
    source = resolve_path(args.source)
    target = resolve_path(args.target, must_exist=False)
    guard_gdb_write(target, "import")

    source_layer = args.source_layer
    if source_layer is None:
        layers = pyogrio.list_layers(source)
        if len(layers) == 0:
            sys.exit("Error: no layers found in source file")
        source_layer = layers[0][0]

    target_layer = args.target_layer or source_layer

    try:
        gdf = gpd.read_file(source, layer=source_layer, engine="pyogrio")
    except Exception as e:
        sys.exit(f"Error reading source layer '{source_layer}': {e}")

    mode = "a" if target.exists() else "w"
    try:
        gdf.to_file(target, layer=target_layer, driver="GPKG", engine="pyogrio", mode=mode)
    except Exception as e:
        sys.exit(f"Error writing to target: {e}")

    print(f"Imported '{source_layer}' -> '{target_layer}' ({len(gdf)} features)")


def cmd_export(args: argparse.Namespace) -> None:
    input_path = resolve_path(args.input)
    output_path = resolve_path(args.output, must_exist=False)

    layer = args.layer
    if layer is None:
        layers = pyogrio.list_layers(input_path)
        if len(layers) == 0:
            sys.exit("Error: no layers found in input file")
        layer = layers[0][0]

    try:
        gdf = gpd.read_file(input_path, layer=layer, engine="pyogrio")
    except Exception as e:
        sys.exit(f"Error reading layer '{layer}': {e}")

    driver_map = {
        ".shp": "ESRI Shapefile",
        ".geojson": "GeoJSON",
        ".json": "GeoJSON",
        ".gpkg": "GPKG",
        ".fgb": "FlatGeobuf",
        ".parquet": None,
    }

    suffix = output_path.suffix.lower()
    if suffix == ".parquet":
        try:
            gdf.to_parquet(output_path)
        except Exception as e:
            sys.exit(f"Error writing parquet: {e}")
    else:
        driver = driver_map.get(suffix)
        if driver is None:
            driver = "GPKG"
        try:
            gdf.to_file(output_path, driver=driver, engine="pyogrio")
        except Exception as e:
            sys.exit(f"Error writing output: {e}")

    print(f"Exported '{layer}' -> {output_path} ({len(gdf)} features)")


def cmd_drop(args: argparse.Namespace) -> None:
    input_path = resolve_path(args.input)
    guard_gdb_write(input_path, "layer deletion from")

    layer = args.layer
    layers = pyogrio.list_layers(input_path)
    names = [l[0] for l in layers]
    if layer not in names:
        sys.exit(f"Error: layer '{layer}' not found. Available: {', '.join(names)}")

    if len(names) == 1:
        sys.exit("Error: cannot drop the only layer in the file. Delete the file instead.")

    try:
        import sqlite3
        conn = sqlite3.connect(str(input_path))
        cursor = conn.cursor()
        cursor.execute(f"DROP TABLE IF EXISTS \"{layer}\"")
        cursor.execute(f"DELETE FROM gpkg_contents WHERE table_name = ?", (layer,))
        cursor.execute(f"DELETE FROM gpkg_geometry_columns WHERE table_name = ?", (layer,))
        try:
            cursor.execute(f"DELETE FROM gpkg_extensions WHERE table_name = ?", (layer,))
        except Exception:
            pass
        try:
            cursor.execute(f"DELETE FROM gpkg_ogr_contents WHERE table_name = ?", (layer,))
        except Exception:
            pass
        conn.commit()
        conn.close()
    except Exception as e:
        sys.exit(f"Error dropping layer: {e}")

    print(f"Dropped layer '{layer}' from {input_path}")


def cmd_rename(args: argparse.Namespace) -> None:
    input_path = resolve_path(args.input)
    guard_gdb_write(input_path, "layer rename in")

    old_name = args.layer
    new_name = args.new_name

    layers = pyogrio.list_layers(input_path)
    names = [l[0] for l in layers]
    if old_name not in names:
        sys.exit(f"Error: layer '{old_name}' not found. Available: {', '.join(names)}")
    if new_name in names:
        sys.exit(f"Error: layer '{new_name}' already exists")

    try:
        gdf = gpd.read_file(input_path, layer=old_name, engine="pyogrio")
    except Exception as e:
        sys.exit(f"Error reading layer '{old_name}': {e}")

    try:
        gdf.to_file(input_path, layer=new_name, driver="GPKG", engine="pyogrio", mode="a")
    except Exception as e:
        sys.exit(f"Error writing new layer: {e}")

    try:
        import sqlite3
        conn = sqlite3.connect(str(input_path))
        cursor = conn.cursor()
        cursor.execute(f"DROP TABLE IF EXISTS \"{old_name}\"")
        cursor.execute(f"DELETE FROM gpkg_contents WHERE table_name = ?", (old_name,))
        cursor.execute(f"DELETE FROM gpkg_geometry_columns WHERE table_name = ?", (old_name,))
        try:
            cursor.execute(f"DELETE FROM gpkg_extensions WHERE table_name = ?", (old_name,))
        except Exception:
            pass
        try:
            cursor.execute(f"DELETE FROM gpkg_ogr_contents WHERE table_name = ?", (old_name,))
        except Exception:
            pass
        conn.commit()
        conn.close()
    except Exception as e:
        sys.exit(f"Error dropping old layer: {e}")

    print(f"Renamed '{old_name}' -> '{new_name}' ({len(gdf)} features)")


def cmd_copy(args: argparse.Namespace) -> None:
    source = resolve_path(args.source)
    target = resolve_path(args.target, must_exist=False)
    guard_gdb_write(target, "copy to")

    source_layer = args.source_layer
    if source_layer is None:
        layers = pyogrio.list_layers(source)
        if len(layers) == 0:
            sys.exit("Error: no layers found in source file")
        source_layer = layers[0][0]

    target_layer = args.target_layer or source_layer

    try:
        gdf = gpd.read_file(source, layer=source_layer, engine="pyogrio")
    except Exception as e:
        sys.exit(f"Error reading source layer '{source_layer}': {e}")

    mode = "a" if target.exists() else "w"
    try:
        gdf.to_file(target, layer=target_layer, driver="GPKG", engine="pyogrio", mode=mode)
    except Exception as e:
        sys.exit(f"Error writing to target: {e}")

    print(f"Copied '{source_layer}' -> '{target_layer}' ({len(gdf)} features)")


def cmd_sql(args: argparse.Namespace) -> None:
    input_path = resolve_path(args.input)
    query = args.query
    if not looks_like_sql(query):
        sys.exit("Error: --query must be a full SQL statement, e.g. SELECT * FROM layer WHERE ...")

    try:
        sql_dialect = "OGRSQL" if is_gdb(input_path) else None
        gdf = pyogrio.read_dataframe(input_path, sql=query, sql_dialect=sql_dialect)
    except Exception as e:
        sys.exit(f"Error executing query: {e}")

    if args.output:
        output_path = resolve_path(args.output, must_exist=False)
        guard_gdb_write(output_path, "SQL output to")
        output_layer = args.output_layer or "sql_result"
        mode = "a" if output_path.exists() and output_path.suffix.lower() == ".gpkg" else "w"
        driver = "GPKG" if output_path.suffix.lower() == ".gpkg" else None
        try:
            if driver:
                gdf.to_file(output_path, layer=output_layer, driver=driver, engine="pyogrio", mode=mode)
            else:
                gdf.to_file(output_path, engine="pyogrio")
        except Exception as e:
            sys.exit(f"Error writing SQL result: {e}")
        print(f"Saved {len(gdf)} features to {output_path} (layer: {output_layer})")
    else:
        if hasattr(gdf, "geometry") and gdf.geometry is not None:
            df = gdf.drop(columns=gdf.geometry.name, errors="ignore")
        else:
            df = gdf
        print(tabulate(df, headers="keys", tablefmt="simple", showindex=False))
        print(f"\n({len(gdf)} rows)")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="gpkg",
        description="GeoPackage and File GDB management tool",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    layers_p = sub.add_parser("layers", help="List all layers")
    layers_p.add_argument("input", help="GPKG or GDB file")

    import_p = sub.add_parser("import", help="Import a layer into a GPKG")
    import_p.add_argument("--source", required=True, help="Source file")
    import_p.add_argument("--source-layer", default=None, help="Source layer name")
    import_p.add_argument("--target", required=True, help="Target GPKG file")
    import_p.add_argument("--target-layer", default=None, help="Target layer name")

    export_p = sub.add_parser("export", help="Export a layer to another format")
    export_p.add_argument("--input", required=True, help="Input GPKG or GDB file")
    export_p.add_argument("--layer", default=None, help="Layer to export")
    export_p.add_argument("--output", required=True, help="Output file path")

    drop_p = sub.add_parser("drop", help="Delete a layer from a GPKG")
    drop_p.add_argument("--input", required=True, help="GPKG file")
    drop_p.add_argument("--layer", required=True, help="Layer to drop")

    rename_p = sub.add_parser("rename", help="Rename a layer in a GPKG")
    rename_p.add_argument("--input", required=True, help="GPKG file")
    rename_p.add_argument("--layer", required=True, help="Current layer name")
    rename_p.add_argument("--new-name", required=True, help="New layer name")

    copy_p = sub.add_parser("copy", help="Copy a layer between GPKG/GDB files")
    copy_p.add_argument("--source", required=True, help="Source file")
    copy_p.add_argument("--source-layer", default=None, help="Source layer name")
    copy_p.add_argument("--target", required=True, help="Target GPKG file")
    copy_p.add_argument("--target-layer", default=None, help="Target layer name")

    sql_p = sub.add_parser("sql", help="Run SQL query against GPKG/GDB")
    sql_p.add_argument("--input", required=True, help="Input file")
    sql_p.add_argument("--query", required=True, help="SQL query string")
    sql_p.add_argument("--output", default=None, help="Save results to file")
    sql_p.add_argument("--output-layer", default=None, help="Output layer name")

    args = parser.parse_args()

    dispatch = {
        "layers": cmd_layers,
        "import": cmd_import,
        "export": cmd_export,
        "drop": cmd_drop,
        "rename": cmd_rename,
        "copy": cmd_copy,
        "sql": cmd_sql,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
