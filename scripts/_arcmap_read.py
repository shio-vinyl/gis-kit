"""ArcMap .lyr -> map-style, for the FeatureLayer and GroupLayer layouts in the ArcObjects skeletons."""
from __future__ import annotations

import math
from pathlib import PureWindowsPath
import re
import struct
from uuid import UUID

import _arcmap_style as arc
from _arcgis_read import GROUPS, label_source
from _style_read import PX_PER_PT, Reading, hex_color, line, paint, parse_filter, tidy


class Cursor:
    def __init__(self, data, pos=0):
        self.data, self.pos = data, pos

    def take(self, size):
        end = self.pos + size
        if self.pos < 0 or end > len(self.data):
            raise ValueError('truncated ArcMap object')
        result = self.data[self.pos:end]
        self.pos = end
        return result

    def number(self, fmt):
        return struct.unpack('<' + fmt, self.take(struct.calcsize('<' + fmt)))[0]

    def text(self):
        raw = self.take(self.number('I'))
        if len(raw) < 2 or len(raw) % 2 or raw[-2:] != b'\0\0':
            raise ValueError('invalid UTF-16 string slot')
        value = raw[:-2].decode('utf-16le')
        if '\0' in value:
            raise ValueError('embedded terminator in string slot')
        return value

    def expect(self, data):
        if self.take(len(data)) != data:
            raise ValueError('unsupported ArcMap object layout')

    def object(self, guid):
        self.expect(guid)
        self.take(6)  # object id and version


def previous_slot(data, end):
    """Start of the UTF-16 slot ending here. Work back from its terminator to the matching byte count."""
    pos = end - len(arc.text_slot(''))
    while pos >= 0:
        if int.from_bytes(data[pos:pos + 4], 'little') == end - pos - 4:
            Cursor(data, pos).text()
            return pos
        pos -= 2
    raise ValueError('cannot locate field-table string slot')


def colour(data, pos):
    at = Cursor(data, pos)
    at.expect(arc.RGB_COLOR)
    at.take(arc.LAB_OFFSET - 16)
    lab = struct.unpack('<3d', at.take(24))
    if not all(math.isfinite(v) for v in lab):
        raise ValueError('non-finite Lab colour')
    return hex_color(*arc.lab_to_rgb(*lab))


def stroke(data, pos, reading, where):
    at = Cursor(data, pos)
    at.object(arc.LINE_SYMBOL)
    rgb = colour(data, at.pos)
    at.take(arc.AFTER_COLOR)
    width, style = at.number('d') * PX_PER_PT, at.number('I')
    if style == 5:
        return None
    if style:
        reading.note('approximated', 'line-style', f'ArcMap line style {style} is drawn as a solid line', where, 'stroke.dash')
    return line(rgb, width)


SYMBOLS = {'polygon': arc.FILL_SYMBOL, 'line': arc.LINE_SYMBOL, 'point': arc.MARKER_SYMBOL}
SHAPES = {v: k for k, v in arc.MARKER_STYLES.items()}


def symbol_parts(blob, geometry, reading, where):
    if geometry == 'line':
        outline = stroke(blob, 0, reading, where)
        return [{'kind': 'stroke', 'stroke': outline or line('#000000', 0, 0)}]
    rgb_pos = 22
    if geometry == 'polygon':
        outline = stroke(blob, 22, reading, where)
        rgb_pos = 22 + len(arc.symbol_blob('line'))
        rgb = colour(blob, rgb_pos)
        style = Cursor(blob, rgb_pos + arc.AFTER_COLOR + 8).number('I')
        if style not in (0, 1):
            reading.note('approximated', 'fill-style', f'ArcMap fill style {style} is drawn solid', where, 'fill')
        parts = [{'kind': 'fill', 'color': rgb}] if style != 1 else []
        if outline:
            parts.append({'kind': 'stroke', 'stroke': outline})
        return parts or [{'kind': 'fill', 'color': '#000000', 'opacity': 0}]

    rgb = colour(blob, rgb_pos)
    at = Cursor(blob, rgb_pos + arc.AFTER_COLOR)
    size, style = at.number('d'), at.number('I')
    if style not in SHAPES:
        raise ValueError(f'unsupported marker shape {style}')
    marker = {'shape': SHAPES[style], 'size': tidy(size * PX_PER_PT)}
    rotation = Cursor(blob, rgb_pos + arc.MARKER_ANGLE).number('d')
    if rotation:
        marker['rotation'] = -rotation % 360
    outline = None
    if blob[rgb_pos + arc.MARKER_OUTLINE_FLAG]:
        width = Cursor(blob, rgb_pos + arc.MARKER_OUTLINE_SIZE).number('d')
        outline = line(colour(blob, rgb_pos + arc.MARKER_OUTLINE_COLOR), width * PX_PER_PT)
    return [{'kind': 'marker', 'marker': marker, 'fill': paint(rgb), 'stroke': outline}]


