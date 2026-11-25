#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["geopandas>=1.0", "pyogrio>=0.10", "tabulate"]
# ///
"""Calculate area-based urban indicators with explicit, auditable CRS rules."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
import tempfile

import geopandas as gpd
from shapely.ops import unary_union

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from _table import tabulate

try:
    from _safe_io import write_vector_atomic
except ImportError:  # Support direct module loading by tests.
    if str(Path(__file__).resolve().parent) not in sys.path:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _safe_io import write_vector_atomic

from _metric import AreaMeasure, area_frames
from _budget import check_vector
from _core import clean


def _read(path: str, layer: str | None = None) -> gpd.GeoDataFrame:
    kwargs = {"engine": "pyogrio"}
    if layer:
        kwargs["layer"] = layer
    check_vector(path, layer)
    return gpd.read_file(path, **kwargs)


def _write_result(
    gdf: gpd.GeoDataFrame,
    output: str | None,
    *,
    overwrite: bool = False,
    protected_paths: tuple[str, ...] = (),
    log=None,
) -> None:
    if output is None:
        return
    path = Path(output)
    if path.suffix.lower() == ".csv":
        path.parent.mkdir(parents=True, exist_ok=True)
        if any(path.resolve(strict=False) == Path(source).resolve(strict=False) for source in protected_paths):
            raise ValueError(f"Output must not replace an input dataset: {path}")
        if path.exists() and (path.is_symlink() or not path.is_file() or not overwrite):
            raise ValueError(f"Refusing to replace existing CSV output without a regular file and --overwrite: {path}")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=path.parent, prefix="." + path.name + ".", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                gdf.drop(columns=gdf.geometry.name, errors="ignore").to_csv(handle, index=False)
                handle.flush()
                os.fsync(handle.fileno())
            if overwrite:
                os.replace(temporary, path)
            else:
                os.link(temporary, path)
                temporary.unlink()
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        print("CSV output uses same-directory staging and atomic publication.", file=log)
    else:
        if path.suffix.lower() in {".gpkg", ".geojson", ".json", ".fgb"}:
            evidence = write_vector_atomic(gdf, path, overwrite=overwrite, protected_paths=protected_paths)
            print(f"=> {path} ({evidence['features']} features; atomic readback verified)", file=log)
        else:
            if any(path.resolve(strict=False) == Path(source).resolve(strict=False) for source in protected_paths):
                raise ValueError(f"Output must not replace an input dataset: {path}")
            if path.exists() and not overwrite:
                raise FileExistsError(f"Output already exists; pass --overwrite to replace it: {path}")
            gdf.to_file(path, engine="pyogrio")
            print("Warning: this multi-file/legacy format output is not atomic.", file=log)
            print(f"=> {path}", file=log)


def _finish_parcels(result, columns, args, analysis_crs, measure: AreaMeasure, report):
    report.update(analysis_crs=str(analysis_crs), analysis_method=measure.method)
    rows = result[columns].to_dict("records")
    if not args.json:
        print(tabulate(rows, headers="keys", tablefmt="simple", floatfmt=".4f"))
        units = f"; meters_per_unit={measure.factor:.12g}" if measure.factor is not None else ""
        print(f"analysis_crs={analysis_crs}; method={measure.method}{units}; assignment={args.assignment}")
        print("Building assignment report: " + json.dumps(report, ensure_ascii=False, sort_keys=True))
    _write_assignment_report(report, args)
    protected = (args.parcels, args.buildings) + ((args.green,) if getattr(args, "green", None) else ())
    _write_result(result, args.output, overwrite=args.overwrite, protected_paths=protected, log=sys.stderr if args.json else None)
    if args.json:
        print(json.dumps(clean({"rows": rows, "assignment_report": report, "meters_per_unit": measure.factor}), ensure_ascii=False, allow_nan=False))


def _write_assignment_report(report: dict[str, object], args: argparse.Namespace) -> None:
    if args.report_json:
        path = Path(args.report_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        protected = [Path(args.parcels), Path(args.buildings)]
        if getattr(args, "green", None):
            protected.append(Path(args.green))
        if any(path.resolve(strict=False) == source.resolve(strict=False) for source in protected):
            raise ValueError(f"Report path must not replace an input dataset: {path}")
        if getattr(args, "output", None) and path.resolve(strict=False) == Path(args.output).resolve(strict=False):
            raise ValueError("Report JSON path must differ from the vector/CSV output path")
        data = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if path.exists() and (path.is_symlink() or not path.is_file() or not getattr(args, "overwrite", False)):
            raise FileExistsError(f"Report already exists; pass --overwrite to replace it: {path}")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix="." + path.name + ".", suffix=".tmp", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            if getattr(args, "overwrite", False):
                os.replace(temporary, path)
            else:
                os.link(temporary, path)
                temporary.unlink()
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def _check_parcels(parcels: gpd.GeoDataFrame, parcel_id: str) -> None:
    if parcel_id not in parcels.columns:
        raise ValueError(f"Parcel ID field {parcel_id!r} not found")
    if parcels[parcel_id].isna().any() or parcels[parcel_id].duplicated().any():
        raise ValueError("Parcel ID field must be unique and non-null")


def _reject_overlapping_parcels(parcels: gpd.GeoDataFrame, measure: AreaMeasure) -> None:
    """Reject positive-area parcel overlap; boundary touches remain valid."""
    index = parcels.sindex
    for i, geom in enumerate(parcels.geometry):
        for j in index.query(geom, predicate="intersects"):
            j = int(j)
            if j <= i:
                continue
            overlap_m2 = measure(geom.intersection(parcels.geometry.iloc[j]))
            if overlap_m2 > 0:
                raise ValueError(f"Parcels at source rows {i} and {j} overlap by {overlap_m2:.9g} m²")


def _floor_counts(buildings: gpd.GeoDataFrame, floors_field: str) -> list[float]:
    if floors_field not in buildings.columns:
        raise ValueError(f"Floor field {floors_field!r} not found in buildings")
    floors: list[float] = []
    for i, raw in enumerate(buildings[floors_field]):
        try:
            value = float(raw)
        except (TypeError, ValueError, OverflowError):
            raise ValueError(f"Invalid floor count at building source row {i}: {raw!r}")
        if not math.isfinite(value) or value <= 0 or not math.isclose(value, round(value), abs_tol=1e-9):
            raise ValueError(f"Floor counts must be positive finite integers; invalid row {i}: {raw!r}")
        floors.append(value)
    return floors


def _prepare(
    parcels_source: gpd.GeoDataFrame,
    buildings_source: gpd.GeoDataFrame,
    parcel_id: str,
    analysis_crs: str | None,
    geodesic: bool,
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, AreaMeasure]:
    _check_parcels(parcels_source, parcel_id)
    (parcels, buildings), measure = area_frames([parcels_source, buildings_source], analysis_crs, geodesic=geodesic)
    parcel_areas = measure.areas(parcels.geometry)
    if (parcel_areas <= 0).any():
        raise ValueError("Parcel geometries must have positive area")
    _reject_overlapping_parcels(parcels, measure)
    return parcels, buildings, measure


def _allocate_buildings(
    parcels: gpd.GeoDataFrame,
    buildings: gpd.GeoDataFrame,
    measure: AreaMeasure,
    assignment: str,
    floors: list[float] | None = None,
) -> tuple[list[float], list[float], dict[str, object]]:
    if assignment not in {"area", "unique"}:
        raise ValueError("assignment must be area or unique")
    n = len(parcels)
    building_area = [0.0] * n
    floor_area = [0.0] * n
    parcel_index = parcels.sindex
    report: dict[str, object] = {
        "assignment": assignment,
        "building_count": len(buildings),
        "matched_buildings": 0,
        "unmatched_buildings": 0,
        "partially_unallocated_buildings": 0,
        "source_footprint_area_m2": 0.0,
        "allocated_footprint_area_m2": 0.0,
        "unallocated_footprint_area_m2": 0.0,
    }
    for bpos, geom in enumerate(buildings.geometry):
        source_area = measure(geom)
        if not math.isfinite(source_area) or source_area <= 0:
            raise ValueError(f"Building source row {bpos} must have positive finite area")
        report["source_footprint_area_m2"] += source_area
        intersections: list[tuple[int, float]] = []
        for raw_pos in parcel_index.query(geom, predicate="intersects"):
            ppos = int(raw_pos)
            overlap = measure(geom.intersection(parcels.geometry.iloc[ppos]))
            if overlap > 0:
                intersections.append((ppos, overlap))
        if not intersections:
            report["unmatched_buildings"] += 1
            report["unallocated_footprint_area_m2"] += source_area
            continue
        report["matched_buildings"] += 1
        if assignment == "unique":
            # `max` keeps the first source parcel on equal overlap areas.
            ppos, _ = max(intersections, key=lambda item: (item[1], -item[0]))
            allocation = [(ppos, source_area)]
        else:
            allocated = sum(area for _, area in intersections)
            allocation = intersections
            remainder = source_area - allocated
            if measure.factor is None:
                # A cut introduces vertices on a geodesic edge. Use piece areas as
                # weights (including the uncovered piece) to conserve the original footprint.
                covered = unary_union([parcels.geometry.iloc[i] for i, _ in intersections])
                uncovered = measure(geom.difference(covered))
                scale = source_area / (allocated + uncovered)
                allocation = [(i, area * scale) for i, area in intersections]
                remainder = uncovered * scale
            if remainder > 1e-7:
                report["partially_unallocated_buildings"] += 1
                report["unallocated_footprint_area_m2"] += remainder
        for ppos, area_m2 in allocation:
            building_area[ppos] += area_m2
            if floors is not None:
                floor_area[ppos] += area_m2 * floors[bpos]
    source_total = report["source_footprint_area_m2"]
    if assignment == "area" and sum(building_area) > source_total + max(1e-7, source_total * 1e-12):
        raise ValueError("Allocated building area exceeds source building area; parcel overlap may be present")
    report["allocated_footprint_area_m2"] = sum(building_area)
    return building_area, floor_area, report


def _base_result(source_parcels: gpd.GeoDataFrame, analysis_parcels: gpd.GeoDataFrame, measure: AreaMeasure) -> gpd.GeoDataFrame:
    result = source_parcels.copy()
    result["parcel_area_m2"] = measure.areas(analysis_parcels.geometry).to_numpy()
    if "area_method" in result:
        raise ValueError("Area method field collision: area_method")
    result["area_method"] = measure.method
    return result


def cmd_far(args: argparse.Namespace) -> None:
    parcels_source, buildings_source = _read(args.parcels, args.parcels_layer), _read(args.buildings, args.buildings_layer)
    _check_parcels(parcels_source, args.parcel_id)
    floors = _floor_counts(buildings_source, args.floors_field)
    parcels, buildings, measure = _prepare(parcels_source, buildings_source, args.parcel_id, args.analysis_crs, args.geodesic)
    _, floor_area, assignment_report = _allocate_buildings(parcels, buildings, measure, args.assignment, floors)
    result = _base_result(parcels_source, parcels, measure)
    result["total_floor_area_m2"] = floor_area
    result["FAR"] = result["total_floor_area_m2"] / result["parcel_area_m2"]
    _finish_parcels(result, [args.parcel_id, "parcel_area_m2", "total_floor_area_m2", "FAR"], args, parcels.crs, measure, assignment_report)


def cmd_density(args: argparse.Namespace) -> None:
    parcels_source, buildings_source = _read(args.parcels, args.parcels_layer), _read(args.buildings, args.buildings_layer)
    parcels, buildings, measure = _prepare(parcels_source, buildings_source, args.parcel_id, args.analysis_crs, args.geodesic)
    building_area, _, assignment_report = _allocate_buildings(parcels, buildings, measure, args.assignment)
    result = _base_result(parcels_source, parcels, measure)
    result["building_area_m2"] = building_area
    result["density"] = result["building_area_m2"] / result["parcel_area_m2"]
    _finish_parcels(result, [args.parcel_id, "parcel_area_m2", "building_area_m2", "density"], args, parcels.crs, measure, assignment_report)


def _green_by_parcel(
    parcels: gpd.GeoDataFrame, greens: gpd.GeoDataFrame, measure: AreaMeasure
) -> list[float]:
    index = greens.sindex
    values: list[float] = []
    for parcel in parcels.geometry:
        clipped = [parcel.intersection(greens.geometry.iloc[int(i)]) for i in index.query(parcel, predicate="intersects")]
        clipped = [geom for geom in clipped if not geom.is_empty and geom.area > 0]
        values.append(measure(unary_union(clipped)) if clipped else 0.0)
    return values


def cmd_coverage(args: argparse.Namespace) -> None:
    boundary_source, target_source = _read(args.boundary, args.boundary_layer), _read(args.target, args.target_layer)
    (boundary, targets), measure = area_frames([boundary_source, target_source], args.analysis_crs, geodesic=args.geodesic)
    study = unary_union(boundary.geometry.tolist())
    study_area_m2 = measure(study)
    if not math.isfinite(study_area_m2) or study_area_m2 <= 0:
        raise ValueError("Boundary must have positive finite area")
    clipped = [geom.intersection(study) for geom in targets.geometry]
    green_area_m2 = measure(unary_union([g for g in clipped if not g.is_empty]))
    ratio = green_area_m2 / study_area_m2
    metrics = {"green_area_m2": green_area_m2, "study_area_m2": study_area_m2, "coverage_ratio": ratio}
    if not args.json:
        print(tabulate(metrics.items(), headers=["metric", "value"], tablefmt="simple", floatfmt=".6f"))
        units = f"; meters_per_unit={measure.factor:.12g}" if measure.factor is not None else ""
        print(f"analysis_crs={boundary.crs}; projection_method={measure.method}; measurement_method=union area{units}")
    if args.output:
        output = boundary_source.copy()
        if "area_method" in output:
            raise ValueError("Area method field collision: area_method")
        output["area_method"] = measure.method
        output["green_area_m2"] = green_area_m2
        output["study_area_m2"] = study_area_m2
        output["coverage_ratio"] = ratio
        _write_result(output, args.output, overwrite=args.overwrite, protected_paths=(args.boundary, args.target), log=sys.stderr if args.json else None)
    if args.json:
        print(json.dumps({**metrics, "analysis_crs": str(boundary.crs), "analysis_method": measure.method,
                          "measurement_method": "union area", "meters_per_unit": measure.factor}, allow_nan=False))


def cmd_summary(args: argparse.Namespace) -> None:
    parcels_source, buildings_source = _read(args.parcels, args.parcels_layer), _read(args.buildings, args.buildings_layer)
    _check_parcels(parcels_source, args.parcel_id)
    floors = _floor_counts(buildings_source, args.floors_field)
    parcels, buildings, measure = _prepare(parcels_source, buildings_source, args.parcel_id, args.analysis_crs, args.geodesic)
    building_area, floor_area, assignment_report = _allocate_buildings(parcels, buildings, measure, args.assignment, floors)
    result = _base_result(parcels_source, parcels, measure)
    result["building_area_m2"] = building_area
    result["total_floor_area_m2"] = floor_area
    result["FAR"] = result["total_floor_area_m2"] / result["parcel_area_m2"]
    result["density"] = result["building_area_m2"] / result["parcel_area_m2"]
    if args.green:
        green_source = _read(args.green, args.green_layer)
        (parcels_for_green, greens), _ = area_frames([parcels_source, green_source], args.analysis_crs, geodesic=args.geodesic)
        if parcels_for_green.crs != parcels.crs:
            raise ValueError("Green and parcel analysis CRS differ; specify a suitable --analysis-crs")
        result["green_area_m2"] = _green_by_parcel(parcels, greens, measure)
        result["green_ratio"] = result["green_area_m2"] / result["parcel_area_m2"]
    columns = [args.parcel_id, "parcel_area_m2", "building_area_m2", "total_floor_area_m2", "FAR", "density"]
    if args.green:
        columns.append("green_ratio")
    _finish_parcels(result, columns, args, parcels.crs, measure, assignment_report)


def _add_parcel_options(parser: argparse.ArgumentParser, include_floors: bool = False) -> None:
    parser.add_argument("--json", action="store_true", help="Pure JSON on stdout")
    parser.add_argument("--parcels", required=True)
    parser.add_argument("--parcels-layer")
    parser.add_argument("--parcel-id", required=True)
    parser.add_argument("--buildings", required=True)
    parser.add_argument("--buildings-layer")
    area_mode = parser.add_mutually_exclusive_group()
    area_mode.add_argument("--analysis-crs", help="Explicit projected CRS for measurement")
    area_mode.add_argument("--geodesic", action="store_true", help="Measure areas on the source ellipsoid")
    parser.add_argument("--assignment", choices=["area", "unique"], default="area", help="Area-proportional or whole-building parcel assignment")
    parser.add_argument("--output")
    parser.add_argument("--overwrite", action="store_true", help="Replace existing output where supported")
    parser.add_argument("--report-json", help="Optional building assignment report path")
    if include_floors:
        parser.add_argument("--floors-field", required=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Calculate area-based urban design indicators")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("far", help="Floor Area Ratio")
    _add_parcel_options(p, include_floors=True)
    p = sub.add_parser("density", help="Building footprint density")
    _add_parcel_options(p)
    p = sub.add_parser("summary", help="All parcel indicators")
    _add_parcel_options(p, include_floors=True)
    p.add_argument("--green")
    p.add_argument("--green-layer")
    p = sub.add_parser("coverage", help="Target coverage within a study-area boundary")
    p.add_argument("--json", action="store_true", help="Pure JSON on stdout")
    p.add_argument("--target", required=True)
    p.add_argument("--target-layer")
    p.add_argument("--boundary", required=True)
    p.add_argument("--boundary-layer")
    area_mode = p.add_mutually_exclusive_group()
    area_mode.add_argument("--analysis-crs", help="Explicit projected CRS for measurement")
    area_mode.add_argument("--geodesic", action="store_true", help="Measure areas on the source ellipsoid")
    p.add_argument("--output")
    p.add_argument("--overwrite", action="store_true", help="Replace an existing output where supported")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        {"far": cmd_far, "density": cmd_density, "summary": cmd_summary, "coverage": cmd_coverage}[args.command](args)
    except (ValueError, TypeError, OverflowError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    import _stdio
    _stdio.utf8()
    main()
