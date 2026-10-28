#!/usr/bin/env python3
"""Unpack an ArcGIS layer or map package (.lpk, .lpkx, .mpk, .mpkx) safely and say what is in it.

`package.py unpack` lists the archive (7z through bsdtar or 7-Zip, or zip) and refuses absolute paths, '..',
links and archives larger than --max-bytes before extracting anything; it then checks the extracted sizes against
the listing. It reads the package information (esriinfo), writes a describe card for every dataset under
commondata/ (shapefiles, file geodatabases, rasters), and matches each layer file to its data: .lyrx (ArcGIS Pro)
is JSON and its renderer fields, label expression and definition query are read; .lyr (ArcMap) is a binary
format, so only its data-source path and the names it mentions are picked out. Layers whose data is not in the
package (packaged by reference, or enterprise data left out) are reported. Symbology is not reproduced.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
import zipfile

from _delivery import bundle, digest, write_json

VERSION = '0.1.0'
SUFFIXES = {'.lpk', '.lpkx', '.mpk', '.mpkx'}
RASTER_SUFFIXES = {'.tif', '.tiff', '.img', '.jp2', '.sid', '.ecw', '.dem', '.asc'}
DEFAULT_MAX_BYTES = 20 * 2**30


def archive_kind(path):
    with open(path, 'rb') as stream:
        magic = stream.read(6)
    if magic == b"7z\xbc\xaf'\x1c":
        return '7z'
    if magic[:2] == b'PK':
        return 'zip'
    raise ValueError(f'{path} is neither a 7z nor a zip archive')


def unescape(raw):
    """bsdtar listings escape non-ASCII bytes as \\ooo and a backslash as \\\\."""
    out, i = bytearray(), 0
    data = raw.encode('latin-1', 'surrogateescape') if isinstance(raw, str) else raw
    while i < len(data):
        if data[i] == 0x5C and data[i+1:i+4].isdigit() and len(data[i+1:i+4]) == 3:
            out.append(int(data[i+1:i+4], 8))
            i += 4
        elif data[i] == 0x5C and data[i+1:i+2] == b'\\':
            out.append(0x5C)
            i += 2
        else:
            out.append(data[i])
            i += 1
    return out.decode('utf-8', 'replace')


def list_members(path, kind):
    """[(name, size, is_dir, is_link)] and the tool used to read the archive."""
    if kind == 'zip':
        with zipfile.ZipFile(path) as archive:
            rows = []
            for info in archive.infolist():
                mode = info.external_attr >> 16
                rows.append((info.filename, info.file_size, info.is_dir(), (mode & 0o170000) == 0o120000))
        return rows, 'zipfile'
    tool = shutil.which('bsdtar')
    if tool:
        run = subprocess.run([tool, '-tvf', str(path)], capture_output=True)
        if run.returncode != 0:
            raise ValueError(f'bsdtar cannot list {path}: {run.stderr.decode(errors="replace").strip()}')
        rows = []
        for line in run.stdout.decode('latin-1').splitlines():
            parts = line.split(None, 8)
            if len(parts) < 9:
                continue
            perms, size, name = parts[0], parts[4], parts[8]
            link = perms.startswith(('l', 'h')) or ' -> ' in name or ' link to ' in name
            rows.append((unescape(name), int(size), perms.startswith('d'), link))
        return rows, 'bsdtar'
    tool = shutil.which('7z') or shutil.which('7za') or shutil.which('7zz')
    if tool:
        run = subprocess.run([tool, 'l', '-slt', '-ba', str(path)], capture_output=True, text=True, errors='replace')
        if run.returncode != 0:
            raise ValueError(f'7-Zip cannot list {path}: {run.stderr.strip()}')
        rows, entry = [], {}
        for line in run.stdout.splitlines() + ['']:
            if not line.strip():
                if 'Path' in entry:
                    attributes = entry.get('Attributes', '')
                    rows.append((entry['Path'], int(entry.get('Size') or 0), attributes.startswith('D') or 'D' in attributes[:1],
                                 'l' in attributes.split(' ')[-1][:1] if ' ' in attributes else False))
                entry = {}
                continue
            key, _, value = line.partition(' = ')
            entry[key.strip()] = value
        return rows, Path(tool).name
    raise ValueError('Reading a 7z package needs bsdtar (libarchive) or 7-Zip; neither was found')


def check_members(rows, max_bytes):
    """Refuse members that would land outside the target folder, links, and oversized archives."""
    bad = []
    for name, _, _, link in rows:
        posix = PurePosixPath(name.replace('\\', '/'))
        if posix.is_absolute() or re.match(r'^[A-Za-z]:', name) or '..' in posix.parts:
            bad.append(f'{name} (outside the package folder)')
        elif link:
            bad.append(f'{name} (link)')
    if bad:
        raise ValueError('Refusing to unpack: ' + '; '.join(bad[:5]) + (' …' if len(bad) > 5 else ''))
    total = sum(size for _, size, is_dir, _ in rows if not is_dir)
    if total > max_bytes:
        raise ValueError(f'Package expands to {total/2**30:.2f} GiB, above --max-bytes {max_bytes/2**30:.2f} GiB')
    return total


def extract(path, kind, tool, target):
    target.mkdir()
    if kind == 'zip':
        with zipfile.ZipFile(path) as archive:
            archive.extractall(target)
        return
    if tool == 'bsdtar':
        command = [shutil.which('bsdtar'), '-xf', str(path), '-C', str(target), '--no-same-owner']
    else:
        command = [shutil.which(tool), 'x', '-y', f'-o{target}', str(path)]
    run = subprocess.run(command, capture_output=True, text=True, errors='replace')
    if run.returncode != 0:
        raise ValueError(f'{tool} could not unpack {path}: {run.stderr.strip()[-300:]}')


def check_extracted(target, expected):
    total = 0
    for item in target.rglob('*'):
        if item.is_symlink():
            raise ValueError(f'Unpacked a link: {item.relative_to(target)}')
        if not item.resolve().is_relative_to(target.resolve()):
            raise ValueError(f'Unpacked outside the package folder: {item}')
        if item.is_file():
            total += item.stat().st_size
    if total != expected:
        raise ValueError(f'Unpacked {total} bytes, the listing says {expected}')


def xml_fields(path, names):
    if not path.is_file():
        return None
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return {'unreadable': True}
    values = {name: (root.findtext(name) or '').strip() or None for name in names}
    extent = root.find('extent')
    if extent is not None:
        values['extent'] = [float(extent.findtext(k) or 'nan') for k in ('xmin', 'ymin', 'xmax', 'ymax')]
    keywords = [k.text for k in root.iter('typekeyword') if k.text]
    if keywords:
        values['typekeywords'] = keywords
    return values


def datasets(root):
    """Data under commondata/: shapefiles, file geodatabases and rasters, with describe cards."""
    from describe import describe
    data = root / 'commondata'
    found = []
    if not data.is_dir():
        return found
    for item in sorted(data.rglob('*')):
        kind = ('filegdb' if item.is_dir() and item.suffix.lower() == '.gdb'
                else 'shapefile' if item.is_file() and item.suffix.lower() == '.shp'
                else 'raster' if item.is_file() and item.suffix.lower() in RASTER_SUFFIXES else None)
        if kind is None or any(parent.suffix.lower() == '.gdb' for parent in item.relative_to(data).parents):
            continue
        row = {'path': str(item.relative_to(root)), 'kind': kind, 'name': item.stem}
        try:
            card = describe(item)
            card['path'] = row['path']
            row['card'] = card
        except Exception as error:  # an unreadable dataset is reported, not fatal
            row['error'] = f'{type(error).__name__}: {error}'
        found.append(row)
    return found


def utf16_strings(data, minimum=3):
    """Readable UTF-16LE runs in a binary file, at both byte alignments."""
    allowed = re.compile(r"[\w\\/.:\[\]()=<>'\"%,+\- 　-〿一-鿿＀-￯]" + f'{{{minimum},}}')
    found = []
    for offset in (0, 1):
        text = data[offset:len(data) - (len(data) - offset) % 2].decode('utf-16le', 'replace')
        found += allowed.findall(text)
    return list(dict.fromkeys(s.strip() for s in found if s.strip()))


def lyr_summary(path):
    """What can be read from an ArcMap .lyr without the ArcObjects format: data path, names, label expression."""
    strings = utf16_strings(path.read_bytes())
    sources = [s for s in strings if 'commondata' in s.lower() or re.search(r'\.(gdb|shp|sde)\b', s, re.I)]
    labels = [s for s in strings if re.fullmatch(r'\[[^\]]+\]', s)]
    return {'format': 'lyr (ArcMap binary)', 'data_sources': sources, 'label_expressions': labels, 'strings': strings[:200],
            'symbology': 'not read: binary .lyr'}


def lyrx_summary(path):
    """Layers of an ArcGIS Pro .lyrx (CIM JSON): data connection, renderer fields and classes, labels, query."""
    document = json.loads(path.read_text(encoding='utf-8-sig'))
    layers = []
    for definition in document.get('layerDefinitions', []):
        table = definition.get('featureTable') or {}
        connection = table.get('dataConnection') or definition.get('dataConnection') or {}
        renderer = definition.get('renderer') or {}
        fields = renderer.get('fields') or ([renderer['field']] if renderer.get('field') else [])
        classes = []
        for group in renderer.get('groups', []):
            for item in group.get('classes', []):
                values = [v for value in item.get('values', []) for v in value.get('fieldValues', [])]
                classes.append({'label': item.get('label'), 'values': values})
        for item in renderer.get('breaks', []):
            classes.append({'label': item.get('label'), 'upper_bound': item.get('upperBound')})
        layers.append({'name': definition.get('name'), 'type': definition.get('type'),
                       'workspace': connection.get('workspaceConnectionString'), 'dataset': connection.get('dataset'),
                       'definition_query': table.get('definitionExpression'),
                       'renderer': renderer.get('type'), 'renderer_fields': fields, 'classes': classes,
                       'label_expressions': [c.get('expression') for c in definition.get('labelClasses', []) if c.get('expression')]})
    return {'format': 'lyrx (ArcGIS Pro CIM JSON)', 'layers': layers,
            'symbology': 'classes and labels read; colours and symbols not reproduced'}


def layer_files(root):
    """Layer files by format; ArcMap packages hold one folder per ArcGIS version (v10, v105, ...), newest kept."""
    files = []
    lyr_folders = sorted({p.parent for p in root.rglob('*.lyr')}, key=lambda p: [int(n) for n in re.findall(r'\d+', p.name)] or [0])
    if lyr_folders:
        newest = lyr_folders[-1]
        files += [(p, lyr_summary(p)) for p in sorted(newest.glob('*.lyr'))]
    for path in sorted(root.rglob('*.lyrx')):
        try:
            files.append((path, lyrx_summary(path)))
        except (ValueError, UnicodeDecodeError) as error:
            files.append((path, {'format': 'lyrx', 'error': str(error)}))
    for path in sorted(root.rglob('*.mxd')) + sorted(root.rglob('*.mapx')):
        files.append((path, {'format': path.suffix.lower().lstrip('.'), 'note': 'map document; layers not read'}))
    return files, [str(p.relative_to(root)) for p in lyr_folders]


def match(summary, data, layer_name):
    """Datasets a layer file points at, by name and data path; empty when its data is not in the package."""
    hits = []
    if summary['format'].startswith('lyrx'):
        for layer in summary['layers']:
            workspace = (layer.get('workspace') or '').replace('\\', '/').lower()
            dataset = layer.get('dataset')
            for row in data:
                inside = row['path'].replace('\\', '/').lower()
                if not dataset:
                    continue
                if row['kind'] == 'filegdb':  # a geodatabase is named by its workspace; the dataset is a layer inside
                    if workspace.endswith(PurePosixPath(inside).name):
                        hits.append(f"{row['path']}:{dataset}")
                elif row['name'] == dataset:
                    hits.append(row['path'])
        return sorted(set(hits))
    strings = set(summary.get('strings', []))
    folders = {s.replace('\\', '/').split('commondata/')[-1].strip('/') for s in summary.get('data_sources', []) if 'commondata' in s.lower()}
    for row in data:
        relative = row['path'].split('commondata/')[-1]
        folder = str(PurePosixPath(relative).parent)
        if (row['name'] in strings or row['name'] == layer_name) and (not folders or folder in folders or relative in folders):
            hits.append(row['path'])
    return sorted(set(hits))


def unpack(args):
    source = Path(args.source)
    if source.suffix.lower() not in SUFFIXES or not source.is_file():
        raise ValueError(f'Source must be an existing {", ".join(sorted(SUFFIXES))} file')
    kind = archive_kind(source)
    rows, tool = list_members(source, kind)
    total = check_members(rows, args.max_bytes)
    stat = source.stat()
    with bundle(args.output) as stage:
        root = stage / 'package'
        extract(source, kind, tool, root)
        check_extracted(root, total)
        info = xml_fields(root / 'esriinfo' / 'item.pkinfo', ('name', 'version', 'type', 'ID'))
        item = xml_fields(root / 'esriinfo' / 'iteminfo.xml', ('name', 'title', 'summary', 'description', 'tags', 'type'))
        data = datasets(root)
        files, versions = layer_files(root)
        layers, used = [], set()
        for path, summary in files:
            name = re.sub(r'^\d{4}', '', path.stem)
            linked = match(summary, data, name) if 'error' not in summary and summary['format'][:3] in ('lyr',) else []
            used.update(p.split(':')[0] for p in linked)
            layers.append({'file': str(path.relative_to(root)), 'name': name, **summary, 'data': linked,
                           'data_in_package': bool(linked) if summary['format'][:3] == 'lyr' else None})
            layers[-1].pop('strings', None)
        missing = [layer['name'] for layer in layers if layer['data_in_package'] is False]
        warnings = []
        if not data:
            warnings.append('No data under commondata/: the package holds layer definitions only (data packaged by reference)')
        if missing:
            warnings.append(f'{len(missing)} layers point at data that is not in the package: {", ".join(missing[:5])}')
        unused = [row['path'] for row in data if row['path'] not in used]
        if unused and layers:
            warnings.append(f'{len(unused)} datasets are not named by any layer file: {", ".join(unused[:5])}')
        for row in data:
            for warning in (row.get('card') or {}).get('warnings', []):
                warnings.append(f'{row["path"]}: {warning["message"]}')
            if 'error' in row:
                warnings.append(f'{row["path"]}: unreadable ({row["error"]})')
        thumbnail = next(iter(sorted((root / 'esriinfo').rglob('thumbnail.*'))), None) if (root / 'esriinfo').is_dir() else None
        manifest = {'manifest': 'esri-package', 'schema_version': 1,
                    'source': {'name': source.name, 'bytes': stat.st_size, 'sha256': digest(source), 'archive': kind,
                               'reader': tool, 'unpacked_bytes': total, 'members': len(rows)},
                    'package': info, 'item': item, 'layer_versions': versions, 'datasets': data, 'layers': layers,
                    'thumbnail': str(thumbnail.relative_to(stage)) if thumbnail else None,
                    'warnings': warnings, 'unpacked_to': 'package/'}
        write_json(stage / 'package.json', manifest)
        write_json(stage / 'record.json', {'schema_version': 1, 'status': 'complete', 'tool': 'package unpack', 'version': VERSION,
                                            'parameters': {k: v for k, v in vars(args).items() if k not in ('output', 'command')},
                                            'source': {'path': str(source.absolute()), 'sha256': manifest['source']['sha256']},
                                            'files': {'package.json': digest(stage / 'package.json')},
                                            'finished_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                                            'assumptions': ['Archive members are checked before extraction; nothing outside package/ is written',
                                                            'Layer-to-data links in .lyr files are read from embedded strings, not the ArcObjects format',
                                                            'Symbology is not reproduced']})
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    sub = commands.add_parser('unpack', help='Unpack a .lpk / .lpkx / .mpk / .mpkx and inventory it')
    sub.add_argument('source', help='ArcGIS package file')
    sub.add_argument('--output', required=True, help='New directory for package/, package.json and record.json')
    sub.add_argument('--max-bytes', type=int, default=DEFAULT_MAX_BYTES, help='Refuse packages that expand beyond this (default 20 GiB)')
    args = parser.parse_args()
    try:
        manifest = unpack(args)
        print(json.dumps({'output': str(Path(args.output).absolute()), 'package': (manifest['package'] or {}).get('name'),
                          'datasets': [d['path'] for d in manifest['datasets']],
                          'layers': [{'name': l['name'], 'data': l['data']} for l in manifest['layers']],
                          'warnings': manifest['warnings']}, ensure_ascii=False))
    except (ValueError, OSError) as exc:
        parser.exit(1, f'ERROR: {exc}\n')


if __name__ == '__main__':
    main()
