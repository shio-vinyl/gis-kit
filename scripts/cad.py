#!/usr/bin/env python3
"""Inspect a DXF drawing before using it as GIS data: layers, entities, units, extent, candidate CRS and a check plot.

`cad.py inspect` reads model-space geometry through GDAL (block references expanded) and, when the optional
ezdxf is installed, inventories layers as CAD counts them: DXF entity types, closed and open polylines,
block names and attributes, raw and cleaned text, layer state, layouts and viewports. Without ezdxf the
inventory comes from GDAL and cad.json lists what is missing. It reads $ACADVER / $INSUNITS /
$DWGCODEPAGE from the header itself, and lists projected CRS
whose grid the coordinates fit, mainly zone-prefixed Chinese Gauss-Kruger grids. Coordinates without
the zone prefix are held: they may equally be a local grid, so no EPSG code is listed for them.
CAD files carry no CRS: every match is a candidate, the datum (CGCS2000, New Beijing, Xian 1980,
Beijing 1954) cannot be told apart from coordinates, and nothing is assigned to the data here.
Writes cad.json, check.png and record.json to a new directory. DWG is not read; save it as DXF first.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from functools import lru_cache
import json
import math
from pathlib import Path
import re

from _sandbox import configure_writable_caches

configure_writable_caches()

from _budget import check_vector
from _delivery import bundle, digest, write_json

VERSION = '0.1.0'
VERSIONS = {'AC1009': 'R12', 'AC1012': 'R13', 'AC1014': 'R14', 'AC1015': 'R2000', 'AC1018': 'R2004',
            'AC1021': 'R2007', 'AC1024': 'R2010', 'AC1027': 'R2013', 'AC1032': 'R2018'}
# $INSUNITS code -> (name, metres per drawing unit); codes not listed are reported without a scale.
UNITS = {0: ('unitless', None), 1: ('inches', 0.0254), 2: ('feet', 0.3048), 4: ('millimeters', 0.001),
         5: ('centimeters', 0.01), 6: ('meters', 1.0), 7: ('kilometers', 1000.0), 10: ('yards', 0.9144),
         14: ('decimeters', 0.1)}
DATUMS = ('CGCS2000', 'New Beijing', 'Xian 1980', 'Beijing 1954')
TEXT_SAMPLES = 5
PLOT_LAYERS = 12
OUTLIER_SPREAD = 20
# What the GDAL fallback cannot report; listed in cad.json when ezdxf is not installed.
EZDXF_ONLY = ['DXF entity types for R12', 'closed and open polylines', 'block names and attribute tags',
              'raw and cleaned text', 'layer off and frozen state', 'unused layers', 'layouts and viewports']


def read_header(path):
    """Header variables of an ASCII DXF; GDAL does not expose them."""
    with open(path, 'rb') as stream:
        if stream.read(18) == b'AutoCAD Binary DXF':
            return {'readable': False, 'reason': 'binary DXF header is not parsed'}
        stream.seek(0)
        values, name, in_header, code = {}, None, False, None
        for number, raw in enumerate(stream):
            text = raw.decode('latin-1').strip()
            if number % 2 == 0:
                code = text
                continue
            if code == '0' and text == 'ENDSEC' and in_header:
                break
            if code == '2' and text == 'HEADER':
                in_header = True
            elif code == '0' and text == 'SECTION':
                continue
            elif code == '2' and not in_header:
                break  # the first section is not HEADER: an entities-only R12 file
            elif in_header and code == '9':
                name = text
            elif in_header and name:
                values.setdefault(name, {})[code] = text
    version = values.get('$ACADVER', {}).get('1')
    units = values.get('$INSUNITS', {}).get('70')
    units = int(units) if units is not None and units.lstrip('-').isdigit() else None
    extent = [values.get(key, {}).get(axis) for key in ('$EXTMIN', '$EXTMAX') for axis in ('10', '20')]
    try:
        extent = [float(v) for v in extent]
    except (TypeError, ValueError):
        extent = None
    return {'readable': bool(values), 'acadver': version, 'release': VERSIONS.get(version),
            'insunits': units, 'units': UNITS.get(units, ('unknown', None))[0] if units is not None else None,
            'codepage': values.get('$DWGCODEPAGE', {}).get('3'), 'extent': extent}


@lru_cache(maxsize=None)
def gauss_kruger():
    """EPSG zone-prefixed Gauss-Kruger grids keyed by (width, zone), datums in DATUMS order."""
    from pyproj.database import query_crs_info
    pattern = re.compile(r'(%s) / (3-degree )?Gauss-Kruger zone (\d+)' % '|'.join(DATUMS))
    grids = {}
    for info in query_crs_info(auth_name='EPSG', pj_types='PROJECTED_CRS'):
        match = pattern.fullmatch(info.name)
        if match and not info.deprecated:
            key = (3 if match[2] else 6, int(match[3]))
            grids.setdefault(key, []).append((match[1], f'EPSG:{info.code}', info.name, info.area_of_use))
    return {key: sorted(rows, key=lambda row: DATUMS.index(row[0])) for key, rows in grids.items()}


def distance_km(a, b):
    lon1, lat1, lon2, lat2 = map(math.radians, (*a, *b))
    h = math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2
    return 2*6371.0088*math.asin(math.sqrt(h))


def locate(code, x, y):
    from pyproj import Transformer
    lon, lat = Transformer.from_crs(code, 'EPSG:4326', always_xy=True).transform(x, y)
    return (lon, lat) if math.isfinite(lon) and math.isfinite(lat) else None


def check(code, name, datum, x, y, area, near, near_km):
    """A candidate row when the point lands in the CRS's area of use (0.5 deg slack) and near the hint."""
    place = locate(code, x, y)
    if place is None:
        return None, 'does not project'
    lon, lat = place
    if area is not None and not (area.west-0.5 <= lon <= area.east+0.5 and area.south-0.5 <= lat <= area.north+0.5):
        return None, 'outside the area of use'
    row = {'crs': code, 'name': name, 'datum': datum, 'lonlat': [round(lon, 5), round(lat, 5)]}
    if near is not None:
        row['distance_km'] = round(distance_km(near, place), 1)
        if row['distance_km'] > near_km:
            return None, f'{row["distance_km"]} km from --near'
    return row, None