def renderer(at, geometry, where, reading):
    kind = at.take(16)
    if kind not in (arc.SIMPLE_RENDERER, arc.UNIQUE_RENDERER, arc.BREAKS_RENDERER):
        raise ValueError(f'unsupported renderer CLSID {UUID(bytes_le=kind)}')
    at.take(6)
    previous = {}

    def symbol():
        start = at.pos
        at.expect(SYMBOLS[geometry])
        key = at.number('I')
        if key in previous:
            return previous[key]
        at.take(len(arc.symbol_blob(geometry)) - 20)
        blob = at.data[start:at.pos]
        previous[key] = blob
        return blob

    def passes(blob, label=None):
        location = {**where, 'class': label} if label else where
        return reading.symbol(geometry, location, lambda: symbol_parts(blob, geometry, reading, location))

    def group():
        at.object(arc.LEGEND_GROUP)
        at.take(4)
        heading = at.text()
        return heading, at.number('I')

    def legend():
        at.object(arc.LEGEND_CLASS)
        blob, label, description = symbol(), at.text(), at.text()
        at.take(20)
        return blob, label, description

    def note_description(description):
        if description:
            reading.note('untranslated', 'legend-description', f'legend description {description!r} has no map-style field', where, 'renderer')

    if kind == arc.SIMPLE_RENDERER:
        return {'type': 'single', 'passes': passes(symbol())}
    if kind == arc.BREAKS_RENDERER:
        field, normalization = at.text(), at.text()
        if normalization:
            raise ValueError(f'unsupported class-break normalization {normalization!r}')
        count = at.number('I')
        bounds = [at.number('d') for _ in range(count)]
        at.take(8 * count)
        low = at.number('d')
        heading, n = group()
        if heading and heading != field:
            reading.note('untranslated', 'legend-heading', f'legend heading {heading!r} is not carried', where, 'renderer')
        if n != count or not n:
            raise ValueError('unsupported class-break legend count')
        classes = []
        for index, upper in enumerate(bounds):
            # ArcMap includes the upper edge. Map-style excludes it except in the last class.
            high = math.nextafter(upper, math.inf) if index + 1 < n else upper
            blob, label, description = legend()
            if index + 1 < n and description.startswith(arc.EXCLUSIVE_LIMIT):
                try:
                    exclusive = float(description[len(arc.EXCLUSIVE_LIMIT):])
                    if math.isfinite(exclusive) and arc.cim.below(exclusive) == upper:
                        high, description = exclusive, ''
                except ValueError:
                    pass
            note_description(description)
            if not math.isfinite(low) or not math.isfinite(high) or high < low:
                raise ValueError('invalid class-break bounds')
            classes.append({'range': [low, high], 'label': label, 'passes': passes(blob, label)})
            low = high
        return {'type': 'graduated', 'field': field, 'method': 'manual', 'classes': classes}

    if at.number('I') != 1:
        raise ValueError('unsupported multi-field unique-value renderer')
    field = at.text()
    at.take(3)
    at.text()  # field delimiter
    default = symbol()
    groups = []
    for _ in range(at.number('I')):
        heading, count = group()
        if count != 1:
            raise ValueError('unsupported multi-class unique-value legend group')
        blob, label, description = legend()
        note_description(description)
        groups.append((heading, blob, label))
        at.take(2)
    at.expect(bytes(16))
    classes, used = [], set()
    for _ in range(at.number('I')):
        value, index = at.text(), at.number('I')
        if index >= len(groups) or at.take(8) != bytes(8):
            raise ValueError('unsupported unique-value index table')
        heading, blob, label = groups[index]
        if heading and heading not in (field, value):
            reading.note('untranslated', 'legend-heading', f'legend heading {heading!r} is not carried', where, 'renderer')
        classes.append({'value': value, 'label': label, 'passes': passes(blob, label)})
        used.add(index)
    # The default has a symbol slot even when off. Its own flag follows the legend index.
    at.take(2)
    at.text()
    at.take(4)
    for _ in range(3):
        at.text()
    enabled = bool(at.number('H'))
    default_label = at.text()
    if set(range(len(groups))) - used != ({0} if enabled else set()):
        raise ValueError('unsupported unique-value legend index')
    if enabled and default_label:
        reading.note('untranslated', 'default-label', f'default legend label {default_label!r} has no map-style field', where, 'renderer.default')
    return {'type': 'categorized', 'field': field, 'classes': classes, 'default': passes(default, default_label) if enabled else None}


