"""Transactional single-file vector output helpers shared by GIS scripts."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Iterable

import pandas as pd
import pyogrio
from pyproj import CRS


_DRIVERS = {
    ".gpkg": "GPKG",
    ".geojson": "GeoJSON",
    ".json": "GeoJSON",
    ".fgb": "FlatGeobuf",
}
_UNSET = object()


def _same_path(first: Path, second: Path) -> bool:
    try:
        if first.exists() and second.exists() and os.path.samefile(first, second):
            return True
    except OSError:
        pass
    return first.resolve(strict=False) == second.resolve(strict=False)


def _has_path(path: Path) -> bool:
    return os.path.lexists(path)


def _check_target(
    path: Path,
    *,
    overwrite: bool,
    protected_paths: Iterable[str | Path] = (),
) -> None:
    if path.suffix.lower() not in _DRIVERS:
        supported = ", ".join(sorted(_DRIVERS))
        raise ValueError(
            f"Safe atomic output supports single-file formats ({supported}); got {path.suffix or '<no extension>'}"
        )

    for protected in protected_paths:
        if protected is None:
            continue
        protected_path = Path(protected)
        if _same_path(path, protected_path):
            raise ValueError(f"Output must not replace an input dataset: {path}")

    if not _has_path(path):
        return
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Refusing to replace a non-regular output path: {path}")
    if not overwrite:
        raise FileExistsError(f"Output already exists; pass --overwrite to replace it: {path}")
    if path.suffix.lower() == ".gpkg":
        sidecars = [Path(f"{path}{suffix}") for suffix in ("-wal", "-shm", "-journal")]
        if any(_has_path(sidecar) for sidecar in sidecars):
            raise ValueError(f"Refusing to replace a GeoPackage with active SQLite sidecars: {path}")
        layers = pyogrio.list_layers(path)
        if len(layers) != 1:
            raise ValueError(
                f"Refusing to replace a GeoPackage with {len(layers)} layers; this tool writes one complete layer/file: {path}"
            )


def _temporary_path(path: Path) -> Path:
    token = uuid.uuid4().hex
    return path.with_name(f".{path.stem}.{token}.tmp{path.suffix}")


def _cleanup(path: Path) -> None:
    for candidate in (
        path,
        Path(f"{path}-wal"),
        Path(f"{path}-shm"),
        Path(f"{path}-journal"),
    ):
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            pass


def iter_vector_chunks(
    path: str | Path,
    *,
    layer: str | None = None,
    chunk_size: int = 100_000,
    **read_kwargs,
):
    """Yield ``(offset, frame)`` pieces of a vector layer in file order.

    Each frame keeps the RangeIndex a full read would give its rows, so callers can
    compare it with ``expected.iloc[offset:offset + len(frame)]``. At least one
    (possibly empty) frame is yielded, so CRS and columns are always observable.
    """
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    offset = 0
    while True:
        frame = pyogrio.read_dataframe(Path(path), layer=layer, skip_features=offset, max_features=chunk_size, **read_kwargs)
        frame.index = pd.RangeIndex(offset, offset + len(frame))
        yield offset, frame
        offset += len(frame)
        if len(frame) < chunk_size:
            return


# Drivers that append in place; GDAL's GeoJSON/FlatGeobuf append reloads the whole file.
STREAM_DRIVERS = frozenset({"GPKG", "ESRI Shapefile"})
_MULTI = {frozenset({"Point", "MultiPoint"}): "MultiPoint",
          frozenset({"LineString", "MultiLineString"}): "MultiLineString",
          frozenset({"Polygon", "MultiPolygon"}): "MultiPolygon"}


def full_read_plan(path: str | Path, *, driver: str, layer: str | None = None, chunk_size: int = 100_000, **read_kwargs):
    """In one bounded pass, decide what one full read and write would: column dtypes and layer geometry type.

    pyogrio types a column by whether the whole read has nulls (an Integer field with any null
    becomes float64), and infers the layer geometry type from every geometry, so chunk-by-chunk
    writes must be given both explicitly. Returns None when the chunks cannot be reconciled.
    """
    from pyogrio.geopandas import DRIVERS_NO_MIXED_DIMENSIONS, DRIVERS_NO_MIXED_SINGLE_MULTI
    with_null: dict[str, set] = {}
    without_null: dict[str, set] = {}
    types: set[str] = set()
    has_z, all_z, geometry = False, True, None
    for _, frame in iter_vector_chunks(path, layer=layer, chunk_size=chunk_size, **read_kwargs):
        geometry = frame.geometry.name if hasattr(frame, "geometry") else None
        for column in frame.columns.drop(geometry) if geometry else frame.columns:
            target = with_null if frame[column].isna().any() else without_null
            target.setdefault(column, set()).add(frame[column].dtype)
        if geometry:
            series = frame.geometry
            z = series[series.notna() & ~series.is_empty].has_z
            has_z, all_z = has_z or bool(z.any()), all_z and bool(z.all())
            types.update(series.geom_type.dropna().unique())
    dtypes = {}
    for column in with_null.keys() | without_null.keys():
        seen = with_null.get(column) or without_null[column]
        if len(seen) != 1:
            return None
        dtypes[column] = next(iter(seen))
    plan = {"dtypes": dtypes, "geometry_type": None, "promote_to_multi": None}
    if geometry:
        if driver in DRIVERS_NO_MIXED_DIMENSIONS and has_z and not all_z:
            raise ValueError(f"Mixed 2D and 3D coordinates are not supported by {driver}")
        kind, promote = "Unknown", False
        if len(types) == 1:
            kind = next(iter(types))
        elif frozenset(types) in _MULTI and driver in DRIVERS_NO_MIXED_SINGLE_MULTI:
            kind, promote = _MULTI[frozenset(types)], True
        plan.update(geometry_type=f"{kind} Z" if has_z and kind != "Unknown" else kind, promote_to_multi=promote)
    return plan


def stream_vector(path: str | Path, write, *, driver: str | None, layer: str | None = None,
                  transform=None, chunk_size: int = 100_000, **read_kwargs) -> int | None:
    """Copy a layer chunk by chunk through ``write(frame, first, **write_options)``; return the feature count.

    Returns None without writing when one full read is the right path instead: the layer fits in
    one chunk, the driver cannot append in place, or :func:`full_read_plan` cannot reconcile chunks.
    """
    if driver not in STREAM_DRIVERS:
        return None
    count = int(pyogrio.read_info(Path(path), layer=layer).get("features", -1))
    if count <= chunk_size:
        return None
    plan = full_read_plan(path, driver=driver, layer=layer, chunk_size=chunk_size, **read_kwargs)
    if plan is None:
        return None
    options = {k: plan[k] for k in ("geometry_type", "promote_to_multi") if plan[k] is not None}
    written = 0
    for offset, frame in iter_vector_chunks(path, layer=layer, chunk_size=chunk_size, **read_kwargs):
        changed = {c: t for c, t in plan["dtypes"].items() if frame[c].dtype != t}
        if changed:
            frame = frame.astype(changed)
        if transform is not None:
            frame = transform(frame)
        if len(frame) or offset == 0:
            write(frame, offset == 0, **options)
        written += len(frame)
    return written


def validate_vector_file(
    path: str | Path,
    *,
    layer: str | None = None,
    expected_features: int | None = None,
    required_fields: Iterable[str] = (),
    expected_crs=_UNSET,
    chunk_size: int = 100_000,
) -> dict:
    """Reopen a vector file and stream through it to validate its full row count."""
    dataset = Path(path)
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    info = pyogrio.read_info(dataset, layer=layer)
    reported = info.get("features", -1)
    if reported < 0:
        raise ValueError(f"Driver cannot verify feature count for output: {dataset}")
    fields = set(info.get("fields", ()))
    missing = set(required_fields) - fields
    if missing:
        raise ValueError(f"Output is missing fields: {', '.join(sorted(missing))}")
    if expected_crs is not _UNSET and expected_crs is not None:
        actual_crs = info.get("crs")
        if actual_crs is None or not CRS.from_user_input(actual_crs).equals(CRS.from_user_input(expected_crs)):
            raise ValueError(f"Output CRS does not match the requested CRS: {dataset}")
    elif expected_crs is None and info.get("crs") is not None:
        # An unreferenced input/output pair must stay unreferenced.
        raise ValueError(f"Output unexpectedly defines a CRS: {dataset}")
    if expected_features is not None and reported != expected_features:
        raise ValueError(f"Output feature count is {reported}, expected {expected_features}: {dataset}")

    actual = 0
    if reported == 0:
        # Also force a geometry/data read for empty layers, not metadata alone.
        pyogrio.read_dataframe(dataset, layer=layer, max_features=1)
    else:
        for start in range(0, reported, chunk_size):
            frame = pyogrio.read_dataframe(
                dataset,
                layer=layer,
                skip_features=start,
                max_features=min(chunk_size, reported - start),
            )
            actual += len(frame)
    if actual != reported:
        raise ValueError(f"Readback yielded {actual} features, metadata reports {reported}: {dataset}")
    return {"features": actual, "fields": sorted(fields), "crs": info.get("crs")}


class AtomicVectorOutput:
    """Write to a same-directory staging file, verify it, then atomically publish."""

    def __init__(
        self,
        path: str | Path,
        *,
        overwrite: bool = False,
        protected_paths: Iterable[str | Path] = (),
    ) -> None:
        self.path = Path(path)
        self.overwrite = overwrite
        self.protected_paths = tuple(path for path in protected_paths if path is not None)
        self.temp_path = _temporary_path(self.path)
        self.driver = _DRIVERS.get(self.path.suffix.lower())
        self.layer = self.path.stem if self.driver == "GPKG" else None
        self._entered = False
        self._published = False

    def __enter__(self) -> "AtomicVectorOutput":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _check_target(self.path, overwrite=self.overwrite, protected_paths=self.protected_paths)
        self._entered = True
        return self

    def write(self, frame, *, append: bool = False, **options) -> None:
        if not self._entered or self._published:
            raise RuntimeError("AtomicVectorOutput is not open for writing")
        if frame.columns.duplicated().any():
            raise ValueError("Duplicate output field names are forbidden")
        kwargs = {"driver": self.driver, "append": append, **options}
        if self.layer is not None:
            kwargs["layer"] = self.layer
        elif self.driver == "GeoJSON":
            # GeoJSON stores its layer name in the file; without one GDAL writes the random temporary
            # name, so the same output would differ from run to run.
            kwargs["layer"] = self.path.stem
        pyogrio.write_dataframe(frame, self.temp_path, **kwargs)

    def commit(
        self,
        *,
        expected_features: int,
        required_fields: Iterable[str] = (),
        expected_crs=_UNSET,
    ) -> dict:
        if not self._entered or self._published:
            raise RuntimeError("AtomicVectorOutput cannot be committed in its current state")
        evidence = validate_vector_file(
            self.temp_path,
            layer=self.layer,
            expected_features=expected_features,
            required_fields=required_fields,
            expected_crs=expected_crs,
        )
        # Recheck at publication to avoid clobbering a file created during processing.
        _check_target(self.path, overwrite=self.overwrite, protected_paths=self.protected_paths)
        if self.overwrite:
            os.replace(self.temp_path, self.path)
        else:
            # A same-directory hard link is an atomic no-clobber publish on the same volume.
            os.link(self.temp_path, self.path)
            self.temp_path.unlink()
        self._published = True
        return evidence

    def __exit__(self, exc_type, exc, traceback) -> bool:
        _cleanup(self.temp_path)
        return False


def write_vector_atomic(
    frame,
    path: str | Path,
    *,
    overwrite: bool = False,
    protected_paths: Iterable[str | Path] = (),
    required_fields: Iterable[str] | None = None,
) -> dict:
    """Write one complete vector layer safely and return readback evidence."""
    output = AtomicVectorOutput(path, overwrite=overwrite, protected_paths=protected_paths)
    with output:
        output.write(frame)
        fields = required_fields
        if fields is None:
            fields = [column for column in frame.columns if column != frame.geometry.name]
        return output.commit(
            expected_features=len(frame),
            required_fields=fields,
            expected_crs=frame.crs,
        )
