"""package.py pack: a map-style map and its data into one GeoPackage styled for QGIS (made-up data only)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import xml.etree.ElementTree as ET

import geopandas as gpd
import pyogrio
import pytest
import rasterio

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent / 'scripts'
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(TESTS))

import _map_style  # noqa: E402
import _qgis_style as qgis  # noqa: E402
import pack_fixture  # noqa: E402
import package  # noqa: E402

QGIS_PYTHON = os.environ.get('GIS_KIT_QGIS_PYTHON') or next(
    (p for p in ('/Applications/QGIS.app/Contents/MacOS/bin/python3',) if Path(p).is_file()), None)


def run(fixture, output, *extra, success=True, env=None):
    command = [sys.executable, str(SCRIPTS / 'package.py'), 'pack', str(fixture['style']), '--output', str(output),
               '--data-root', f'basemap={fixture["basemap"]}', *map(str, extra)]
    result = subprocess.run(command, capture_output=True, text=True,
                            env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'GIS_COMPOSER_CONFIG_DIR': str(output.parent / 'no-config'),
                                 **(env or {})})
    assert (result.returncode == 0) == success, result.stdout + result.stderr
    return json.loads((output / 'package.json').read_text()) if success else result.stderr


@pytest.fixture(scope='module')
def packs(tmp_path_factory):
    root = tmp_path_factory.mktemp('pack')
    fixture = pack_fixture.build(root / 'data')
    out = {scope: root / scope for scope in ('extent', 'clip', 'full')}
    manifests = {'extent': run(fixture, out['extent']), 'clip': run(fixture, out['clip'], '--clip'),
                 'full': run(fixture, out['full'], '--full')}
    return fixture, out, manifests


def styles(gpkg):
    with sqlite3.connect(gpkg) as db:
        rows = db.execute('SELECT f_table_name, styleName, useAsDefault, styleQML FROM layer_styles ORDER BY id').fetchall()
    db.close()
    return {name: {'table': table, 'default': bool(default), 'qml': ET.fromstring(text.split('\n', 1)[1])} for table, name, default, text in rows}


def test_default_scope_keeps_whole_features_that_touch_the_extent(packs):
    fixture, out, manifests = packs
    manifest = manifests['extent']
    gpkg = out['extent'] / 'demo-map.gpkg'
    assert manifest['manifest'] == 'qgis-package' and manifest['scope'] == 'extent'
    rows = {d['ref']: d for d in manifest['datasets']}
    assert rows['@basemap/land.geojson']['features'] == 1 and rows['@basemap/land.geojson']['source_features'] == 2
    assert rows['@basemap/land.geojson']['root'] == '@basemap'
    assert rows['inputs/analysis/roads.geojson']['features'] == 2 and rows['inputs/analysis/sites.geojson']['features'] == 2
    parcels = gpd.read_file(gpkg, layer='parcels')
    source = gpd.read_file(fixture['project'] / 'inputs' / 'analysis' / 'parcels.gpkg', layer='parcels')
    assert len(parcels) == 16 and 'clipped' not in parcels.columns
    assert parcels.crs.to_epsg() == 32632  # data keep their own CRS
    assert sorted(parcels.geometry.area.round(1)) == sorted(source.geometry.area.round(1))  # whole features, not cut
    assert gpd.read_file(gpkg, layer='land').crs.to_epsg() == 4326
    with rasterio.open(gpkg, table='height') as height:  # the extent's window of the float grid, stored in the GeoPackage
        assert (height.width, height.height) == (41, 40) and height.dtypes[0] == 'float32'
    assert (out['extent'] / 'rasters' / 'cover.tif').is_file()  # a Byte raster with nodata is not lossless as GeoPackage tiles
    assert {d['ref']: d['storage'] for d in manifest['datasets'] if d['kind'] == 'raster'} == {
        'inputs/analysis/height.tif': 'geopackage', 'inputs/analysis/cover.tif': 'geotiff'}
    with sqlite3.connect(gpkg) as db:
        assert db.execute('PRAGMA user_version').fetchone()[0] == 10200
    db.close()
    record = json.loads((out['extent'] / 'record.json').read_text())
    assert record['tool'] == 'package pack' and set(record['files']) == {'demo-map.gpkg', 'package.json', 'rasters/cover.tif'}


def test_clip_cuts_at_the_extent_and_marks_cut_features(packs):
    fixture, out, manifests = packs
    gpkg = out['clip'] / 'demo-map.gpkg'
    parcels = gpd.read_file(gpkg, layer='parcels')
    assert parcels['clipped'].sum() == 12 == next(d for d in manifests['clip']['datasets'] if d['table'] == 'parcels')['clipped_features']
    extent = package.extent_in(pack_fixture.BBOX, parcels.crs)
    assert parcels.geometry.buffer(-0.01).covered_by(extent).all()
    assert (parcels.loc[parcels['clipped'], 'geometry'].area < parcels.loc[parcels['clipped'], '面积'] - 1).all()
    assert set(parcels.geometry.geom_type) <= {'Polygon', 'MultiPolygon'}
    roads = gpd.read_file(gpkg, layer='roads')
    assert len(roads) == 2 and roads['clipped'].all()


def test_full_keeps_whole_datasets(packs):
    fixture, out, manifests = packs
    rows = {d['table']: d for d in manifests['full']['datasets'] if d['table']}
    assert rows['land']['features'] == 2 and rows['roads']['features'] == 3 and rows['sites']['features'] == 3
    assert rows['height']['size'] == [80, 60] and 'window' not in rows['height']
    assert {d['scope'] for d in manifests['full']['datasets']} == {'full'}


def test_one_table_per_dataset_and_one_style_per_layer(packs):
    out, manifest = packs[1], packs[2]['extent']
    gpkg = out['extent'] / 'demo-map.gpkg'
    tables = {row[0] for row in pyogrio.list_layers(gpkg)}
    assert {'land', 'parcels', 'roads', 'sites', 'map_labels', 'map_label_leaders'} <= tables
    parcels = next(d for d in manifest['datasets'] if d['table'] == 'parcels')
    assert parcels['layers'] == ['parcels', 'far', 'parcels-outline', 'inset.parcels']
    found = styles(gpkg)
    assert {name: (s['table'], s['default']) for name, s in found.items() if s['table'] == 'parcels'} == {
        'parcels': ('parcels', True), 'far': ('parcels', False), 'parcels-outline': ('parcels', False), 'inset.parcels': ('parcels', False)}
    defaults = [s['table'] for s in found.values() if s['default']]
    assert len(defaults) == len(set(defaults))  # exactly one default style per table


def test_renderers_in_qml(packs):
    found = styles(packs[1]['extent'] / 'demo-map.gpkg')
    categorized = found['parcels']['qml'].find('renderer-v2')
    assert categorized.get('type') == 'categorizedSymbol' and categorized.get('attr') == '类别'
    categories = [(c.get('value'), c.get('render')) for c in categorized.iter('category')]
    assert categories == [('A 类', 'true'), ('B 类', 'true'), ('C 类', 'false'), ('', 'true')]  # '' = QGIS "all other values"
    fill = categorized.find("symbols/symbol[@name='0']/layer[@class='SimpleFill']")
    assert {o.get('name'): o.get('value') for o in fill.iter('Option')}['color'] == '237,125,49,255'
    c_fill = categorized.find("symbols/symbol[@name='2']/layer[@class='SimpleFill']")
    assert {o.get('name'): o.get('value') for o in c_fill.iter('Option')}['color'] == '155,187,89,204'  # opacity 0.8
    far = found['far']['qml']
    assert far.find('layerOpacity').text == '0.7'
    parent = far.find('renderer-v2/rules/rule')  # the layer filter wraps the graduated classes
    assert parent.get('filter') == '("类别" IS NOT NULL)'
    children = parent.findall('rule')
    assert children[0].get('filter') == 'coalesce((("容积率" IS NOT NULL AND "容积率" >= 0) AND ("容积率" IS NOT NULL AND "容积率" < 1.5)), FALSE)'
    assert children[-1].get('filter').startswith('coalesce((("容积率" IS NOT NULL AND "容积率" >= 2.5) AND ("容积率" IS NOT NULL AND "容积率" <= 3))')
    outline = found['parcels-outline']['qml']
    line = {o.get('name'): o.get('value') for o in outline.find(".//layer[@class='SimpleLine']").iter('Option')}
    assert line['line_width'] == qgis.number(0.8 * 25.4 / 96) and line['use_custom_dash'] == '1'
    assert line['customdash'] == ';'.join(qgis.number(v * 25.4 / 96) for v in (4, 2)) and line['capstyle'] == 'flat'
    assert outline.get('labelsEnabled') == '0' and outline.find('labeling').get('type') == 'simple'  # enabled: false
    roads = found['roads']['qml']
    rules = roads.findall('renderer-v2/rules/rule')
    assert [r.get('label') for r in rules] == ['主干', '次干', '长路']  # the rule in an unknown dialect is skipped, not a catch-all
    assert rules[1].get('filter') == ('coalesce(("等级" IN (\'主干\', \'次干\')), FALSE) AND NOT coalesce(("等级" IS NOT NULL AND "等级" = \'主干\'), FALSE)')
    assert rules[2].get('filter').startswith('coalesce(length($geometry) > 100, FALSE) AND NOT')  # dialect qgis used as written
    assert roads.get('labelsEnabled') == '1'
    assert roads.find('labeling/settings/text-style').get('fieldName') == 'concat("name", \'（\', "等级", \'）\')'
    sites = found['sites']['qml']
    marker = sites.find(".//layer[@class='SimpleMarker']")
    size = next(o for o in marker.iter('Option') if o.get('name') == 'expression').get('value')
    assert size == '2 * 0.26458333333333334 * (3 + 7 * max(0, min(1, (sqrt("规模") - 0) / 4)))'
    assert sites.find('renderer-v2').get('enableorderby') == '1' and sites.find('renderer-v2/orderby/orderByClause').get('asc') == '0'
    assert found['names']['qml'].find('renderer-v2').get('type') == 'nullSymbol'
    names = found['names']['qml'].find('labeling/settings/text-style')  # the theme alias resolved through fonts[].names
    assert (names.get('fontFamily'), names.get('namedStyle')) == ('Arial', 'Bold')
    marks = {o.get('name'): o.get('value') for o in found['site-marks']['qml'].find(".//layer[@class='SimpleMarker']/Option")}
    assert (marks['name'], marks['angle'], marks['color'], marks['outline_color']) == ('cross2', '10', '0,0,0,0', '0,0,0,255')
    assert found['ground']['qml'].find('renderer-v2').get('type') == 'singleSymbol'


def test_label_placements_become_a_point_layer_with_leaders(packs):
    out, manifest = packs[1], packs[2]['extent']
    gpkg = out['extent'] / 'demo-map.gpkg'
    labels = gpd.read_file(gpkg, layer='map_labels')
    assert labels.crs.to_epsg() == 4326 and list(labels['text']) == ['Site 1', 'Site 2\nsecond line', 'Inset']
    assert list(labels['quadrant']) == [5, 0, 4]  # anchor left -> text to the right; bottom-right -> above left; center
    assert list(labels['panel']) == ['main', 'main', 'inset'] and list(labels['style_id']) == [1, 2, 3]
    assert labels['size_mm'][0] == pytest.approx(13.4 * 25.4 / 96) and labels['halo_color'][0] == '#FFFFFF'
    assert (labels['font'][0], labels['font_style'][0], labels['font_name'][0]) == ('Arial', 'Bold', 'Demo Sans')
    assert labels['halo_color'].isna()[1] and labels['rotation'][1] == 15 and labels['font'][2] == 'Plain Alias'
    assert labels.geometry[0].coords[0] == (10.021, 0.421)
    leaders = gpd.read_file(gpkg, layer='map_label_leaders')
    assert list(leaders['label_id']) == [2, 3] and list(leaders['stroke_id']) == [1, 2]
    assert list(leaders.geometry[0].coords) == [(10.05, 0.45), (10.07, 0.47)]
    style = styles(gpkg)['map_labels']['qml']
    assert style.get('labelsEnabled') == '1' and style.find('renderer-v2').get('type') == 'nullSymbol'
    rules = style.findall('labeling/rules/rule')  # one rule per distinct text style, static settings
    assert [r.get('filter') for r in rules] == ['"style_id" = 1', '"style_id" = 2', '"style_id" = 3']
    first = rules[0].find('settings/text-style')
    size = 13.4 * 25.4 / 96
    assert (first.get('fontFamily'), first.get('namedStyle'), first.get('textOpacity')) == ('Arial', 'Bold', '0.9')
    assert float(first.get('fontLetterSpacing')) == pytest.approx(0.1 * size)
    buffer = first.find('text-buffer')
    assert buffer.get('bufferOpacity') == '0.78'
    blur = {o.get('name'): o.get('value') for o in buffer.find('effect').iter('Option')}
    assert float(blur['blur_level']) == pytest.approx(0.6 * 25.4 / 96) and blur['blur_unit'] == 'MM'
    box = first.find('background')
    assert (box.get('shapeDraw'), box.get('shapeFillColor'), box.get('shapeBorderColor')) == ('1', '255,255,204,255', '51,51,51,255')
    assert float(box.get('shapeSizeX')) == pytest.approx(2 * 25.4 / 96) and float(box.get('shapeBorderWidth')) == pytest.approx(0.5 * 25.4 / 96)
    defined = {o.get('name') for o in rules[0].find('settings/dd_properties/Option/Option[@name="properties"]')}
    assert defined == {'OffsetQuad', 'LabelRotation'}
    leader_style = styles(gpkg)['map_label_leaders']['qml']
    lines = [{o.get('name'): o.get('value') for o in layer.iter('Option')} for layer in leader_style.iter('layer')]
    assert (lines[0]['line_color'], lines[0]['use_custom_dash']) == ('0,0,255,255', '1')  # leaderStroke
    assert (lines[1]['line_color'], lines[1]['line_width']) == ('0,0,0,255', qgis.number(25.4 / 96))  # text colour, 1 px
    assert manifest['label_placements'] == {'labels': 3, 'leaders': 2, 'table': 'map_labels', 'leader_table': 'map_label_leaders',
                                            'text_styles': 3, 'leader_strokes': 2}


def test_embedded_project(packs):
    out, manifest = packs[1], packs[2]['extent']
    gpkg = out['extent'] / 'demo-map.gpkg'
    project = ET.fromstring(qgis.read_project(gpkg, 'demo-map').split('\n', 1)[1])
    assert project.find('projectCrs/spatialrefsys/authid').text == 'EPSG:32632' == manifest['project']['crs']
    assert project.find('properties/SpatialRefSys/ProjectionsEnabled').text == '1'
    order = [item.text.rsplit('_', 5)[0] for item in project.findall('layer-tree-group/custom-order/item')]
    assert order == ['map_labels_inset', 'map_labels_main', 'map_label_leaders_inset', 'map_label_leaders_main', 'inset_parcels', 'site_marks',
                     'names', 'sites', 'roads', 'parcels_outline', 'far', 'parcels', 'cover', 'height', 'ground']
    panels = project.findall('layer-tree-group/layer-tree-group')  # a group per panel first, then the map-style groups
    assert [(g.get('name'), g.get('checked')) for g in panels] == [('main', 'Qt::Checked'), ('inset', 'Qt::Unchecked')]
    assert [g.get('name') for g in panels[0].findall('layer-tree-group')] == ['labels', 'points', 'lines', 'fills', 'ground']
    assert [g.get('name') for g in panels[1].findall('layer-tree-group')] == ['labels', 'fills']
    unchecked = {l.get('name') for l in project.iter('layer-tree-layer') if l.get('checked') == 'Qt::Unchecked'}
    assert unchecked == {'容积率', 'cover'}
    themes = {t.get('name'): t for t in project.findall('visibility-presets/visibility-preset')}
    assert set(themes) == {'main', 'inset'}
    assert [n.get('id') for n in themes['inset'].iter('checked-group-node')] == ['inset', 'inset/fills', 'inset/labels']
    assert [b.get('name') for b in project.iter('Bookmark')] == ['main', 'inset']
    assert [project.find(f'properties/Gui/CanvasColor{c}Part').text for c in ('Red', 'Green', 'Blue')] == ['180', '214', '226']
    sources = {l.find('id').text.rsplit('_', 5)[0]: l.find('datasource').text for l in project.iter('maplayer')}
    assert sources['map_labels_inset'] == './demo-map.gpkg|layername=map_labels|subset="panel" = \'inset\''
    sources = {l.find('layername').text: l.find('datasource').text for l in project.iter('maplayer')}
    assert sources['用地分类'] == './demo-map.gpkg|layername=parcels' and sources['height'] == 'GPKG:./demo-map.gpkg:height'
    assert sources['cover'] == './rasters/cover.tif'
    raster = next(l for l in project.iter('maplayer') if l.find('layername').text == 'height')
    assert raster.find('pipe/rasterrenderer').get('type') == 'singlebandpseudocolor'
    assert [i.get('value') for i in raster.iter('item')] == ['0', '70', '138']
    extent = [float(project.find(f'mapcanvas/extent/{k}').text) for k in ('xmin', 'ymin', 'xmax', 'ymax')]
    assert extent == pytest.approx(manifest['project']['extent'])
    with sqlite3.connect(gpkg) as db:
        assert db.execute("SELECT data_type FROM gpkg_contents WHERE table_name = 'layer_styles'").fetchone() == ('attributes',)
    db.close()


def test_manifest_reports_unsupported_fonts_and_external_data(packs):
    manifest = packs[2]['extent']
    reasons = {(n['layer'], n['item']) for n in manifest['unsupported']}
    assert ('roads', 'filter') in reasons and ('height', 'renderer.stretch') in reasons and ('labelPlacements', 'font') in reasons
    assert any('arcade' in n['reason'] and 'skipped' in n['reason'] for n in manifest['unsupported'])
    fonts = {row['name']: row for row in manifest['fonts']}
    assert (fonts['Demo Sans']['family'], fonts['Demo Sans']['style']) == ('Arial', 'Bold') and 'labelPlacements' in fonts['Demo Sans']['used_by']
    assert fonts['Plain Alias']['unresolved']
    assert any('fonts Arial Bold, Plain Alias' in w for w in manifest['warnings'])
    assert manifest['not_packed'] == [{'ref': '@basemap/tiles', 'layers': ['tiles'], 'reason': 'rasterTiles: tiles are not packed'}]
    assert manifest['producer_notes'][0]['reason'] == 'a made-up note' and any('producer reports 1' in w for w in manifest['warnings'])
    assert manifest['background'] == '#B4D6E2'
    assert manifest['panels'] == [{'id': 'main', 'shown': True, 'bookmark': True}, {'id': 'inset', 'shown': False, 'bookmark': True}]
    assert any('external data roots' in w for w in manifest['warnings'])
    assert any('rasters/cover.tif' in w for w in manifest['warnings'])
    layers = {l['id']: l for l in manifest['layers']}
    assert layers['far']['qgis_renderer'] == 'RuleRenderer' and layers['far']['filter'] == '("类别" IS NOT NULL)'
    assert layers['parcels-outline']['labels'] == 'off' and layers['roads']['labels'] == 'on'


def test_filters_and_class_edges():
    notes = []
    assert qgis.expression({'field': 'a', 'op': '!=', 'value': "it's"}, {}, notes) == '("a" IS NULL OR "a" <> \'it\'\'s\')'
    assert qgis.expression({'field': 'a', 'op': 'notIn', 'value': [1, 2.5]}, {}, notes) == '("a" IS NULL OR "a" NOT IN (1, 2.5))'
    assert qgis.expression({'not': {'field': 'b', 'op': '==', 'value': True}}, {}, notes) == \
        '(NOT coalesce(("b" IS NOT NULL AND "b" = TRUE), FALSE))'
    assert qgis.expression({'any': [{'field': 'c', 'op': 'isNull'}, {'expression': '"c" > 1', 'dialect': 'QGIS'}]}, {}, notes) == \
        '(("c" IS NULL) OR ("c" > 1))'
    assert notes == []
    assert qgis.expression({'all': [{'field': 'c', 'op': 'isNull'}, {'expression': 'x', 'dialect': 'arcade'}]}, {'layer': 'l'}, notes) is None
    assert notes and notes[0]['layer'] == 'l'
    layer = {'id': 'g', 'geometry': 'polygon', 'renderer': {'type': 'graduated', 'field': 'v', 'classes': [
        {'range': [0, 1], 'symbol': {'fill': {'color': '#000000'}}}, {'range': [1, 2], 'symbol': {'fill': {'color': '#FFFFFF'}}}]}}
    text, kind = qgis.qml(layer, [], {})
    ranges = ET.fromstring(text.split('\n', 1)[1]).findall('renderer-v2/ranges/range')
    assert kind == 'graduatedSymbol'
    assert float(ranges[0].get('upper')) < 1.0 == float(ranges[1].get('lower'))  # a value of exactly 1 falls in the upper class
    assert float(ranges[1].get('upper')) == 2.0  # the last class includes its high
    with pytest.raises(ValueError):
        _map_style.check_filter({'field': 'a', 'op': 'in', 'value': 1}, 'here')


def test_marker_shapes():
    def marker(spec):
        layer = {'id': 'm', 'geometry': 'point', 'renderer': {'type': 'single', 'symbol': spec}}
        text, _ = qgis.qml(layer, notes, {})
        found = ET.fromstring(text.split('\n', 1)[1]).find(".//layer[@class='SimpleMarker']")
        values = {o.get('name'): o.get('value') for o in found.find('Option')}
        return values['name'], values['angle'], values['color']
    notes = []
    assert marker({'marker': {'shape': 'arrow', 'size': 8}, 'fill': {'color': '#FF0000'}}) == ('filled_arrowhead', '270', '255,0,0,255')
    assert marker({'marker': {'shape': 'chevron', 'size': 8, 'rotation': 90}, 'fill': {'color': '#FF0000'}}) == ('arrowhead', '0', '0,0,0,0')
    assert marker({'marker': {'shape': 'cross', 'size': 8}, 'fill': {'color': '#FF0000'}}) == ('cross_fill', '0', '255,0,0,255')
    assert marker({'marker': {'shape': 'x', 'size': 8}, 'fill': {'color': '#FF0000'}}) == ('cross_fill', '45', '255,0,0,255')
    assert marker({'marker': {'shape': 'cross', 'size': 8}, 'stroke': {'color': '#000000', 'width': 1}}) == ('cross', '0', '0,0,0,0')
    assert marker({'fill': {'color': '#FF0000'}})[0] == 'circle' and notes[-1]['item'] == 'symbol.marker'


def test_errors_are_clear(tmp_path):
    fixture = pack_fixture.build(tmp_path / 'data', with_raster=False)
    output = tmp_path / 'out'
    command = [sys.executable, str(SCRIPTS / 'package.py'), 'pack', str(fixture['style']), '--output', str(output)]
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'GIS_COMPOSER_CONFIG_DIR': str(tmp_path / 'no-config')}
    missing = subprocess.run(command, capture_output=True, text=True, env=env)
    assert missing.returncode == 1 and 'Data root @basemap has no path on this device' in missing.stderr and 'roots.json' in missing.stderr
    config = tmp_path / 'config'
    config.mkdir()
    (config / 'roots.json').write_text(json.dumps({'roots': {'basemap': str(fixture['basemap'])}}))
    assert subprocess.run(command, capture_output=True, text=True, env={**env, 'GIS_COMPOSER_CONFIG_DIR': str(config)}).returncode == 0
    undeclared = subprocess.run(command[:-2] + ['--output', str(tmp_path / 'o2'), '--data-root', f'other={tmp_path}'],
                                capture_output=True, text=True, env=env)
    assert 'not declared in gis-project.json' in undeclared.stderr
    document = json.loads(fixture['style'].read_text())
    document['layers'][1]['data']['sha256'] = '0' * 64
    document.pop('extent')
    changed = tmp_path / 'changed.json'
    changed.write_text(json.dumps(document))
    stale = run({**fixture, 'style': changed}, tmp_path / 'o3', '--full', '--project', fixture['project'], success=False)
    assert 'has changed since the map-style was written' in stale
    document['layers'][1]['data'].pop('sha256')
    changed.write_text(json.dumps(document))
    no_extent = run({**fixture, 'style': changed}, tmp_path / 'o4', '--project', fixture['project'], success=False)
    assert 'no extent.bbox' in no_extent and '--full' in no_extent
    no_project = run({**fixture, 'style': changed}, tmp_path / 'o5', '--full', success=False)
    assert 'no gis-project.json was found' in no_project
    assert run({**fixture, 'style': changed}, tmp_path / 'o6', '--full', '--project', fixture['project'])['scope'] == 'full'
    document['layers'][1]['data']['path'] = '../outside.gpkg'
    changed.write_text(json.dumps(document))
    assert 'must be relative, without ".."' in run({**fixture, 'style': changed}, tmp_path / 'o7', '--full', success=False)


def test_unreadable_data_is_listed_not_packed(tmp_path):
    fixture = pack_fixture.build(tmp_path / 'data', with_raster=False)
    tiles = fixture['basemap'] / 'folder'  # tiles without data.kind: GDAL cannot read the folder
    (tiles / '12').mkdir(parents=True)
    (tiles / '12' / '0.png').write_bytes(b'\x89PNG\r\n\x1a\n')
    document = json.loads(fixture['style'].read_text())
    document['dataRoot'] = 'file'
    document['layers'].insert(0, {'id': 'tile-folder', 'geometry': 'polygon', 'data': {'path': '@basemap/folder'},
                                  'renderer': {'type': 'single', 'symbol': {'fill': {'color': '#000000'}}}})
    style = fixture['project'] / 'tiles.map-style.json'  # dataRoot "file": paths relative to this file, here the project root
    style.write_text(json.dumps(document))
    manifest = run({**fixture, 'style': style}, tmp_path / 'out')
    assert {row['ref']: row['layers'] for row in manifest['not_packed']} == {'@basemap/tiles': ['tiles'], '@basemap/folder': ['tile-folder']}
    assert not {'tiles', 'tile-folder'} & {l['id'] for l in manifest['layers']}


@pytest.mark.skipif(QGIS_PYTHON is None, reason='QGIS not installed (set GIS_KIT_QGIS_PYTHON to its Python)')
def test_qgis_opens_the_package(packs, tmp_path):
    """QGIS itself reads the embedded project and the default styles, and renders the class colours."""
    gpkg = packs[1]['extent'] / 'demo-map.gpkg'
    resources = Path(QGIS_PYTHON).parents[2] / 'Resources'
    env = {**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'PYTHONDONTWRITEBYTECODE': '1'}
    if (resources / 'proj').is_dir():  # the macOS bundle does not set its own PROJ and GDAL data paths
        env.update(PROJ_DATA=str(resources / 'proj'), PROJ_LIB=str(resources / 'proj'), GDAL_DATA=str(resources / 'gdal'))
    for key in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV'):
        env.pop(key, None)
    points = ['10.005,0.405', '10.035,0.405', '10.065,0.405', '10.035,0.435', '10.0925,0.4075']
    result = subprocess.run([QGIS_PYTHON, str(TESTS / 'qgis_open_check.py'), str(gpkg), 'demo-map', str(tmp_path / 'map.png'), *points],
                            capture_output=True, text=True, env=env, timeout=300)
    assert result.returncode == 0, result.stderr[-2000:]
    report = json.loads(result.stdout)
    assert report['read'] and report['crs'] == 'EPSG:32632' and report['title'] == 'Demo map'
    by_id = {lid.rsplit('_', 5)[0]: row for lid, row in report['layers'].items()}
    layers = {row['name']: row for lid, row in report['layers'].items() if 'map_label' not in lid}
    assert all(row['valid'] for row in report['layers'].values()) and len(by_id) == 15
    assert layers['用地分类']['renderer'] == 'categorizedSymbol'
    assert [(c['value'], c['color'], c['render']) for c in layers['用地分类']['classes']] == [
        ('A 类', '#ed7d31', True), ('B 类', '#4f81bd', True), ('C 类', '#9bbb59', False), ('', '#bbbbbb', True)]
    assert layers['容积率']['renderer'] == 'RuleRenderer' and layers['容积率']['opacity'] == pytest.approx(0.7)
    assert [r['color'] for r in layers['容积率']['rules'][0]['children']] == ['#fff5eb', '#fd8d3c', '#a63603']
    assert layers['roads']['labels'] and not layers['parcels-outline']['labels'] and by_id['map_labels_main']['labels']
    assert layers['height']['renderer'] == 'singlebandpseudocolor' and layers['cover']['renderer'] == 'paletted'
    assert layers['sites']['size_expression'].startswith('2 * 0.26458333333333334')
    order = [lid.rsplit('_', 5)[0] for lid in report['drawing_order_top_first']]
    assert order == ['map_labels_inset', 'map_labels_main', 'map_label_leaders_inset', 'map_label_leaders_main', 'inset_parcels', 'site_marks',
                     'names', 'sites', 'roads', 'parcels_outline', 'far', 'parcels', 'cover', 'height', 'ground']
    assert [t['group'] for t in report['tree']] == ['main', 'inset']
    assert not report['checked'][next(l for l in report['checked'] if l.startswith('inset_parcels'))]  # the inset panel is hidden
    assert report['background'] == '#b4d6e2' and report['bookmarks'] == ['main', 'inset']
    assert sorted(lid.rsplit('_', 5)[0] for lid in report['themes']['inset']) == ['inset_parcels', 'map_label_leaders_inset', 'map_labels_inset']
    assert by_id['map_labels_inset']['subset'] == '"panel" = \'inset\'' and by_id['map_labels_inset']['features'] == 1
    first = by_id['map_labels_main']['label_settings'][0]
    assert (first['family'], first['style'], first['installed'], first['background']) == ('Arial', 'Bold', True, True)
    assert (layers['names']['label_settings'][0]['family'], layers['names']['label_settings'][0]['style']) == ('Arial', 'Bold')
    alone = report['tables_alone']
    assert alone['parcels']['renderer'] == 'categorizedSymbol' and alone['parcels']['styles'] == ['far', 'inset.parcels', 'parcels', 'parcels-outline']
    assert alone['map_labels']['renderer'] == 'nullSymbol' and alone['map_labels']['labels']
    colors = [s['color'] for s in report['samples']]
    assert colors[:4] == ['#bbbbbb', '#ed7d31', '#4f81bd', '#4f81bd']  # default class, A, B, B: as the map-style says
    assert colors[4] != '#9bbb59'  # the hidden C class is not drawn
    assert (tmp_path / 'map.png').stat().st_size > 1000


def test_clip_keeps_field_types_and_split_parts(tmp_path):
    """Integer and boolean fields with NULLs keep their types; a polygon cut in two becomes a MultiPolygon table."""
    import pandas as pd
    from shapely.geometry import Polygon
    fixture = pack_fixture.build(tmp_path / 'data', with_raster=False)
    # a U shape whose two arms reach into the extent from the south, joined below it
    u = Polygon([(10.01, 0.30), (10.09, 0.30), (10.09, 0.45), (10.07, 0.45), (10.07, 0.35), (10.03, 0.35), (10.03, 0.45), (10.01, 0.45)])
    frame = gpd.GeoDataFrame({'count': pd.array([7, None], dtype='Int64'), 'flag': pd.array([True, None], dtype='boolean')},
                             geometry=[u, Polygon([(10.04, 0.42), (10.06, 0.42), (10.06, 0.44), (10.04, 0.44)])], crs='EPSG:4326')
    pyogrio.write_dataframe(frame, fixture['project'] / 'inputs' / 'shapes.gpkg', layer='shapes')
    document = json.loads(fixture['style'].read_text())
    document['layers'] = [{'id': 'shapes', 'geometry': 'polygon', 'data': {'path': 'inputs/shapes.gpkg'},
                           'renderer': {'type': 'single', 'symbol': {'fill': {'color': '#123456'}}}}]
    document.pop('labelPlacements')
    fixture['style'].write_text(json.dumps(document))
    manifest = run(fixture, tmp_path / 'out', '--clip')
    gpkg = tmp_path / 'out' / 'demo-map.gpkg'
    info = pyogrio.read_info(gpkg, layer='shapes')
    assert info['geometry_type'] == 'MultiPolygon'
    assert dict(zip(info['fields'], info['ogr_types'])) == {'count': 'OFTInteger64', 'flag': 'OFTInteger', 'clipped': 'OFTInteger'}
    assert dict(zip(info['fields'], info['ogr_subtypes']))['flag'] == 'OFSTBoolean'
    shapes = gpd.read_file(gpkg, layer='shapes')
    assert list(shapes['clipped']) == [True, False] and len(shapes.geometry[0].geoms) == 2
    assert manifest['datasets'][0]['clipped_features'] == 1 and not any('pyarrow' in w for w in manifest['warnings'])