def readings(x, y, scale, near, near_km):
    """Grid kinds the representative point fits at one drawing-unit scale, with candidate CRS."""
    from pyproj import CRS
    e, n = x*scale, y*scale
    found, rejected = [], []

    def consider(grid, rows):
        kept = []
        for datum, code, name, area in rows:
            row, reason = check(code, name, datum, e, n, area, near, near_km)
            if row:
                kept.append(row)
            else:
                rejected.append({'crs': code, 'reason': reason})
        if kept:
            found.append({'grid': grid, 'candidates': kept})

    if scale == 1 and -180 <= x <= 180 and -90 <= y <= 90:
        consider('geographic', [(CRS(code).name, code, CRS(code).name, CRS(code).area_of_use)
                                for code in ('EPSG:4490', 'EPSG:4326')])
    zone = e // 1e6
    if 0 < n < 6.5e6 and (13 <= zone <= 23 or 25 <= zone <= 45):
        width = 6 if zone <= 23 else 3
        consider(f'gauss-kruger {width}-degree zone {int(zone)} (zone prefix)', gauss_kruger().get((width, int(zone)), []))
    # Without the zone prefix a Gauss-Kruger grid, a UTM zone and a city or project local grid (Shenzhen's,
    # for one) look alike, so no EPSG code is listed for them, --near or not.
    bare = 0 < e < 1e6 and 0 < n < 6.5e6
    if not found and not bare and near is not None and abs(e) <= 20037508.35 and abs(n) <= 20037508.35:
        consider('web mercator', [('WGS 84', 'EPSG:3857', 'WGS 84 / Pseudo-Mercator', None)])
    return found, rejected, bare


def looks_misdecoded(values):
    """Names that are GBK bytes read as Latin-1, as when $DWGCODEPAGE is missing or says ANSI_1252."""
    for value in values:
        if not isinstance(value, str) or value.isascii():
            continue
        try:
            raw = value.encode('latin-1')
            if raw.decode('gbk') != value:
                return True
        except UnicodeError:
            continue
    return False


