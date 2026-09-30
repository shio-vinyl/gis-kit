# /// script
# requires-python = ">=3.11"
# dependencies = ["geopandas>=1.0", "pyogrio>=0.10"]
# ///
"""Spatial join with stable source identity, explicit aggregation, and meter distances."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
import tempfile

import geopandas as gpd
import numpy as np
import pandas as pd

try:
    from _metric import analysis_frames, analysis_method
except ImportError:  # Support direct module loading by tests.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _metric import analysis_frames, analysis_method

try:
    from _safe_io import write_vector_atomic
except ImportError:
    if str(Path(__file__).resolve().parent) not in sys.path:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _safe_io import write_vector_atomic


RESERVED = {
    "left_source_id", "right_source_id", "match_count", "matched_source_ids", "distance_m",
    "_join_left_position", "_join_right_position", "geometry",
}


def _read(path: str, layer: str | None) -> gpd.GeoDataFrame:
    kwargs = {"engine": "pyogrio"}
    if layer:
        kwargs["layer"] = layer
    return gpd.read_file(path, **kwargs).reset_index(drop=True)


def _id_values(frame: gpd.GeoDataFrame, field: str | None, side: str) -> list[object]:
    if field:
        if field not in frame.columns:
            raise ValueError(f"{side} ID field not found: {field}")
        values = frame[field].tolist()
        if any(pd.isna(value) for value in values) or len(set(values)) != len(values):
            raise ValueError(f"{side} ID field must be unique and non-null")
        return values
    return [f"{side}:{position}" for position in range(len(frame))]


def _assert_output_names(left: gpd.GeoDataFrame, right: gpd.GeoDataFrame) -> None:
    for side, frame in (("left", left), ("right", right)):
        reserved = RESERVED - {"geometry"}
        conflicts = set(reserved.intersection(frame.columns))
        if frame.geometry.name != "geometry" and "geometry" in frame.columns:
            conflicts.add("geometry")
        conflicts = sorted(conflicts)
        if conflicts:
            raise ValueError(f"{side} input uses reserved output field name(s): {', '.join(conflicts)}")


def _analysis_pair(
    left: gpd.GeoDataFrame, right: gpd.GeoDataFrame, analysis_crs: str | None
) -> tuple[gpd.GeoDataFrame, gpd.GeoDataFrame, float]:
    if left.crs is None or right.crs is None:
        raise ValueError("Both join inputs must have known CRS")
    (left_projected, right_projected), factor = analysis_frames([left, right], analysis_crs)
    return left_projected.reset_index(drop=True), right_projected.reset_index(drop=True), factor


def _json_value(value: object) -> object:
    if isinstance(value, np.generic):
        return value.item()
    return value


def _write_report_json(path_value: str, report: dict[str, object], args: argparse.Namespace) -> None:
    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    protected = [Path(args.left), Path(args.right), Path(args.output)]
    if any(path.resolve(strict=False) == source.resolve(strict=False) for source in protected):
        raise ValueError("--report-json must not replace an input or vector output")
    if path.exists() and (path.is_symlink() or not path.is_file() or not args.overwrite):
        raise FileExistsError(f"Report already exists; pass --overwrite to replace it: {path}")
    data = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix="." + path.name + ".", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if args.overwrite:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)
            temporary.unlink()
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _selected_fields(frame: gpd.GeoDataFrame, selection: str | None, label: str) -> list[str]:
    available = [name for name in frame.columns if name != frame.geometry.name]
    if not selection:
        return available
    fields = [name.strip() for name in selection.split(",") if name.strip()]
    if len(set(fields)) != len(fields):
        raise ValueError("--fields contains duplicate field names")
    missing = [name for name in fields if name not in available]
    if missing:
        raise ValueError(f"Unknown {label} field(s): {', '.join(missing)}")
    return fields


def _renamed_fields(
    imported: list[str], base_columns: list[str], suffix: str, base_geom_name: str
) -> dict[str, str]:
    occupied = set(base_columns) | RESERVED | {"geometry", base_geom_name}
    result: dict[str, str] = {}
    for name in imported:
        candidate = name
        if candidate in occupied:
            candidate = name + suffix
        if candidate in occupied or candidate in result.values():
            raise ValueError(f"Field collision for {name!r}; choose a non-conflicting --suffix")
        result[name] = candidate
        occupied.add(candidate)
    return result


def _pairs(
    left: gpd.GeoDataFrame,
    right: gpd.GeoDataFrame,
    predicate: str,
    max_distance_m: float | None,
    meters_per_unit: float,
    how: str,
) -> list[tuple[int, int, float | None]]:
    left = left.copy()
    right = right.copy()
    left["_join_left_position"] = np.arange(len(left), dtype=int)
    right["_join_right_position"] = np.arange(len(right), dtype=int)
    if predicate == "nearest":
        kwargs = {"how": "inner", "distance_col": "_join_distance_units"}
        if max_distance_m is not None:
            kwargs["max_distance"] = max_distance_m / meters_per_unit
        if how == "right":
            # Right join means each right-side feature searches for its nearest
            # left counterpart, not the reverse relation grouped by right.
            matched = gpd.sjoin_nearest(right, left, **kwargs)
        else:
            matched = gpd.sjoin_nearest(left, right, **kwargs)
    else:
        matched = gpd.sjoin(left, right, how="inner", predicate=predicate)
    pairs = []
    for _, row in matched.iterrows():
        # Position columns retain their source-side names even when the
        # nearest search runs right-to-left for --how right.
        lpos, rpos = int(row["_join_left_position"]), int(row["_join_right_position"])
        distance = float(row["_join_distance_units"] * meters_per_unit) if predicate == "nearest" else None
        pairs.append((lpos, rpos, distance))
    return sorted(pairs, key=lambda pair: (pair[0], pair[1], pair[2] if pair[2] is not None else -1))


def _aggregate(
    args: argparse.Namespace,
    left: gpd.GeoDataFrame,
    right: gpd.GeoDataFrame,
    left_ids: list[object],
    right_ids: list[object],
    pairs: list[tuple[int, int, float | None]],
    imported: list[str],
    output_names: dict[str, str],
    original_left_crs: object,
    original_right_crs: object,
    analysis_crs: object,
) -> tuple[gpd.GeoDataFrame, dict[str, object]]:
    primary_is_left = args.how != "right"
    primary, counterpart = (left, right) if primary_is_left else (right, left)
    primary_ids, counterpart_ids = (left_ids, right_ids) if primary_is_left else (right_ids, left_ids)
    primary_id_key = "left_source_id" if primary_is_left else "right_source_id"
    counterpart_id_key = "right_source_id" if primary_is_left else "left_source_id"
    # `--fields` always identifies the non-primary fields to bring across.
    by_primary: dict[int, list[tuple[int, int, float | None]]] = {i: [] for i in range(len(primary))}
    for pair in pairs:
        ppos, cpos = (pair[0], pair[1]) if primary_is_left else (pair[1], pair[0])
        by_primary[ppos].append((ppos, cpos, pair[2]))

    records: list[dict[str, object]] = []
    geometry_values = []
    primary_attrs = [name for name in primary.columns if name != primary.geometry.name]
    for ppos, matches in by_primary.items():
        matches.sort(key=lambda item: item[1])
        if args.how == "inner" and not matches:
            continue
        if args.agg == "all":
            chosen: list[tuple[int, int, float | None]] = matches or [(ppos, -1, None)]
        else:
            chosen = matches[:1] if args.agg == "first" and matches else [(ppos, -1, None)]
        for _, cpos, distance in chosen:
            record = {name: primary.iloc[ppos][name] for name in primary_attrs}
            matched_ids = [counterpart_ids[item[1]] for item in matches]
            record[primary_id_key] = primary_ids[ppos]
            record["match_count"] = len(matches)
            record["matched_source_ids"] = json.dumps([_json_value(value) for value in matched_ids], ensure_ascii=False)
            record[counterpart_id_key] = counterpart_ids[cpos] if cpos >= 0 and args.agg in {"first", "all"} else None
            if args.agg in {"first", "all"} and cpos >= 0:
                for source_name in imported:
                    record[output_names[source_name]] = counterpart.iloc[cpos][source_name]
            elif args.agg in {"first", "all"}:
                for source_name in imported:
                    record[output_names[source_name]] = None
            elif args.agg in {"sum", "mean"}:
                for source_name in imported:
                    values = [counterpart.iloc[item[1]][source_name] for item in matches]
                    numeric = pd.to_numeric(pd.Series(values), errors="coerce")
                    if args.agg == "sum":
                        value = numeric.sum(min_count=1)
                    else:
                        value = numeric.mean() if numeric.notna().any() else np.nan
                    record[output_names[source_name]] = value if not pd.isna(value) else None
            if args.agg == "count":
                for source_name in imported:
                    record[output_names[source_name]] = sum(
                        pd.notna(counterpart.iloc[item[1]][source_name]) for item in matches
                    )
            if args.predicate == "nearest" and args.distance_field:
                record[args.distance_field] = min((item[2] for item in matches if item[2] is not None), default=None)
            geometry_values.append(primary.geometry.iloc[ppos])
            records.append(record)

    output_crs = original_left_crs if primary_is_left else original_right_crs
    schema = list(primary_attrs)
    for name in [primary_id_key, "match_count", "matched_source_ids", counterpart_id_key]:
        if name not in schema:
            schema.append(name)
    for source_name in imported:
        output_name = output_names[source_name]
        if output_name not in schema:
            schema.append(output_name)
    if args.predicate == "nearest" and args.distance_field and args.distance_field not in schema:
        schema.append(args.distance_field)
    result_frame = pd.DataFrame(records, columns=schema)
    out = gpd.GeoDataFrame(result_frame, geometry=geometry_values, crs=output_crs)
    matched_left = {lpos for lpos, _, _ in pairs}
    matched_right = {rpos for _, rpos, _ in pairs}
    report = {
        "how": args.how,
        "predicate": args.predicate,
        "aggregation": args.agg,
        "left_features": len(left_ids),
        "right_features": len(right_ids),
        "match_pairs": len(pairs),
        "left_matched": len(matched_left),
        "left_unmatched": len(left_ids) - len(matched_left),
        "right_matched": len(matched_right),
        "right_unmatched": len(right_ids) - len(matched_right),
        "output_features": len(out),
        "max_distance_m": args.max_distance,
        "analysis_crs": str(analysis_crs),
        "analysis_method": analysis_method(left, args.analysis_crs),
    }
    return out, report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Spatial join and attribute attachment")
    parser.add_argument("--left", required=True, help="Left/target input layer")
    parser.add_argument("--right", required=True, help="Right/source input layer")
    parser.add_argument("--output", required=True, help="Output vector file")
    parser.add_argument("--left-layer")
    parser.add_argument("--right-layer")
    parser.add_argument("--how", choices=["inner", "left", "right"], default="left")
    parser.add_argument("--predicate", choices=["intersects", "within", "contains", "nearest"], default="intersects")
    parser.add_argument("--fields", help="Comma-separated counterpart fields to attach")
    parser.add_argument("--suffix", default="_right", help="Deterministic suffix for colliding fields")
    parser.add_argument("--agg", choices=["first", "count", "sum", "mean", "all"], default="first")
    parser.add_argument("--analysis-crs", help="Projected CRS for planar predicates and distances")
    parser.add_argument("--max-distance", type=float, help="Nearest-search maximum distance in meters")
    parser.add_argument("--distance-field", default="distance_m", help="Nearest distance result field, in meters")
    parser.add_argument("--left-id-field", help="Unique non-null ID field; default is stable source row position")
    parser.add_argument("--right-id-field", help="Unique non-null ID field; default is stable source row position")
    parser.add_argument("--report-json", help="Optional JSON match/unmatched report path")
    parser.add_argument("--overwrite", action="store_true", help="Replace an existing output where supported")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.max_distance is not None and (not math.isfinite(args.max_distance) or args.max_distance <= 0):
        parser.error("--max-distance must be a positive finite distance in meters")
    if args.max_distance is not None and args.predicate != "nearest":
        parser.error("--max-distance is valid only with --predicate nearest")
    if args.predicate == "nearest" and args.agg not in {"first", "count", "sum", "mean", "all"}:
        parser.error("Unsupported nearest aggregation")
    if not args.suffix or args.suffix == "_":
        parser.error("--suffix must be a non-empty deterministic suffix")
    if not args.distance_field:
        parser.error("--distance-field must be non-empty")

    try:
        left_source = _read(args.left, args.left_layer)
        right_source = _read(args.right, args.right_layer)
        _assert_output_names(left_source, right_source)
        left_ids = _id_values(left_source, args.left_id_field, "left")
        right_ids = _id_values(right_source, args.right_id_field, "right")
        left_analysis, right_analysis, factor = _analysis_pair(left_source, right_source, args.analysis_crs)

        primary_is_left = args.how != "right"
        counterpart_source = right_source if primary_is_left else left_source
        selection = _selected_fields(counterpart_source, args.fields, "counterpart")
        if args.agg in {"sum", "mean"}:
            nonnumeric = [name for name in selection if not pd.api.types.is_numeric_dtype(counterpart_source[name].dtype)]
            if nonnumeric:
                raise ValueError(f"{args.agg} requires numeric counterpart fields: {', '.join(nonnumeric)}")
        primary_source = left_source if primary_is_left else right_source
        output_names = _renamed_fields(selection, [c for c in primary_source.columns if c != primary_source.geometry.name], args.suffix, primary_source.geometry.name)
        if args.predicate == "nearest":
            primary_attrs = set(primary_source.columns) - {primary_source.geometry.name}
            conflicts = primary_attrs | set(output_names.values()) | (RESERVED - {"distance_m"})
            if args.distance_field in conflicts:
                raise ValueError(f"Distance result field {args.distance_field!r} conflicts with an existing/reserved field")
        pairs = _pairs(left_analysis, right_analysis, args.predicate, args.max_distance, factor, args.how)
        output, report = _aggregate(
            args, left_source, right_source, left_ids, right_ids, pairs, selection,
            output_names, left_source.crs, right_source.crs, left_analysis.crs,
        )
        print("Match report: " + json.dumps(report, ensure_ascii=False, sort_keys=True))
        if args.report_json:
            _write_report_json(args.report_json, report, args)
        evidence = write_vector_atomic(
            output,
            args.output,
            overwrite=args.overwrite,
            protected_paths=[args.left, args.right],
        )
        print(f"Written {evidence['features']} features to {args.output} in CRS {output.crs}")
    except (ValueError, TypeError, OverflowError, KeyError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
