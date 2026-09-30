# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "geopandas>=1.0",
#     "pyogrio>=0.10",
# ]
# ///

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import geopandas as gpd
from _safe_io import AtomicVectorOutput

SUPPORTED_EXTENSIONS = {".gpkg", ".shp", ".geojson", ".json", ".kml", ".gdb"}


def infer_driver(path: Path) -> str | None:
    mapping = {
        ".gpkg": "GPKG",
        ".shp": "ESRI Shapefile",
        ".geojson": "GeoJSON",
        ".json": "GeoJSON",
        ".kml": "KML",
        ".gdb": "OpenFileGDB",
    }
    return mapping.get(path.suffix.lower())


def read_file(path: Path, layer: str | None, encoding: str | None) -> gpd.GeoDataFrame:
    kwargs: dict = {"engine": "pyogrio"}
    if layer:
        kwargs["layer"] = layer
    if encoding:
        kwargs["encoding"] = encoding
    if path.suffix.lower() == ".gdb":
        kwargs["engine"] = "pyogrio"
    return gpd.read_file(path, **kwargs)


def write_file(gdf, path, layer, *, overwrite=False, protected_paths=()):
    with AtomicVectorOutput(path, overwrite=overwrite, protected_paths=protected_paths) as out:
        if layer and out.driver == "GPKG":
            out.layer = layer
        out.write(gdf)
        out.commit(expected_features=len(gdf), expected_crs=gdf.crs,
                   required_fields=[c for c in gdf.columns if c != gdf.geometry.name])


def convert_single(input_path: Path, output_path: Path, layer: str | None, output_layer: str | None, crs: str | None, encoding: str | None, overwrite=False) -> None:
    gdf = read_file(input_path, layer, encoding)
    print(f"Read {len(gdf)} features from {input_path}" + (f" (layer: {layer})" if layer else ""))

    if crs:
        src_crs = gdf.crs
        gdf = gdf.to_crs(crs)
        print(f"Reprojected: {src_crs} -> {gdf.crs}")

    out_layer = output_layer or layer
    write_file(gdf, output_path, out_layer, overwrite=overwrite, protected_paths=[input_path])
    print(f"Written to {output_path}" + (f" (layer: {out_layer})" if out_layer else ""))


def build_output_path(input_path: Path, output_ext: str) -> Path:
    return input_path.with_suffix(output_ext)


def main() -> None:
    parser = argparse.ArgumentParser(description="GIS format conversion and CRS reprojection")
    parser.add_argument("input", help="Input file or directory path")
    parser.add_argument("--output", help="Output file path (format inferred from extension)")
    parser.add_argument("--layer", help="Source layer name")
    parser.add_argument("--output-layer", help="Output layer name (defaults to source layer)")
    parser.add_argument("--crs", help="Target CRS (e.g., EPSG:4326)")
    parser.add_argument("--encoding", help="Source file encoding (e.g., gbk, gb2312)")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    input_path = Path(args.input)

    if input_path.is_dir() and input_path.suffix.lower() != ".gdb":
        if not args.output:
            print("ERROR: --output (with target extension) is required for batch directory conversion", file=sys.stderr)
            sys.exit(1)
        out_ext = Path(args.output).suffix
        if not out_ext:
            print("ERROR: --output must have a file extension to infer target format", file=sys.stderr)
            sys.exit(1)
        output_dir = Path(args.output).parent if Path(args.output).parent != Path(".") else input_path
        output_dir.mkdir(parents=True, exist_ok=True)

        files = [f for f in input_path.iterdir() if f.suffix.lower() in SUPPORTED_EXTENSIONS and f.is_file()]
        if not files:
            files = [f for f in input_path.iterdir() if f.is_dir() and f.suffix.lower() == ".gdb"]
        print(f"Batch mode: {len(files)} files found in {input_path}")
        failures = 0
        for f in sorted(files):
            out = output_dir / f"{f.stem}{out_ext}"
            try:
                convert_single(f, out, args.layer, args.output_layer, args.crs, args.encoding, args.overwrite)
            except Exception as e:
                failures += 1
                print(f"ERROR processing {f}: {e}", file=sys.stderr)
        if failures:
            sys.exit(1)
    else:
        if not args.output:
            print("ERROR: --output is required", file=sys.stderr)
            sys.exit(1)
        convert_single(input_path, Path(args.output), args.layer, args.output_layer, args.crs, args.encoding, args.overwrite)


if __name__ == "__main__":
    main()