def entity_type(subclasses):
    if isinstance(subclasses, str) and subclasses:
        return subclasses.split(':')[-1]
    return None


def inventory(frame):
    """Per-layer counts, entity and geometry types, text samples and bounds of model-space entities."""
    import numpy as np
    types = frame['SubClasses'].map(entity_type) if 'SubClasses' in frame else None
    text = frame['Text'] if 'Text' in frame else None
    is_text = np.zeros(len(frame), bool)
    if text is not None:
        has_text = text.notna() & (text.astype(str).str.strip() != '')
        if types is not None and types.notna().any():
            is_text = (has_text & types.fillna('').str.contains('Text')).to_numpy()
        else:  # R12 has no subclass markers: TEXT entities come through as points with text
            is_text = (has_text & (frame.geom_type == 'Point')).to_numpy()
    layers = []
    for name, rows in frame.groupby(frame['Layer'].fillna(''), sort=False):
        index = rows.index
        row = {'layer': name, 'entities': len(rows),
               'geometry_types': {k: int(v) for k, v in rows.geom_type.value_counts().items()}}
        if types is not None and types.loc[index].notna().any():
            row['entity_types'] = {k: int(v) for k, v in types.loc[index].value_counts().items()}
        mask = is_text[frame.index.get_indexer(index)]
        if mask.any():
            values = text.loc[index][mask].astype(str).str.strip()
            row['texts'] = int(mask.sum())
            row['text_samples'] = [{'raw': v[:80]} for v in dict.fromkeys(values)][:TEXT_SAMPLES]
        row['bounds'] = [round(float(v), 3) for v in rows.total_bounds]
        layers.append(row)
    return sorted(layers, key=lambda row: -row['entities'])


def clean_text(text):
    """Text with full-width ASCII folded to half-width and whitespace (MTEXT \\P included) collapsed."""
    folded = ''.join(chr(ord(c) - 0xFEE0) if 0xFF01 <= ord(c) <= 0xFF5E else ' ' if c == '　' else c for c in text)
    return ' '.join(folded.split())


def read_drawing(path, encoding):
    """(ezdxf document, audit counts); (None, None) when ezdxf is not installed."""
    try:
        import ezdxf
        from ezdxf import recover
    except ImportError:
        return None, None
    if encoding:  # recover mode takes the encoding from the header only
        return ezdxf.readfile(path, encoding=encoding), None
    doc, auditor = recover.readfile(path)
    return doc, {'errors': len(auditor.errors), 'fixes': len(auditor.fixes)}


def drawing_inventory(doc, bounds):
    """Per-layer DXF entity types counted as CAD counts them (one INSERT per block reference), block names,
    attribute tags, raw and cleaned text, and layer state; bounds come from the expanded GDAL geometry."""
    from collections import Counter
    layers = {}
    for entity in doc.modelspace():
        kind = entity.dxftype()
        if kind == 'LWPOLYLINE':
            kind += ' closed' if entity.closed else ' open'
        elif kind == 'POLYLINE':
            kind += ' closed' if entity.is_closed else ' open'
        row = layers.setdefault(entity.dxf.layer, {'layer': entity.dxf.layer, 'entities': 0, 'dxf_types': Counter(),
                                                    'blocks': Counter(), 'attributes': {}, 'text': []})
        row['entities'] += 1
        row['dxf_types'][kind] += 1
        if kind == 'INSERT':
            row['blocks'][entity.dxf.name] += 1
            for attrib in entity.attribs:
                samples = row['attributes'].setdefault(attrib.dxf.tag, [])
                value = clean_text(attrib.dxf.text)
                if value not in samples and len(samples) < TEXT_SAMPLES:
                    samples.append(value)
        elif kind == 'TEXT':
            row['text'].append((entity.dxf.text, clean_text(entity.plain_text())))
        elif kind == 'MTEXT':
            row['text'].append((entity.text, clean_text(entity.plain_text(split=False))))
    table = {layer.dxf.name: layer for layer in doc.layers}
    result = []
    for name, row in layers.items():
        out = {'layer': name, 'entities': row['entities'], 'dxf_types': dict(row['dxf_types'].most_common())}
        if row['blocks']:
            out['blocks'] = dict(row['blocks'].most_common())
        if row['attributes']:
            out['attributes'] = row['attributes']
        if row['text']:
            out['texts'] = len(row['text'])
            out['text_samples'] = [{'raw': raw[:80], 'clean': clean[:80]}
                                   for raw, clean in dict(row['text']).items()][:TEXT_SAMPLES]
        layer = table.get(name)
        state = [flag for flag, on in (('off', layer and layer.is_off()), ('frozen', layer and layer.is_frozen())) if on]
        if state:
            out['state'] = state
        out['bounds'] = bounds.get(name)
        result.append(out)
    unused = sorted(set(table) - set(layers) - {'0', 'Defpoints'})
    layouts = [{'name': layout.name, 'entities': len(layout),
                'viewports': sum(1 for v in layout.query('VIEWPORT') if v.dxf.get('id', 0) != 1)}
               for layout in doc.layouts if layout.name != 'Model']
    return sorted(result, key=lambda row: -row['entities']), unused, layouts