def label_properties(data, enabled, where, reading):
    start = data.find(arc.ANNOTATION_SET)
    if start < 0:
        if enabled:
            raise ValueError('enabled labels have no supported annotation properties')
        return None
    at = Cursor(data, start + 22)
    if at.number('I') != 1:
        raise ValueError('unsupported number of annotation classes')
    at.object(arc.LABEL_ENGINE)
    expression = at.text()
    expressions = reading.extra.setdefault('label_expressions', [])
    if expression not in expressions:
        expressions.append(expression)
    field, template = label_source(expression, 'VBScript', where, reading)
    if not field and not template:
        return None
    text = data.index(arc.TEXT_SYMBOL, at.pos)
    font = data.index(arc.STD_FONT, text)
    body = Cursor(data, font + 20)
    body.expect(b'\1')
    charset, flags, weight, size = body.number('H'), body.number('B'), body.number('H'), body.number('I')
    family = body.take(body.number('B')).decode('gbk' if charset == 0x86 else 'ascii')
    style = {'color': colour(data, text + 22), 'size': tidy(size / 10000 * PX_PER_PT), 'font': family}
    if weight != 400:
        style['weight'] = weight
    if flags:
        reading.note('untranslated', 'font-style', 'additional font styles are not carried', where, 'labels.text')
    halo_pos = data.find(arc.FILL_SYMBOL, text, font)
    if halo_pos >= 0:
        width = Cursor(data, halo_pos - 8).number('d')
        style['halo'] = {'color': colour(data, halo_pos + 22 + len(arc.symbol_blob('line'))), 'width': tidy(width * PX_PER_PT)}
    reading.note('approximated', 'label-placement', 'ArcMap label placement properties are left to the target engine', where, 'labels')
    return {**({'field': field} if field else {'template': template}), 'enabled': enabled, 'text': style}


def source_header(at):
    """FeatureClassName, WorkspaceName and the file geodatabase connection property."""
    class_id = at.take(16)
    shapefile = class_id == arc.guid_bytes('198846d0-ca42-11d1-aa7c-00c04fa33a15')
    if class_id != arc.FEATURE_CLASS_NAME and not shapefile:
        raise ValueError(f'unsupported feature class connection {UUID(bytes_le=class_id)}')
    at.take(6 if shapefile else 10)
    dataset = at.text()
    at.take(6)
    at.text()  # localized dataset category
    at.text()  # shape field
    geometry = {1: 'point', 2: 'point', 3: 'line', 4: 'polygon'}.get(at.number('I'))
    if geometry is None:
        raise ValueError('unsupported feature geometry')
    at.take(6)
    at.object(arc.WORKSPACE_NAME)
    workspace = at.text()
    at.take(6)
    at.text()  # workspace browse name
    if at.data[at.pos:at.pos + 16] == bytes(16):
        at.take(16)  # no connection properties (a relative shapefile workspace)
    else:
        at.object(arc.PROPERTY_SET)
        if at.number('I') != 1 or at.text() != 'DATABASE':
            raise ValueError('unsupported workspace connection properties')
        at.take(2)
        if at.text() != workspace:
            raise ValueError('inconsistent workspace paths')
    at.take(23)  # workspace factory and flags
    return workspace, dataset, geometry, shapefile


