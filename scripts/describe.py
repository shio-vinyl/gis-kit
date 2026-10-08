# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "pyogrio>=0.10",
#     "pyproj>=3.6",
#     "rasterio>=1.3",
# ]
# ///
"""Describe a GIS file from headers only (CRS, extent, layers, fields, bands, NoData); no features or pixels are read."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

RASTER_SUFFIXES = {'.tif', '.tiff', '.vrt', '.img', '.asc', '.jp2', '.nc', '.dem', '.hgt'}
LAYER_LIMIT = 20
FIELD_LIMIT = 50


def crs_card(crs) -> dict | None:
    """CRS identity and units via pyproj; None when the source has no CRS."""
    if crs is None:
        return None
    from pyproj import CRS
    parsed = CRS.from_user_input(crs)
    authority = parsed.to_authority(min_confidence=70)
    axis = parsed.axis_info[0] if parsed.axis_info else None
    return {'id': ':'.join(authority) if authority else None, 'name': parsed.name,
            'geographic': parsed.is_geographic, 'units': axis.unit_name if axis else None}


def _extent(bounds) -> list[float] | None:
    if bounds is None:
        return None
    values = [float(v) for v in bounds]
    return values if len(values) == 4 and all(math.isfinite(v) for v in values) else None


def describe_vector(path: Path) -> tuple[dict, list[dict]]:
    import pyogrio
    names = [str(row[0]) for row in pyogrio.list_layers(path)]
    layers, warnings, driver = [], [], None
    for name in names[:LAYER_LIMIT]:
        info = pyogrio.read_info(path, layer=name)  # fast counts/bounds only; -1/None when unknown
        driver = driver or info.get('driver')
        # pandas reports strings as 'object'; the OGR type name is clearer there.
        fields = [{'name': str(n), 'type': str(t) if str(t) != 'object' else str(o).removeprefix('OFT').lower()}
                  for n, t, o in zip(info['fields'], info['dtypes'], info['ogr_types'])]
        count = int(info['features'])
        crs = crs_card(info['crs'])
        geometry = info.get('geometry_type')
        layers.append({'name': name, 'geometry_type': geometry, 'feature_count': count if count >= 0 else None,
                       'crs': crs, 'extent': _extent(info.get('total_bounds')),
                       'fields': fields[:FIELD_LIMIT], 'field_count': len(fields)})
        if crs is None and geometry:
            warnings.append({'code': 'crs_missing', 'message': f'图层 {name} 没有 CRS'})
    return {'driver': driver or 'unknown', 'layers': layers, 'layer_count': len(names)}, warnings


def describe_raster(path: Path) -> tuple[dict, list[dict]]:
    import rasterio
    warnings = []
    with rasterio.open(path) as src:
        crs = crs_card(src.crs.to_wkt()) if src.crs else None
        # JSON has no NaN: a NaN NoData is reported as the string "nan", unset as null.
        nodata = [None if v is None else 'nan' if math.isnan(v) else float(v) for v in src.nodatavals]
        raster = {'width': src.width, 'height': src.height, 'band_count': src.count,
                  'dtypes': list(src.dtypes), 'nodata': nodata, 'crs': crs,
                  'resolution': [float(src.res[0]), float(src.res[1])] if src.transform.is_rectilinear else None,
                  'extent': _extent(src.bounds)}
        driver = src.driver
    if crs is None:
        warnings.append({'code': 'crs_missing', 'message': '栅格没有 CRS'})
    if all(v is None for v in nodata):
        warnings.append({'code': 'nodata_unset', 'message': '所有波段都未设置 NoData'})
    return {'driver': driver, 'raster': raster}, warnings


def describe(path: Path) -> dict:
    stat = path.stat()
    kind = 'raster' if path.suffix.lower() in RASTER_SUFFIXES else 'vector'
    body, warnings = (describe_raster if kind == 'raster' else describe_vector)(path)
    return {'contract': 'describe', 'schema_version': 1, 'path': str(path), 'kind': kind,
            'driver': body.pop('driver'), 'revision': {'size': stat.st_size if path.is_file() else None,
                                                       'mtime': stat.st_mtime},
            **body, 'warnings': warnings}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', help='GIS file or dataset directory (e.g. .gdb)')
    parser.add_argument('--json', action='store_true', required=True, help='Print the describe card as JSON')
    args = parser.parse_args(argv)
    path = Path(args.path).expanduser().resolve()
    if not path.exists():
        parser.exit(2, f'ERROR: path not found: {path}\n')
    try:
        card = describe(path)
    except Exception as error:  # driver errors come back as a plain message, not a traceback
        parser.exit(1, f'ERROR: cannot describe {path}: {error}\n')
    print(json.dumps(card, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