def split_core(frame):
    """Entities more than OUTLIER_SPREAD median distances from the median centre (title blocks at the origin,
    strays) are outliers; returns the core mask and the core's median centre."""
    import numpy as np
    bounds = frame.geometry.bounds
    cx, cy = ((bounds.minx+bounds.maxx)/2).to_numpy(), ((bounds.miny+bounds.maxy)/2).to_numpy()
    distance = np.hypot(cx-np.median(cx), cy-np.median(cy))
    core = distance <= OUTLIER_SPREAD*max(float(np.median(distance)), 1e-9)
    return core, float(np.median(cx[core])), float(np.median(cy[core]))


def plot(frame, core, layers, path, title, font):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    plt.rcParams['font.sans-serif'] = [font, 'DejaVu Sans']
    plt.rcParams['axes.unicode_minus'] = False
    outliers = not core.all()
    figure, axes = plt.subplots(1, 2 if outliers else 1, figsize=(15 if outliers else 9, 8), squeeze=False)
    axes = axes[0]
    shown = [row['layer'] for row in layers[:PLOT_LAYERS]]
    colours = matplotlib.colormaps['tab20']
    names = frame['Layer'].fillna('')
    panels = [(axes[-1], frame[core], 'core extent')] + ([(axes[0], frame, 'full extent')] if outliers else [])
    for axis, part, label in panels:
        other = part[~names.loc[part.index].isin(shown)]
        if len(other):
            other.plot(ax=axis, color='#b0b0b0', linewidth=0.5, markersize=3)
        for i, layer in enumerate(shown):
            rows = part[names.loc[part.index] == layer]
            if len(rows):
                rows.plot(ax=axis, color=colours(i % 20), linewidth=0.7, markersize=4)
        axis.set_title(label)
        axis.set_xlabel('x (drawing units)')
        axis.set_ylabel('y (drawing units)')
        axis.set_aspect('equal', adjustable='datalim')
        axis.ticklabel_format(useOffset=False, style='plain')
        axis.tick_params(labelsize=7)
    if outliers:
        minx, miny, maxx, maxy = frame[core].total_bounds
        axes[0].add_patch(Rectangle((minx, miny), maxx-minx, maxy-miny, fill=False, linestyle='--', edgecolor='black'))
        stray = frame[~core].geometry.centroid
        axes[0].scatter(stray.x, stray.y, marker='x', color='red', s=40, label='outlier', zorder=5)
    handles = [plt.Line2D([], [], color=colours(i % 20), label=layer or '(no layer)') for i, layer in enumerate(shown)]
    if len(layers) > PLOT_LAYERS:
        handles.append(plt.Line2D([], [], color='#b0b0b0', label=f'{len(layers)-PLOT_LAYERS} other layers'))
    figure.legend(handles=handles, loc='center right', fontsize=8, frameon=False)
    figure.suptitle(title, fontsize=11)
    figure.tight_layout(rect=(0, 0, 0.85, 0.95))
    figure.savefig(path, dpi=150)
    plt.close(figure)


def parse_near(text):
    try:
        lon, lat = (float(part) for part in text.split(','))
    except ValueError as error:
        raise ValueError('--near must be LON,LAT in degrees') from error
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise ValueError('--near must be LON,LAT in degrees')
    return lon, lat