def feature(data, at, layer_id, visible, parents, reading, bindings):
    where = {'layer': layer_id}
    at.text()  # localized layer category
    header = at.pos
    visible = bool(at.number('H')) and visible
    at.take(4)
    workspace, dataset, geometry, shapefile = source_header(at)
    key = (PureWindowsPath(workspace), dataset.casefold())
    connection = {'layer': layer_id, 'workspace': workspace, 'dataset': dataset}
    connections = reading.extra.setdefault('data_connections', [])
    if not connections or connections[-1] != connection:
        connections.append(connection)
    if reading.data:
        source = {'path': reading.data, **({'layer': reading.data_layer} if reading.data_layer else {})}
    elif key in bindings:
        source = {'path': bindings[key][0], **({} if shapefile else {'layer': dataset})}
    else:
        if shapefile:
            filename = PureWindowsPath(dataset).name
            source = {'path': filename if filename.lower().endswith('.shp') else filename + '.shp'}
        else:
            basename = PureWindowsPath(workspace).name
            source = {'path': basename, 'layer': dataset} if basename and basename != '..' else reading.unbound_data(layer_id, 'this connection')
        reading.note('approximated', 'data-path', 'the source basename is relative to the output document; bind the dataset at that location', where, 'data')
    if parents:
        reading.extra.setdefault('group_members', {})[layer_id] = list(parents)
    try:
        drawn = renderer(at, geometry, where, reading)
        if drawn['type'] == 'categorized':
            dtype = bindings.get(key, (None, {}))[1].get(drawn['field'], '')
            for item in drawn['classes']:
                if dtype.startswith(('int', 'uint')):
                    item['value'] = int(item['value'])
                elif dtype.startswith('float'):
                    item['value'] = float(item['value'])
                elif not dtype and re.fullmatch(r'[+-]?\d+(?:\.\d+)?', item['value']):
                    reading.note('approximated', 'value-type', 'numeric-looking unique values stay text without the dataset field type', where, 'renderer.classes')
    except (ValueError, IndexError) as error:
        reading.note('untranslated', 'renderer', str(error), where, 'renderer')
        drawn = None

    extra = {'visible': visible, 'group': next((p for p in reversed(parents) if p in GROUPS), None)}
    query = ''
    fields = data.find(arc.FIELD_INFO, header)
    try:
        if fields < 0:
            raise ValueError('no field table for definition query and label visibility')
        first_name = previous_slot(data, fields)
        query_end = previous_slot(data, first_name - 8)
        query_pos = previous_slot(data, query_end)
        query = Cursor(data, query_pos).text()
        if query:
            cut = interval(query)
            if cut:
                base, field, low, high, op = cut
                terms = [{'field': field, 'op': '>=', 'value': low}, {'field': field, 'op': op, 'value': high}]
                if base:
                    terms.insert(0, parse_filter(base, 'sql'))
                extra['filter'] = {'all': terms}
            else:
                extra['filter'] = parse_filter(query, 'sql')
        shown = Cursor(data, query_pos - 38 + arc.LABELS_SHOWN).number('H')
        extra['labels'] = label_properties(data, bool(shown), where, reading)
    except (ValueError, IndexError) as error:
        reading.note('untranslated', 'layer-properties', f'could not read query or labels: {error}', where, 'labels')
    extension = data.find(arc.LAYER_EXTENSION, fields)
    if extension >= 0:
        pos = extension + arc.EXTENSION_FILTER
        if data[pos:pos + 16] == arc.TRANSPARENCY:
            percent = Cursor(data, pos + 34).number('B')
            if percent <= 100:
                extra['opacity'] = (100 - percent) / 100
            else:
                reading.note('untranslated', 'transparency', 'invalid transparency percentage', where, 'opacity')
        elif data[pos:pos + 16] != bytes(16):
            reading.note('untranslated', 'display-filter', 'unsupported display filter', where, 'opacity')
    else:
        reading.note('untranslated', 'display-filter', 'missing layer extension for transparency', where, 'opacity')
    return geometry, drawn, source, extra, query


# A precision-split group carries a legend description on each child. Only rejoin
# it when its actual filters, field, connection and layer properties still agree.
INTERVAL = re.compile(r'SIGN\((?P<field>[^\W\d]\w*|"(?:[^"]|"")*") - (?P<lo>[+\-\deE.]+)\) >= 0 AND '
                      r'SIGN\((?P=field) - (?P<hi>[+\-\deE.]+)\) (?P<op><=?) 0')


def interval(query):
    base, separator, suffix = query.rpartition(') AND (SIGN(')
    if separator and query.startswith('(') and suffix.endswith(')'):
        query, base = 'SIGN(' + suffix[:-1], base[1:]
    else:
        base = ''
    found = INTERVAL.fullmatch(query)
    if found is None:
        return None
    field = found['field']
    if field.startswith('"'):
        field = field[1:-1].replace('""', '"')
    try:
        low, high = float(found['lo']), float(found['hi'])
    except ValueError:
        return None
    if math.isfinite(low) and math.isfinite(high):
        return base, field, low, high, found['op']
    return None


def read_range_group(children, name, parents, visible, reading, bindings):
    marker = arc.text_slot(arc.RANGE_CLASS)
    if not children or not all(child.startswith(arc.FEATURE_LAYER) and marker in child for child in children):
        return False
    # Read into a separate report until the complete group has been validated.
    group = Reading(reading.source, data=reading.data, data_layer=reading.data_layer)
    classes = []
    for index, data in enumerate(reversed(children)):
        try:
            at = Cursor(data, 22)
            title = at.text()
            geometry, drawn, source, extra, query = feature(data, at, name, visible, parents, group, bindings)
        except (ValueError, IndexError):
            return False  # ordinary traversal can still recover the other children
        bounds = interval(query)
        if bounds is None or not drawn or drawn['type'] != 'single':
            return False
        base, field, low, high, op = bounds
        extra.pop('filter', None)
        properties = (geometry, source, extra, base, field)
        if index == 0:
            common = properties
        if (properties != common or len(group.extra['data_connections']) != 1 or
                op != ('<=' if index == len(children) - 1 else '<') or not low < high or
                (classes and low != classes[-1]['range'][1])):
            return False
        classes.append({'range': [low, high], 'label': title, 'passes': drawn['passes']})
    layer_id = reading.unique_id(name)
    reading.symbols.update(group.symbols)
    reading.losses.update(group.losses)
    reading.notes.extend({**note, 'layer': layer_id} for note in group.notes)
    reading.extra.setdefault('data_connections', []).append({**group.extra['data_connections'][0], 'layer': layer_id})
    if parents:
        reading.extra.setdefault('group_members', {})[layer_id] = list(parents)
    for expr in group.extra.get('label_expressions', []):
        expressions = reading.extra.setdefault('label_expressions', [])
        if expr not in expressions:
            expressions.append(expr)
    if base:
        extra['filter'] = parse_filter(base, 'sql')
    drawn = {'type': 'graduated', 'field': field, 'method': 'manual', 'classes': classes}
    reading.add_layer(layer_id, geometry, drawn, source, title=name, **extra)
    return True


def read(path, reading, bindings=None):
    """Read only the supplied stream. Package bindings come from the unpacker's local inventory."""
    stream = arc.read_layer_file(path.read_bytes())
    pending = [(stream, (), True)]
    while pending:
        data, parents, visible = pending.pop()
        at = Cursor(data)
        where = {}
        try:
            kind = at.take(16)
            at.take(6)
            name = at.text()
            if kind == arc.GROUP_LAYER:
                visible = bool(at.number('H')) and visible
                at.take(20)
                children = [at.take(at.number('Q')) for _ in range(at.number('I'))]
                reading.extra.setdefault('groups', []).append({'path': list(parents + (name,)), 'visible': visible})
                if read_range_group(children, name, parents, visible, reading, bindings or {}):
                    continue
                if name not in GROUPS:
                    reading.note('approximated', 'group-layers', f'group {name!r} is flattened in drawing order', prop='group')
                pending.extend((child, parents + (name,), visible) for child in children)
            elif kind == arc.FEATURE_LAYER:
                layer_id = reading.unique_id(name)
                where = {'layer': layer_id}
                geometry, drawn, source, extra, _ = feature(data, at, layer_id, visible, parents, reading, bindings or {})
                reading.add_layer(layer_id, geometry, drawn, source, title=name, **extra)
            else:
                raise ValueError(f'unsupported layer CLSID {UUID(bytes_le=kind)}')
        except (ValueError, IndexError) as error:
            reading.note('untranslated', 'layer-type', f'could not read layer: {error}', where)
    if reading.data and len(reading.layers) > 1:
        raise ValueError('--data names one dataset but this layer file has multiple layers')