def inspect(args):
    import pyogrio
    source = Path(args.source)
    if source.suffix.lower() == '.dwg':
        raise ValueError('DWG is not read; save it as DXF first (AutoCAD / ZWCAD "Save As", or ODA File Converter)')
    if source.suffix.lower() != '.dxf' or not source.is_file():
        raise ValueError('Source must be an existing .dxf file')
    near = parse_near(args.near) if args.near else None
    header = read_header(source)
    check_vector(source)
    if args.encoding:
        pyogrio.set_gdal_config_options({'DXF_ENCODING': args.encoding})
    frame = pyogrio.read_dataframe(source)
    warnings = []
    names = [*frame['Layer'].dropna().unique()[:200], *(frame['Text'].dropna().unique()[:200] if 'Text' in frame else [])]
    if not args.encoding and looks_misdecoded(names):
        warnings.append(f'Layer or text names look like GBK read as Latin-1 ($DWGCODEPAGE = {header.get("codepage")}); '
                        'rerun with --encoding CP936')
    empty = frame.geometry.isna() | frame.geometry.is_empty
    paper = frame['PaperSpace'].eq(True) if 'PaperSpace' in frame else empty & False
    if paper.any():
        warnings.append(f'{int(paper.sum())} paper-space entities left out of extent and CRS checks')
    model = frame[~empty & ~paper]
    if model.empty:
        raise ValueError('No model-space geometry in the drawing')
    core, x, y = split_core(model)
    try:
        doc, audit = read_drawing(source, args.encoding)
    except Exception as error:  # ezdxf's own errors vary by damage; GDAL already read the drawing
        doc, audit = None, None
        warnings.append(f'ezdxf could not read the drawing ({type(error).__name__}: {error}); layer inventory falls back to GDAL')
    if doc is not None:
        import ezdxf
        bounds = {name: [round(float(v), 3) for v in rows.total_bounds] for name, rows in model.groupby(model['Layer'].fillna(''))}
        layers, unused, layouts = drawing_inventory(doc, bounds)
        reader = {'inventory': f'ezdxf {ezdxf.__version__}', 'missing': []}
        if audit and audit['errors']:
            warnings.append(f'ezdxf recovered the drawing with {audit["errors"]} structure errors and {audit["fixes"]} fixes')
    else:
        layers, unused, layouts = inventory(model), None, None
        reader = {'inventory': 'gdal', 'missing': EZDXF_ONLY}
    reader['geometry'] = f'gdal {pyogrio.__gdal_version_string__}'
    stray = model[~core]
    if len(stray):
        counts = stray['Layer'].fillna('').value_counts()
        warnings.append(f'{len(stray)} entities lie far outside the main drawing (layers: '
                        + ', '.join(f'{k or "(no layer)"} {v}' for k, v in counts.head(5).items()) + ')')

    scales = [(1.0, 'coordinates as drawn')]
    declared = UNITS.get(header.get('insunits'), (None, None))[1]
    if declared and declared != 1.0:
        scales.append((declared, f'header $INSUNITS = {header["units"]}'))
    tried, rejected, unprefixed = [], [], False
    for scale, basis in scales:
        found, dropped, bare = readings(x, y, scale, near, args.near_km)
        tried.append({'scale_to_metre': scale, 'basis': basis, 'matches': found})
        rejected += dropped
        unprefixed = unprefixed or bare
    candidates = [row for reading in tried for match in reading['matches'] for row in match['candidates']]
    notes = ['Coordinates cannot distinguish CGCS2000, New Beijing, Xian 1980 and Beijing 1954 grids; '
             'confirm the datum with the data provider or known control points']
    if unprefixed:
        notes.append('Coordinates look like a Gauss-Kruger grid without zone prefix, a UTM zone or a city or project '
                     'local grid, which coordinates alone cannot tell apart; no EPSG code is listed. Compare with a '
                     'layer in a known CRS or fit control points')
    if not candidates and not unprefixed:
        notes.append('No national grid fits; treat as a local or engineering coordinate system that needs control points')
    if declared and declared != 1.0 and any(r['matches'] for r in tried[:1]):
        warnings.append(f'Header says {header["units"]}, but coordinates as drawn fit a metre grid')

    crs = {'status': 'candidate' if candidates else 'hold', 'representative_point': [round(x, 3), round(y, 3)],
           'near': list(near) if near else None, 'near_km': args.near_km if near else None,
           'readings': tried, 'rejected': rejected, 'notes': notes, 'assigned': None}
    extent = {'full': [round(float(v), 3) for v in model.total_bounds],
              'core': [round(float(v), 3) for v in model[core].total_bounds], 'outliers': int(len(stray))}
    summary = (', '.join(dict.fromkeys(c['crs'] for c in candidates[:4])) + (' …' if len(candidates) > 4 else '')
               if candidates else 'CRS on hold')
    try:
        from _theme import get_chinese_font
        font = get_chinese_font()
    except ImportError:
        font = 'DejaVu Sans'
    stat = source.stat()
    total = sum(row['entities'] for row in layers)
    with bundle(args.output) as stage:
        plot(model, core, layers, stage / 'check.png',
             f'{source.name} · {total} entities · {len(layers)} layers · {summary}', font)
        report = {'report': 'cad-inventory', 'schema_version': 1,
                  'source': {'name': source.name, 'bytes': stat.st_size, 'sha256': digest(source),
                             'modified': datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec='seconds')},
                  'header': header, 'reader': reader,
                  'entities': {'model_space': total, 'geometries': int(len(model)), 'paper_space': int(paper.sum()),
                               'empty': int(empty.sum()),
                               'geometry_types': {k: int(v) for k, v in model.geom_type.value_counts().items()}},
                  'blocks': ('each reference counts once per layer; geometry, extent and the plot expand references'
                             if doc is not None else 'expanded into their geometry; block names are not reported'),
                  'layers': layers, 'unused_layers': unused, 'layouts': layouts,
                  'extent': extent, 'crs': crs, 'warnings': warnings,
                  'files': {'plot': 'check.png', 'record': 'record.json'}}
        write_json(stage / 'cad.json', report)
        write_json(stage / 'record.json', {'schema_version': 1, 'status': 'complete', 'tool': 'cad', 'version': VERSION,
                                            'parameters': {k: v for k, v in vars(args).items() if k not in ('output', 'command')},
                                            'source': {'path': str(source.absolute()), 'sha256': report['source']['sha256']},
                                            'files': {'cad.json': digest(stage / 'cad.json'), 'check.png': digest(stage / 'check.png')},
                                            'warnings': warnings,
                                            'finished_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                                            'assumptions': ['Geometry comes from GDAL with block references expanded',
                                                            'CRS candidates come from coordinate magnitude and EPSG areas of use only',
                                                            'No CRS is assigned and no geometry is written']})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    sub = commands.add_parser('inspect', help='Inventory a DXF and list candidate CRS')
    sub.add_argument('source', help='DXF file')
    sub.add_argument('--output', required=True, help='New directory for cad.json, check.png and record.json')
    sub.add_argument('--near', help='Approximate LON,LAT of the area the drawing covers; candidates farther than --near-km are rejected')
    sub.add_argument('--near-km', type=float, default=50.0, help='Keep candidates within this distance of --near (default 50)')
    sub.add_argument('--encoding', help='Text encoding overriding $DWGCODEPAGE, e.g. CP936 for Chinese drawings without one')
    args = parser.parse_args()
    try:
        if args.near_km <= 0:
            raise ValueError('--near-km must be positive')
        report = inspect(args)
        candidates = [row['crs'] for reading in report['crs']['readings'] for match in reading['matches'] for row in match['candidates']]
        print(json.dumps({'output': str(Path(args.output).absolute()), 'layers': len(report['layers']),
                          'entities': report['entities']['model_space'], 'crs_status': report['crs']['status'],
                          'candidates': candidates, 'warnings': report['warnings']}, ensure_ascii=False))
    except (ValueError, OSError, RuntimeError) as exc:  # pyogrio's DataSourceError is a RuntimeError
        parser.exit(1, f'ERROR: {exc}\n')


if __name__ == '__main__':
    main()
