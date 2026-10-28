"""cad.py inspect: header, layer inventory, outliers and candidate CRS on hand-written R12 DXF files."""
from __future__ import annotations

import json
import math
import os
import shutil
from pathlib import Path
import subprocess
import sys

import geopandas as gpd
import pyogrio
import pytest
import shapely
from shapely.geometry import LineString, Point, Polygon, box

SCRIPTS = Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0, str(SCRIPTS))


def pairs(*items):
    return ''.join(f'{code}\n{value}\n' for code, value in items)


def dxf(path, entities, units=6, codepage='ANSI_936', encoding='gbk', header=True):
    """Minimal R12 DXF: HEADER (variables optional), a block with one circle, then the given ENTITIES pairs."""
    variables = [(9, '$ACADVER'), (1, 'AC1009'), (9, '$INSUNITS'), (70, units), (9, '$DWGCODEPAGE'), (3, codepage)]
    text = pairs((0, 'SECTION'), (2, 'HEADER'), *(variables if header else []), (0, 'ENDSEC'))  # GDAL needs the section
    text += pairs((0, 'SECTION'), (2, 'BLOCKS'), (0, 'BLOCK'), (8, '0'), (2, 'TREE'), (70, 0), (10, 0), (20, 0), (30, 0),
                  (0, 'CIRCLE'), (8, '0'), (10, 0), (20, 0), (30, 0), (40, 1), (0, 'ENDBLK'), (8, '0'), (0, 'ENDSEC'))
    text += pairs((0, 'SECTION'), (2, 'ENTITIES'), *entities, (0, 'ENDSEC'), (0, 'EOF'))
    path.write_text(text, encoding=encoding)
    return path


def line(layer, x0, y0, x1, y1, paper=False):
    return [(0, 'LINE'), (8, layer), *([(67, 1)] if paper else []), (10, x0), (20, y0), (30, 0), (11, x1), (21, y1), (31, 0)]


def label(layer, x, y, text):
    return [(0, 'TEXT'), (8, layer), (10, x), (20, y), (30, 0), (40, 2.5), (1, text)]


def site(x0, y0, step=1.0):
    """Roads, a closed building outline, a street name and a block reference around (x0, y0)."""
    s = step
    return [*line('道路', x0, y0, x0+100*s, y0+50*s), *line('道路', x0, y0+80*s, x0+120*s, y0+80*s),
            (0, 'POLYLINE'), (8, '建筑'), (66, 1), (70, 1), (10, 0), (20, 0), (30, 0),
            *[item for x, y in ((10, 10), (30, 10), (30, 30)) for item in ((0, 'VERTEX'), (8, '建筑'), (10, x0+x*s), (20, y0+y*s), (30, 0))],
            (0, 'SEQEND'), (8, '建筑'), *label('注记', x0+20*s, y0+20*s, '人民路'),
            (0, 'INSERT'), (8, '绿化'), (2, 'TREE'), (10, x0+50*s), (20, y0+5*s), (30, 0)]


# Runs cad.py as __main__ with ezdxf made unimportable, to exercise the GDAL-only inventory.
WITHOUT_EZDXF = ("import runpy, sys; sys.modules['ezdxf'] = None; sys.path.insert(0, sys.argv[1]); "
                 "sys.argv = sys.argv[1:]; sys.argv[0] = sys.argv[0] + '/cad.py'; runpy.run_path(sys.argv[0], run_name='__main__')")


def inspect(source, out, *extra, success=True, ezdxf=True):
    command = [sys.executable, str(SCRIPTS/'cad.py')] if ezdxf else [sys.executable, '-c', WITHOUT_EZDXF, str(SCRIPTS)]
    result = subprocess.run([*command, 'inspect', str(source), '--output', str(out), *map(str, extra)],
                            capture_output=True, text=True, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
    assert (result.returncode == 0) == success, result.stdout+result.stderr
    return (json.loads((out/'cad.json').read_text()) if success else result.stderr)


def codes(report):
    return [row['crs'] for reading in report['crs']['readings'] for match in reading['matches'] for row in match['candidates']]


def test_zone_prefixed_grid_inventory_and_outputs(tmp_path):
    pytest.importorskip('ezdxf')
    source = dxf(tmp_path/'site.dxf', site(39_500_000, 3_400_000))
    report = inspect(source, tmp_path/'out')
    assert report['header'] == {'readable': True, 'acadver': 'AC1009', 'release': 'R12', 'insunits': 6, 'units': 'meters',
                                'codepage': 'ANSI_936', 'extent': None, 'handles': False}
    layers = {row['layer']: row for row in report['layers']}
    assert set(layers) == {'道路', '建筑', '注记', '绿化'} and layers['道路']['entities'] == 2
    assert layers['注记']['text_samples'] == [{'raw': '人民路', 'clean': '人民路'}] and 'texts' not in layers['道路']
    assert layers['道路']['dxf_types'] == {'LINE': 2} and layers['建筑']['dxf_types'] == {'POLYLINE closed': 1}
    assert layers['绿化']['blocks'] == {'TREE': 1} and report['reader']['missing'] == []
    assert report['entities']['model_space'] == 5 and report['reader']['inventory'].startswith('ezdxf')
    assert report['crs']['status'] == 'candidate' and report['crs']['assigned'] is None
    assert codes(report) == ['EPSG:4527', 'EPSG:4775', 'EPSG:2363', 'EPSG:2415']  # CGCS2000 first, all four datums
    lon, lat = report['crs']['readings'][0]['matches'][0]['candidates'][0]['lonlat']
    assert lon == pytest.approx(117, abs=0.01) and lat == pytest.approx(30.72, abs=0.01)
    assert (tmp_path/'out'/'check.png').stat().st_size > 10_000
    record = json.loads((tmp_path/'out'/'record.json').read_text())
    assert record['source']['sha256'] == report['source']['sha256'] and set(record['files']) == {'cad.json', 'check.png'}
    assert 'new directory' in inspect(source, tmp_path/'out', success=False)
    assert not list(tmp_path.glob('.delivery-*'))


def test_unprefixed_grid_is_held_even_with_near(tmp_path):
    source = dxf(tmp_path/'hefei.dxf', site(518_900, 3_520_000))  # CGCS2000 CM 117E without prefix, or a local grid
    for name, extra in (('held', ()), ('near', ('--near', '117.2,31.8'))):
        report = inspect(source, tmp_path/name, *extra)
        assert report['crs']['status'] == 'hold' and codes(report) == [] and report['crs']['rejected'] == []
        assert any('local grid' in note and 'no EPSG code' in note for note in report['crs']['notes'])


def test_near_rejects_distant_candidates(tmp_path):
    source = dxf(tmp_path/'site.dxf', site(39_500_000, 3_400_000))
    kept = inspect(source, tmp_path/'kept', '--near', '117.2,31.0')
    assert codes(kept)[0] == 'EPSG:4527'
    assert all(row['distance_km'] <= 50 for match in kept['crs']['readings'][0]['matches'] for row in match['candidates'])
    far = inspect(source, tmp_path/'far', '--near', '87.6,43.8')  # Urumqi
    assert codes(far) == [] and far['crs']['status'] == 'hold'
    assert {row['crs'] for row in far['crs']['rejected']} == {'EPSG:4527', 'EPSG:4775', 'EPSG:2363', 'EPSG:2415'}


def test_millimetre_drawing_uses_header_units(tmp_path):
    source = dxf(tmp_path/'mm.dxf', site(39_500_000_000, 3_400_000_000, step=1000), units=4)
    report = inspect(source, tmp_path/'out')
    first, second = report['crs']['readings']
    assert first['matches'] == [] and second['scale_to_metre'] == 0.001 and second['basis'] == 'header $INSUNITS = millimeters'
    assert codes(report)[0] == 'EPSG:4527'


def test_outliers_paper_space_and_local_grid(tmp_path):
    entities = site(1200, 800) + line('图框', 0, 0, 0.5, 0.5) + line('图框', 9e5, 9e5, 9e5+1, 9e5+1) \
        + line('布局', 0, 0, 420, 297, paper=True)
    source = dxf(tmp_path/'local.dxf', entities, header=False)  # no $DWGCODEPAGE: GDAL falls back to Latin-1
    auto = inspect(source, tmp_path/'auto')  # GBK read as Latin-1 is noticed and read again
    assert auto['encoding']['source'] == 'auto' and auto['encoding']['used'] == 'CP936'
    assert auto['encoding']['tried'] == {'CP936': 0, 'GB18030': 0} and '道路' in {r['layer'] for r in auto['layers']}
    assert any('read again as CP936' in w for w in auto['warnings'])
    garbled = inspect(source, tmp_path/'garbled', '--encoding', 'CP1252')  # an explicit choice is kept, however wrong
    assert garbled['encoding']['source'] == 'option' and '道路' not in {r['layer'] for r in garbled['layers']}
    report = inspect(source, tmp_path/'out', '--encoding', 'CP936')
    assert report['header']['readable'] is False and report['entities']['paper_space'] == 1
    assert report['extent']['outliers'] == 2 and report['extent']['core'][2] < 2000
    assert report['crs']['status'] == 'hold'
    assert any('图框 2' in w for w in report['warnings']) and any('paper-space' in w for w in report['warnings'])
    assert not any('--encoding' in w for w in report['warnings'])


def test_geographic_coordinates(tmp_path):
    report = inspect(dxf(tmp_path/'lonlat.dxf', site(117.2, 31.8, step=0.0001)), tmp_path/'out')
    assert codes(report)[:2] == ['EPSG:4490', 'EPSG:4326']


def test_r2000_entity_types_and_hatch_is_not_text(tmp_path):
    pytest.importorskip('ezdxf')
    frame = gpd.GeoDataFrame({'Layer': ['A', 'B', 'C']}, geometry=[LineString([(0, 0), (10, 10)]), Point(5, 5),
                                                                   Polygon([(0, 0), (10, 0), (10, 10)])])
    source = tmp_path/'r2000.dxf'
    with pytest.warns(UserWarning):
        pyogrio.write_dataframe(frame, source, driver='DXF')
    report = inspect(source, tmp_path/'out', ezdxf=False)
    layers = {row['layer']: row for row in report['layers']}
    assert layers['C']['entity_types'] == {'AcDbHatch': 1} and 'texts' not in layers['C']
    assert report['header']['release'] == 'R2004'
    full = {row['layer']: row for row in inspect(source, tmp_path/'ezdxf')['layers']}
    assert full['C']['dxf_types'] == {'HATCH': 1} and 'texts' not in full['C']


def test_gdal_fallback_lists_what_is_missing(tmp_path):
    source = dxf(tmp_path/'site.dxf', site(39_500_000, 3_400_000))
    report = inspect(source, tmp_path/'out', ezdxf=False)
    assert report['reader']['inventory'] == 'gdal' and 'block names and attribute tags' in report['reader']['missing']
    layers = {row['layer']: row for row in report['layers']}
    assert layers['注记']['text_samples'] == [{'raw': '人民路'}] and 'dxf_types' not in layers['注记']
    assert report['layouts'] is None and report['unused_layers'] is None
    assert codes(report)[0] == 'EPSG:4527'  # geometry, extent and CRS do not depend on ezdxf


def test_ezdxf_inventory_of_an_r2018_drawing(tmp_path):
    ezdxf = pytest.importorskip('ezdxf')
    doc = ezdxf.new('R2018', setup=True)
    doc.header['$INSUNITS'] = 6
    for name in ('地块界线', '地块编号', '用地代码', '图框', '辅助线'):
        doc.layers.add(name)
    doc.layers.get('辅助线').off()
    doc.layers.get('图框').freeze()
    msp = doc.modelspace()
    x0, y0 = 38_500_000, 2_490_000
    msp.add_lwpolyline([(x0, y0), (x0+100, y0), (x0+100, y0+80), (x0, y0+80)], close=True, dxfattribs={'layer': '地块界线'})
    msp.add_lwpolyline([(x0+100, y0), (x0+180, y0), (x0+180, y0+80)], dxfattribs={'layer': '地块界线'})
    msp.add_mtext(r'{\fSimSun|b0;03-03-02}\P', dxfattribs={'layer': '地块编号', 'insert': (x0+50, y0+40)})
    msp.add_text('Ｒ２＋Ｃ１', dxfattribs={'layer': '用地代码', 'insert': (x0+50, y0+30)})
    msp.add_line((x0, y0), (x0+180, y0+80), dxfattribs={'layer': '辅助线'})
    block = doc.blocks.new('地块标签')
    block.add_attdef('BH', (0, 0))
    block.add_circle((0, 0), 1)
    for point, value in (((x0+20, y0+20), '03-01'), ((x0+140, y0+20), '03-02')):
        msp.add_blockref('地块标签', point, dxfattribs={'layer': '地块编号'}).add_attrib('BH', value, point)
    layout = doc.layouts.new('图则')
    layout.add_viewport((150, 100), (200, 150), (x0+90, y0+40), 100)
    source = tmp_path/'r2018.dxf'
    doc.saveas(source)

    report = inspect(source, tmp_path/'out')
    layers = {row['layer']: row for row in report['layers']}
    assert layers['地块界线']['dxf_types'] == {'LWPOLYLINE closed': 1, 'LWPOLYLINE open': 1}
    assert layers['地块编号']['dxf_types'] == {'INSERT': 2, 'MTEXT': 1}
    assert layers['地块编号']['blocks'] == {'地块标签': 2} and layers['地块编号']['attributes'] == {'BH': ['03-01', '03-02']}
    assert layers['地块编号']['text_samples'] == [{'raw': r'{\fSimSun|b0;03-03-02}\P', 'clean': '03-03-02'}]
    assert layers['用地代码']['text_samples'] == [{'raw': 'Ｒ２＋Ｃ１', 'clean': 'R2+C1'}]
    assert layers['辅助线']['state'] == ['off'] and 'state' not in layers['地块界线']
    assert report['unused_layers'] == ['图框']
    assert report['encoding'] == {'declared': 'ANSI_1252', 'used': 'UTF-8', 'source': 'dxf-version', 'undecodable_strings': 0}
    assert layers['地块界线']['bounds'] is not None  # GDAL names match ezdxf names only when GDAL reads UTF-8 too
    assert {row['name']: row['viewports'] for row in report['layouts']}['图则'] == 1
    assert report['header']['release'] == 'R2018' and codes(report)[0] == 'EPSG:4526'


def test_refuses_dwg_and_other_inputs(tmp_path):
    (tmp_path/'plan.dwg').write_bytes(b'AC1032')
    assert 'save it as DXF' in inspect(tmp_path/'plan.dwg', tmp_path/'a', success=False)
    assert '.dxf' in inspect(tmp_path/'missing.dxf', tmp_path/'b', success=False)
    assert not (tmp_path/'a').exists() and not (tmp_path/'b').exists()


def reference(path, crs, bounds):
    gpd.GeoDataFrame({'name': ['范围']}, geometry=[box(*bounds)], crs=crs).to_file(path)
    return path


def test_reference_overlap_lists_its_crs_for_unprefixed_coordinates(tmp_path):
    source = dxf(tmp_path/'qianhai.dxf', site(495_000, 2_490_000))  # CGCS2000 CM 114E without prefix
    ref = reference(tmp_path/'range.gpkg', 'EPSG:4547', (494_000, 2_489_000, 497_000, 2_492_000))
    report = inspect(source, tmp_path/'out', '--reference', ref)
    comparison = report['crs']['reference']['comparisons'][0]
    assert comparison['verdict'] == 'extents-overlap' and comparison['overlap'] == 1.0
    assert report['crs']['status'] == 'candidate' and codes(report) == ['EPSG:4547']
    match = report['crs']['readings'][0]['matches'][0]
    assert match['grid'] == 'reference layer (extents-overlap)' and match['candidates'][0]['basis'] == 'reference'
    record = json.loads((tmp_path/'out'/'record.json').read_text())
    assert record['reference']['sha256'] and record['reference']['layer'] == 'range'


def test_reference_zone_prefix_both_ways(tmp_path):
    bare = dxf(tmp_path/'bare.dxf', site(495_000, 2_490_000))
    prefixed_ref = reference(tmp_path/'zone38.gpkg', 'EPSG:4526', (38_494_000, 2_489_000, 38_497_000, 2_492_000))
    report = inspect(bare, tmp_path/'a', '--reference', prefixed_ref)
    comparison = report['crs']['reference']['comparisons'][0]
    assert comparison['verdict'] == 'zone-prefix' and comparison['zone'] == 38 and comparison['drawing_has_prefix'] is False
    assert codes(report) == ['EPSG:4547']  # the reference's grid without the prefix
    prefixed = dxf(tmp_path/'prefixed.dxf', site(38_495_000, 2_490_000))
    bare_ref = reference(tmp_path/'cm114.gpkg', 'EPSG:4547', (494_000, 2_489_000, 497_000, 2_492_000))
    report = inspect(prefixed, tmp_path/'b', '--reference', bare_ref)
    assert report['crs']['reference']['comparisons'][0]['twin']['crs'] == 'EPSG:4526'
    assert codes(report).count('EPSG:4526') == 1  # already a coordinate candidate; not listed twice


def test_reference_shift_and_magnitude_are_held(tmp_path):
    ref = reference(tmp_path/'range.gpkg', 'EPSG:4547', (494_000, 2_489_000, 497_000, 2_492_000))
    shifted = inspect(dxf(tmp_path/'shift.dxf', site(480_000, 2_470_000)), tmp_path/'shift', '--reference', ref)
    comparison = shifted['crs']['reference']['comparisons'][0]
    assert comparison['verdict'] == 'shift-or-rotation' and comparison['overlap'] == 0.0
    assert comparison['offset'][0] == pytest.approx(15_440, abs=1) and shifted['crs']['status'] == 'hold'
    assert any('control points' in note and 'offset' in note for note in shifted['crs']['notes'])
    local = inspect(dxf(tmp_path/'local.dxf', site(105_000, 32_000)), tmp_path/'local', '--reference', ref)
    assert local['crs']['reference']['comparisons'][0]['verdict'] == 'different-magnitude'
    assert local['crs']['status'] == 'hold' and codes(local) == []


def test_reference_must_be_projected_in_metres(tmp_path):
    source = dxf(tmp_path/'site.dxf', site(495_000, 2_490_000))
    ref = reference(tmp_path/'lonlat.gpkg', 'EPSG:4326', (113.8, 22.4, 114.0, 22.6))
    assert 'projected CRS in metres' in inspect(source, tmp_path/'out', '--reference', ref, success=False)
    assert not (tmp_path/'out').exists()


def assign(source, out, *extra, success=True):
    result = subprocess.run([sys.executable, str(SCRIPTS/'cad.py'), 'assign-crs', str(source), '--output', str(out), *map(str, extra)],
                            capture_output=True, text=True, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
    assert (result.returncode == 0) == success, result.stdout+result.stderr
    return json.loads((out/'manifest.json').read_text()) if success else result.stderr


def converted(tmp_path, entities, name='drawing', **options):
    """A drawing, its inspection and its CRS-less GPKG made by convert.py."""
    source = dxf(tmp_path/f'{name}.dxf', entities, **options)
    inspect(source, tmp_path/f'{name}-inspect')
    gpkg = tmp_path/f'{name}.gpkg'
    run = subprocess.run([sys.executable, str(SCRIPTS/'convert.py'), str(source), '--output', str(gpkg)], capture_output=True, text=True)
    assert run.returncode == 0, run.stdout+run.stderr
    return gpkg, tmp_path/f'{name}-inspect'


CONFIRM = ('--confirmed-by', '张三', '--basis', '数据提供方书面说明为 CGCS2000 3 度带 39 带')


def test_assign_crs_labels_without_touching_coordinates(tmp_path):
    gpkg, inspection = converted(tmp_path, site(39_500_000, 3_400_000))
    before = gpd.read_file(gpkg)
    assert before.crs is None
    manifest = assign(gpkg, tmp_path/'out', '--inspection', inspection, '--crs', 'EPSG:4527', *CONFIRM)
    after = gpd.read_file(tmp_path/'out'/'drawing.gpkg')
    assert after.crs.to_epsg() == 4527 and len(after) == len(before)
    assert list(shapely.to_wkb(after.geometry.to_numpy())) == list(shapely.to_wkb(before.geometry.to_numpy()))
    assert manifest['confirmed_by'] == '张三' and manifest['basis'].startswith('数据提供方')
    assert manifest['crs'] == {'id': 'EPSG:4527', 'name': 'CGCS2000 / 3-degree Gauss-Kruger zone 39', 'datum': 'China 2000'}
    assert manifest['outside_candidates'] is False and manifest['checks']['geometry_wkb_identical'] is True
    assert manifest['inspection']['drawing'] == 'drawing.dxf' and manifest['input']['sha256']
    assert gpd.read_file(gpkg).crs is None  # the input is not modified


def test_assign_crs_refusals(tmp_path):
    gpkg, inspection = converted(tmp_path, site(39_500_000, 3_400_000))
    base = ('--inspection', inspection)
    assert 'must not be blank' in assign(gpkg, tmp_path/'a', *base, '--crs', 'EPSG:4527', '--confirmed-by', ' ', '--basis', 'x', success=False)
    assert 'not among the inspection candidates' in assign(gpkg, tmp_path/'b', *base, '--crs', 'EPSG:4547', *CONFIRM, success=False)
    assert 'projected CRS in metres' in assign(gpkg, tmp_path/'c', *base, '--crs', 'EPSG:4490', *CONFIRM, success=False)
    outside = assign(gpkg, tmp_path/'d', *base, '--crs', 'EPSG:4547', *CONFIRM, '--outside-candidates')
    assert outside['outside_candidates'] is True and outside['inspection']['candidates_as_drawn'][0] == 'EPSG:2363'
    labelled = tmp_path/'d'/'drawing.gpkg'
    assert 'already has a CRS' in assign(labelled, tmp_path/'e', *base, '--crs', 'EPSG:4527', *CONFIRM, success=False)
    moved = tmp_path/'moved.gpkg'
    frame = gpd.read_file(gpkg)
    frame.set_geometry(frame.translate(5000, 0)).to_file(moved)
    assert 'outside the inspected drawing' in assign(moved, tmp_path/'f', *base, '--crs', 'EPSG:4527', *CONFIRM, success=False)
    assert 'new directory' in assign(gpkg, tmp_path/'d', *base, '--crs', 'EPSG:4527', *CONFIRM, success=False)
    assert not any((tmp_path/name).exists() for name in 'abcef')


def test_assign_crs_refuses_a_candidate_that_needs_unit_scaling(tmp_path):
    gpkg, inspection = converted(tmp_path, site(39_500_000_000, 3_400_000_000, step=1000), name='mm', units=4)
    message = assign(gpkg, tmp_path/'out', '--inspection', inspection, '--crs', 'EPSG:4527', *CONFIRM, '--outside-candidates', success=False)
    assert 'only after unit scaling' in message and not (tmp_path/'out').exists()


def extract(source, out, *extra, success=True, ezdxf=True):
    command = [sys.executable, str(SCRIPTS/'cad.py')] if ezdxf else [sys.executable, '-c', WITHOUT_EZDXF, str(SCRIPTS)]
    result = subprocess.run([*command, 'extract', str(source), '--output', str(out), *map(str, extra)],
                            capture_output=True, text=True, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
    assert (result.returncode == 0) == success, result.stdout+result.stderr
    return json.loads((out/'manifest.json').read_text()) if success else result.stderr


def layer(out, name):
    return gpd.read_file(out/'raw.gpkg', layer=name)


def plan_drawing(path, units=6, scale=1.0, z=0.0):
    """R2018 parcels: a bulged boundary, an MTEXT number, a full-width code, an aligned TEXT and attributed blocks."""
    ezdxf = pytest.importorskip('ezdxf')
    doc = ezdxf.new('R2018')
    doc.header['$INSUNITS'] = units
    msp = doc.modelspace()
    x0, y0, s = 38_500_000*scale, 2_490_000*scale, scale
    # Two straight sides and a semicircular side of radius 50: bulge 1 between (x0+100, y0) and (x0+100, y0+100).
    msp.add_lwpolyline([(x0, y0, 0, 0, 0), (x0+100*s, y0, 0, 0, 1), (x0+100*s, y0+100*s, 0, 0, 0), (x0, y0+100*s, 0, 0, 0)],
                       format='xyseb', close=True, dxfattribs={'layer': '地块界线', 'elevation': z})
    msp.add_line((x0, y0+100*s), (x0+300*s, y0+100*s), dxfattribs={'layer': '道路'})
    msp.add_mtext(r'{\fSimSun|b0;03-03-02}\P', dxfattribs={'layer': '地块编号', 'insert': (x0+50*s, y0+50*s), 'char_height': 2.5*s})
    msp.add_text('Ｒ２＋Ｃ１', dxfattribs={'layer': '用地代码', 'insert': (x0+50*s, y0+40*s), 'height': 2*s})
    aligned = msp.add_text('C1', dxfattribs={'layer': '用地代码', 'height': 2*s})
    aligned.set_placement((x0+60*s, y0+40*s), align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)
    block = doc.blocks.new('地块标签')
    block.add_attdef('BH', (0, 0))
    block.add_circle((0, 0), 1*s)
    for point, value in (((x0+20*s, y0+20*s), '03-01'), ((x0+80*s, y0+20*s), '03-02')):
        msp.add_blockref('地块标签', point, dxfattribs={'layer': '地块编号'}).add_attrib('BH', value, point)
    doc.saveas(path)
    return path


def test_extract_layers_fields_and_counts_match_inspect(tmp_path):
    source = plan_drawing(tmp_path/'plan.dxf')
    report = inspect(source, tmp_path/'inspect')
    manifest = extract(source, tmp_path/'out', '--units', 'header')
    counts = {row['layer']: row['entities'] for row in report['layers']}
    assert {k: sum(v.values()) for k, v in manifest['read'].items()} == counts  # same entities as the inspection
    assert manifest['layers'] == {'lines': 2, 'closed_polygons': 1, 'texts': 5, 'blocks': 2, 'leaders': 0} and manifest['crs'] is None
    lines, polygons, texts, blocks = (layer(tmp_path/'out', n) for n in ('lines', 'closed_polygons', 'texts', 'blocks'))
    assert lines.crs is None and set(lines['layer']) == {'地块界线', '道路'} and lines['handle'].notna().all()
    assert polygons['valid'].all() and polygons['handle'].iloc[0] == lines.set_index('dxf_type').loc['LWPOLYLINE', 'handle']
    by_raw = texts.set_index('text_raw')
    assert by_raw.loc[r'{\fSimSun|b0;03-03-02}\P', 'text_clean'] == '03-03-02'
    assert by_raw.loc['Ｒ２＋Ｃ１', 'text_clean'] == 'R2+C1' and by_raw.loc['C1', 'anchor'] == 'align'
    assert by_raw.loc['C1'].geometry.x == pytest.approx(38_500_060)
    attribs = texts[texts['dxf_type'] == 'ATTRIB']
    assert sorted(attribs['text_clean']) == ['03-01', '03-02'] and set(attribs['tag']) == {'BH'}
    assert set(attribs['block']) == {'地块标签'} and attribs['source_handle'].notna().all()
    assert sorted(json.loads(a)['BH'] for a in blocks['attributes']) == ['03-01', '03-02']
    assert manifest['warnings'] == []  # R2018 carries real handles


def test_extract_flattens_arcs_within_tolerance(tmp_path):
    source = plan_drawing(tmp_path/'plan.dxf')
    for tolerance in (0.05, 0.5):
        extract(source, tmp_path/f'out{tolerance}', '--units', 'm', '--layers', '地块界线', '--arc-tolerance', tolerance)
        ring = layer(tmp_path/f'out{tolerance}', 'closed_polygons').geometry.iloc[0].exterior
        centre = (38_500_100, 2_490_050)
        arc = [p for p in ring.coords if p[0] > 38_500_100 + 1e-6]
        assert arc, 'the bulge side bows outwards'
        mids = [((a[0]+b[0])/2, (a[1]+b[1])/2) for a, b in zip(arc, arc[1:])]
        assert max(50 - math.hypot(x-centre[0], y-centre[1]) for x, y in mids) <= tolerance + 1e-9
    assert len(layer(tmp_path/'out0.05', 'lines').geometry.iloc[0].coords) > len(layer(tmp_path/'out0.5', 'lines').geometry.iloc[0].coords)


def test_extract_explodes_blocks_on_request(tmp_path):
    source = plan_drawing(tmp_path/'plan.dxf')
    manifest = extract(source, tmp_path/'out', '--units', 'm', '--layers', '地块编号', '--include-blocks')
    assert manifest['layers'] == {'lines': 2, 'closed_polygons': 0, 'texts': 3, 'blocks': 0, 'leaders': 0}
    circles = layer(tmp_path/'out', 'lines')
    assert set(circles['dxf_type']) == {'CIRCLE'} and set(circles['layer']) == {'地块编号'}  # layer 0 takes the reference's
    assert circles['handle'].isna().all() and circles['source_handle'].notna().all() and set(circles['block']) == {'地块标签'}


def test_extract_units(tmp_path):
    mm = plan_drawing(tmp_path/'mm.dxf', units=4, scale=1000)
    manifest = extract(mm, tmp_path/'mm', '--units', 'header', '--layers', '道路')
    assert manifest['parameters']['scale_to_metre'] == 0.001
    assert layer(tmp_path/'mm', 'lines').total_bounds[0] == pytest.approx(38_500_000)
    lying = plan_drawing(tmp_path/'lying.dxf', units=4)  # says millimetres, drawn in metres
    assert 'pass --units m' in extract(lying, tmp_path/'lying', '--units', 'header', success=False)
    assert extract(lying, tmp_path/'forced', '--units', 'm')['parameters']['units_basis'] == '--units m'
    unitless = plan_drawing(tmp_path/'none.dxf', units=0)
    assert 'unitless' in extract(unitless, tmp_path/'none', '--units', 'header', success=False)


def test_extract_refuses_z_unless_dropped(tmp_path):
    source = plan_drawing(tmp_path/'z.dxf', z=12.5)
    assert 'non-zero Z' in extract(source, tmp_path/'a', '--units', 'm', success=False)
    manifest = extract(source, tmp_path/'b', '--units', 'm', '--drop-z')
    assert manifest['z']['features'] == 2 and manifest['z']['dropped'] and manifest['z']['max'] == 12.5
    assert not layer(tmp_path/'b', 'lines').has_z.any()


def test_extract_refusals_and_r12_handles(tmp_path):
    source = dxf(tmp_path/'site.dxf', site(39_500_000, 3_400_000))
    message = extract(source, tmp_path/'a', '--units', 'm', '--layers', '不存在', success=False)
    assert 'No model-space entities on layers 不存在' in message and '道路' in message
    assert 'needs ezdxf' in extract(source, tmp_path/'b', '--units', 'm', success=False, ezdxf=False)
    manifest = extract(source, tmp_path/'c', '--units', 'm')
    assert any('handles' in w for w in manifest['warnings'])
    assert 'new directory' in extract(source, tmp_path/'c', '--units', 'm', success=False)
    assert not (tmp_path/'a').exists() and not (tmp_path/'b').exists()


def test_extract_then_assign_crs(tmp_path):
    source = plan_drawing(tmp_path/'plan.dxf')
    inspect(source, tmp_path/'inspect')
    extract(source, tmp_path/'raw', '--units', 'header')
    manifest = assign(tmp_path/'raw'/'raw.gpkg', tmp_path/'crs', '--layer', 'closed_polygons', '--inspection', tmp_path/'inspect',
                      '--crs', 'EPSG:4526', *CONFIRM)
    assert manifest['crs']['id'] == 'EPSG:4526' and manifest['input']['layer'] == 'closed_polygons'


def linework(path, lines):
    """A raw.gpkg-like `lines` layer: (CAD layer, handle, coordinates) per line, no CRS."""
    frame = gpd.GeoDataFrame({'layer': [l for l, _, _ in lines], 'handle': [h for _, h, _ in lines]},
                             geometry=[LineString(c) for _, _, c in lines])
    with pytest.warns(UserWarning):
        frame.to_file(path, layer='lines')
    return path


def rect(x, y, w, h, gap=0.0):
    """A rectangle as one open line; with `gap`, its last vertex stops `gap` short of the first along the bottom edge."""
    return [(x+gap, y), (x+w, y), (x+w, y+h), (x, y+h), (x, y)] if gap else [(x, y), (x+w, y), (x+w, y+h), (x, y+h), (x, y)]


def polygonize(source, out, *extra, success=True, snap=0.05, max_gap=0.5, min_area=10):
    result = subprocess.run([sys.executable, str(SCRIPTS/'cad.py'), 'polygonize', str(source), '--output', str(out),
                             '--snap', str(snap), '--max-gap', str(max_gap), '--min-area', str(min_area), *map(str, extra)],
                            capture_output=True, text=True, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
    assert (result.returncode == 0) == success, result.stdout+result.stderr
    return json.loads((out/'manifest.json').read_text()) if success else result.stderr


def faces_of(out, name='faces'):
    return gpd.read_file(out/'faces.gpkg', layer=name)


def test_polygonize_closed_rectangles_and_shared_edge(tmp_path):
    source = linework(tmp_path/'raw.gpkg', [('地块', 'A', rect(0, 0, 50, 40)), ('地块', 'B', rect(50, 0, 30, 40)),
                                            ('地块', 'C', rect(200, 0, 20, 20))])
    manifest = polygonize(source, tmp_path/'out')
    faces = faces_of(tmp_path/'out')
    assert manifest['faces'] == {'pass': 3, 'hold': 0, 'outside_boundary': None}
    assert sorted(faces['area_m2']) == [400, 1200, 2000] and faces['valid'].all()
    shared = faces[faces['area_m2'] == 2000].iloc[0]
    assert shared['source_handles'] == 'A,B' and shared['source_layers'] == '地块'  # the shared side belongs to both
    assert manifest['layers']['qa_gaps'] == 0 and manifest['layers']['qa_dangles'] == 0
    assert not any(row['check'] == 'overlaps' for _, row in faces_of(tmp_path/'out', 'qa_topology').iterrows())
    assert manifest['area']['boundary_area_m2'] is None and manifest['notes'][0].startswith('No --boundary')
    assert (tmp_path/'out'/'check.png').stat().st_size > 10_000 and faces.crs is None


def test_polygonize_small_gap_snaps_large_gap_is_reported(tmp_path):
    source = linework(tmp_path/'raw.gpkg', [('地块', 'S', rect(0, 0, 10, 10, gap=0.03)), ('地块', 'L', rect(100, 0, 10, 10, gap=0.3))])
    manifest = polygonize(source, tmp_path/'out')
    assert manifest['snap']['endpoints_merged'] == 2 and manifest['snap']['largest_move_m'] == pytest.approx(0.015)
    faces = faces_of(tmp_path/'out')
    assert len(faces) == 1 and faces['source_handles'].iloc[0] == 'S'
    gap = faces_of(tmp_path/'out', 'qa_gaps')
    assert len(gap) == 1 and gap['kind'].iloc[0] == 'endpoint' and gap['length_m'].iloc[0] == pytest.approx(0.3)
    assert gap['handles_a'].iloc[0] == 'L' and not gap['closed'].iloc[0]
    closed = polygonize(source, tmp_path/'closed', '--close-gaps')
    faces = faces_of(tmp_path/'closed')
    assert len(faces) == 2 and faces.set_index('source_handles').loc['L', 'closed_gap'] and closed['layers']['added_lines'] == 1


def test_polygonize_t_junction_dangle_sliver_and_stray(tmp_path):
    source = linework(tmp_path/'raw.gpkg', [
        ('地块', 'R', rect(0, 0, 40, 20)),
        ('地块', 'T', [(20, 0.02), (20, 20)]),        # stops 0.02 short of the bottom side
        ('地块', 'D', [(40, 10), (60, 10)]),          # sticks out: a dangle
        ('地块', 'P', rect(100, 0, 1, 2)),            # 2 m²: a suspected sliver
        ('地块', 'X', [(300, 0), (300.4, 0)]),        # short stray line, not a gap
    ])
    manifest = polygonize(source, tmp_path/'out')
    assert manifest['snap']['endpoints_moved_to_line'] == 1
    faces = faces_of(tmp_path/'out')
    assert sorted(faces['area_m2']) == [2, 400, 400]
    sliver = faces[faces['area_m2'] == 2].iloc[0]
    assert sliver['status'] == 'hold' and 'sliver' in sliver['reason']
    loose = faces_of(tmp_path/'out', 'qa_dangles')
    assert set(loose['handles']) == {'D', 'X'} and set(loose['kind']) == {'dangle'}
    assert manifest['layers']['qa_gaps'] == 0
    assert any(row['check'] == 'slivers' for _, row in faces_of(tmp_path/'out', 'qa_topology').iterrows())


def test_polygonize_boundary_limits_faces(tmp_path):
    source = linework(tmp_path/'raw.gpkg', [
        ('规划范围线', 'B', rect(0, 0, 100, 50)),
        ('地块', 'A', [(50, 0), (50, 50)]),
        ('地块', 'O', rect(200, 0, 20, 20)),
    ])
    manifest = polygonize(source, tmp_path/'out', '--cad-layers', '地块', '--boundary', '规划范围线')
    assert manifest['faces']['outside_boundary'] == 1 and manifest['parameters']['cad_layers'] == ['地块', '规划范围线']
    assert manifest['area'] == {'faces_area_m2': 5000.0, 'boundary_area_m2': 5000.0, 'share_of_boundary': 1.0}
    assert len(faces_of(tmp_path/'out')) == 2
    open_boundary = linework(tmp_path/'open.gpkg', [('规划范围线', 'B', rect(0, 0, 100, 50, gap=2))])
    assert 'do not close' in polygonize(open_boundary, tmp_path/'bad', '--boundary', '规划范围线', success=False)


def test_polygonize_refusals_and_crs(tmp_path):
    source = linework(tmp_path/'raw.gpkg', [('地块', 'A', rect(0, 0, 10, 10))])
    assert '--max-gap > --snap' in polygonize(source, tmp_path/'a', snap=0.5, max_gap=0.5, success=False)
    assert 'No lines on CAD layers 不存在' in polygonize(source, tmp_path/'b', '--cad-layers', '不存在', success=False)
    projected = tmp_path/'projected.gpkg'
    gpd.read_file(source, layer='lines').set_crs('EPSG:4547').to_file(projected, layer='lines')
    assert polygonize(projected, tmp_path/'c')['source']['crs'] == 'EPSG:4547'
    assert faces_of(tmp_path/'c').crs.to_epsg() == 4547
    lonlat = tmp_path/'lonlat.gpkg'
    gpd.read_file(source, layer='lines').set_crs('EPSG:4326').to_file(lonlat, layer='lines')
    assert 'projected in metres' in polygonize(lonlat, tmp_path/'d', success=False)


def test_extract_then_polygonize(tmp_path):
    source = plan_drawing(tmp_path/'plan.dxf')
    extract(source, tmp_path/'raw', '--units', 'header', '--layers', '地块界线,道路')
    manifest = polygonize(tmp_path/'raw'/'raw.gpkg', tmp_path/'faces')
    faces = faces_of(tmp_path/'faces')
    # 100 x 100 square plus a half disc of radius 50 on its right side, flattened at 0.05 m.
    assert len(faces) == 1 and faces['area_m2'].iloc[0] == pytest.approx(10_000 + math.pi*50**2/2, rel=1e-3)
    assert manifest['layers']['qa_dangles'] == 1  # the road line runs on past the parcel


RULES = r"""
fields:
  parcel_id:
    layers: [地块编号]
    pattern: '\d{2}-\d{2}'
  landuse:
    layers: [用地代码]
    pattern: '(R[1-4]|C\d|M\d|GIC\d|S\d)(\+[A-Z]+\d*)*'
combined:
  - layers: [地块标注]
    pattern: '(?P<parcel_id>\d{2}-\d{2})\s+(?P<landuse>[A-Z]+\d(\+[A-Z]+\d*)*)'
landuse_major: {R: 居住, C: 商业服务业, G: 绿地广场, GIC: 公共管理与服务}
road:
  layers: [道路红线]
  landuse: [S1]
"""


def label_drawing(path):
    """Seven faces and texts covering every labelling case; see test_label_end_to_end for what each one is."""
    ezdxf = pytest.importorskip('ezdxf')
    doc = ezdxf.new('R2018')
    doc.header['$INSUNITS'] = 6
    msp = doc.modelspace()
    def box(x, y, w, h, layer):
        msp.add_lwpolyline([(x, y), (x+w, y), (x+w, y+h), (x, y+h)], close=True, dxfattribs={'layer': layer})
    def text(value, x, y, layer, mtext=False):
        if mtext:
            return msp.add_mtext(value, dxfattribs={'layer': layer, 'insert': (x, y), 'char_height': 2})
        return msp.add_text(value, dxfattribs={'layer': layer, 'insert': (x, y), 'height': 2})
    for x, y in ((0, 0), (50, 0), (0, 40), (50, 40), (0, 80), (50, 80)):   # A B / C D / E F
        box(x, y, 50, 40, '地块界线')
    box(0, 200, 100, 20, '道路红线')                                        # G: a road strip
    text(r'{\fSimSun;01-01}', 20, 30, '地块编号', mtext=True)               # A
    text('R2', 20, 25, '用地代码')
    text('编号待定', 25, 10, '地块编号')                                     # A: unparsed
    text('01-02 GIC1', 70, 30, '地块标注')                                  # B: combined
    text('01-03', 10, 70, '地块编号')                                       # C: two ids
    text('01-09', 30, 70, '地块编号')
    text('C1', 20, 60, '用地代码')
    note = text('01-04', 150, 60, '地块编号', mtext=True)                  # D: by leader, text outside
    leader = msp.add_leader([(75, 60), (120, 60), (150, 60)], dxfattribs={'layer': '标注'})
    leader.dxf.annotation_handle = note.dxf.handle
    text('R2+C1', 70, 50, '用地代码')
    text('01-01', 70, 110, '地块编号')                                      # F: duplicate of A
    text('M1', 70, 100, '用地代码')
    text('09-09', 50, 20, '地块编号')                                       # on the A|B edge
    text('01-05', 300, 300, '地块编号')                                     # outside every face
    text('人民路', 50, 210, '道路名称')                                     # not covered by the rules
    msp.add_leader([(25, 35), (40, 45)], dxfattribs={'layer': '标注'})      # points at nothing extracted
    doc.saveas(path)
    return path


def run_label(faces, texts, rules, out, success=True):
    result = subprocess.run([sys.executable, str(SCRIPTS/'cad.py'), 'label', str(faces), '--texts', str(texts), '--rules', str(rules),
                             '--output', str(out)], capture_output=True, text=True, env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
    assert (result.returncode == 0) == success, result.stdout+result.stderr
    return json.loads((out/'manifest.json').read_text()) if success else result.stderr


def test_label_end_to_end(tmp_path):
    source = label_drawing(tmp_path/'fatu.dxf')
    raw = extract(source, tmp_path/'raw', '--units', 'm')
    assert raw['layers']['leaders'] == 2
    leader_text = layer(tmp_path/'raw', 'texts').set_index('text_clean').loc['01-04']
    assert leader_text['anchor'] == 'leader' and leader_text.geometry.x == 75 and leader_text['arrows'] == 1
    polygonize(tmp_path/'raw'/'raw.gpkg', tmp_path/'faces', '--cad-layers', '地块界线,道路红线')
    rules = tmp_path/'rules.yaml'
    rules.write_text(RULES, encoding='utf-8')
    manifest = run_label(tmp_path/'faces'/'faces.gpkg', tmp_path/'raw'/'raw.gpkg', rules, tmp_path/'out')
    parcels = gpd.read_file(tmp_path/'out'/'parcels.gpkg', layer='parcels')
    at = lambda x, y: parcels[parcels.contains(Point(x, y))].iloc[0]
    a, b, c, d, e, f, g = (at(*p) for p in ((25, 20), (75, 20), (25, 60), (75, 60), (25, 100), (75, 100), (50, 210)))
    assert (a['parcel_id'], a['landuse'], a['landuse_major']) == ('01-01', 'R2', '居住') and 'duplicate' in a['reason']
    assert (b['parcel_id'], b['landuse'], b['landuse_major'], b['status']) == ('01-02', 'GIC1', '公共管理与服务', 'pass')
    assert c['parcel_id'] is None and c['status'] == 'hold' and 'multi-value parcel_id' in c['reason'] and c['landuse_major'] == '商业服务业'
    assert (d['parcel_id'], d['landuse_major'], d['status']) == ('01-04', '居住', 'pass')
    assert e['status'] == 'hold' and e['reason'] == 'unknown use' and not e['road']
    assert f['parcel_id'] == '01-01' and f['landuse_major'] is None and 'duplicate parcel_id' in f['reason']
    assert g['road'] and g['status'] == 'pass'
    assert manifest['conflicts'] == {'multi-value': 2, 'duplicate-id': 2, 'on-boundary': 1, 'unassigned': 1, 'leader-unlinked': 1}
    assert manifest['layers'] == {'parcels': 7, 'label_conflicts': 7, 'unparsed_text': 1}
    assert manifest['ignored_text_layers'] == {'道路名称': 1}
    assert manifest['coverage'] == {'faces': 7, 'road_faces': 1, 'unknown_use': 1, 'non_road_known_use': 5,
                                    'with_parcel_id': 4, 'parcel_id_share': 0.8}
    conflicts = gpd.read_file(tmp_path/'out'/'parcels.gpkg', layer='label_conflicts')
    assert set(conflicts[conflicts['kind'] == 'multi-value']['value']) == {'01-03', '01-09'}
    assert conflicts[conflicts['kind'] == 'on-boundary']['value'].tolist() == ['09-09']
    assert (tmp_path/'out'/'rules.yaml').read_text(encoding='utf-8') == RULES
    assert (tmp_path/'out'/'check.png').stat().st_size > 10_000


def test_label_rule_validation(tmp_path):
    import _cad_label as L
    for text, message in (("fields: {face_id: {layers: [a], pattern: 'x'}}", 'reserved'),
                          ("fields: {parcel_id: {pattern: 'x'}}", 'layers and/or tags'),
                          ("combined: [{layers: [a], pattern: 'x'}]", 'named groups'),
                          ("fields: {parcel_id: {layers: [a], pattern: '('}}", 'bad pattern'),
                          ("fields: {parcel_id: {layers: [a], pattern: 'x'}}\nlanduse_major: {R: 居住}", 'needs a landuse field'),
                          ("colours: {}", 'Unknown rule sections')):
        with pytest.raises(ValueError, match=message):
            L.Rules(__import__('yaml').safe_load(text))
    rules = L.Rules(__import__('yaml').safe_load(RULES))
    assert rules.major('GIC1') == '公共管理与服务' and rules.major('G1') == '绿地广场' and rules.major('R2+C1') == '居住'
    assert rules.classify('地块编号', None, '01-01') == (True, {'parcel_id': '01-01'})
    assert rules.classify('地块编号', None, '01-01x') == (True, {})          # full match only
    assert rules.classify('道路名称', None, '01-01') == (False, {})


def test_inspect_keeps_large_blocks_and_skips_polyface_meshes(tmp_path):
    ezdxf = pytest.importorskip('ezdxf')
    doc = ezdxf.new('R2000')
    block = doc.blocks.new('区域位置图')
    for i in range(10_050):  # GDAL stops at 10000 features per block unless told otherwise
        block.add_line((i, 0), (i, 1))
    msp = doc.modelspace()
    msp.add_blockref('区域位置图', (39_500_000, 3_400_000), dxfattribs={'layer': '图框'})
    mesh = msp.add_polyface(dxfattribs={'layer': '面'})
    mesh.append_face([(39_500_000, 3_400_000, 0), (39_500_001, 3_400_000, 0), (39_500_001, 3_400_001, 1)])
    source = tmp_path/'big.dxf'
    doc.saveas(source)
    report = inspect(source, tmp_path/'out')
    assert report['entities']['geometries'] == 1  # GDAL merges one block reference into one geometry collection
    import cad
    frame, unreadable, _ = cad.read_gdal(source)
    parts = shapely.get_num_geometries(frame.geometry.to_numpy())
    assert parts.max() == 10_050 and unreadable == 1  # nothing cut at 10000
    assert any('1 polyface meshes' in w for w in report['warnings'])
    assert {row['layer']: row['dxf_types'] for row in report['layers']} == {'图框': {'INSERT': 1}, '面': {'POLYLINE polyface': 1}}


def test_inspect_reads_libredwg_xdata_in_xrecords(tmp_path):
    ezdxf = pytest.importorskip('ezdxf')
    doc = ezdxf.new('R2018')
    doc.modelspace().add_line((0, 0), (10, 10), dxfattribs={'layer': '地块界线'})
    doc.objects.add_xrecord(doc.rootdict.dxf.handle)  # empty, like the 湘源 reactor records in the Mawan drawing
    source = tmp_path/'libredwg.dxf'
    doc.saveas(source)
    text = source.read_text(encoding='utf-8')
    marker = 'AcDbXrecord\n280\n1\n'
    assert marker in text
    # LibreDWG writes pointers (330) after an application name inside XRECORDs; ezdxf refuses them.
    source.write_text(text.replace(marker, marker + '1001\nHYGggl\n330\n2E\n', 1), encoding='utf-8')
    from ezdxf import recover
    with pytest.raises(Exception, match='XDATA'):
        recover.readfile(source)
    report = inspect(source, tmp_path/'out')
    assert report['reader']['inventory'].startswith('ezdxf') and report['layers'][0]['dxf_types'] == {'LINE': 1}
    assert any('1 malformed XDATA tags' in w for w in report['warnings'])


def fake_dwg2dxf(folder, body):
    """A dwg2dxf stand-in, alone on PATH: the converter adapter is tested without LibreDWG or ODA installed."""
    folder.mkdir()
    script = folder/'dwg2dxf'
    script.write_text(f'#!{sys.executable}\nimport shutil, sys\nargs = sys.argv\n' + body, encoding='utf-8')
    script.chmod(0o755)
    return folder


def run_convert(source, out, *extra, path, success=True):
    result = subprocess.run([sys.executable, str(SCRIPTS/'cad.py'), 'convert', str(source), '--output', str(out), *map(str, extra)],
                            capture_output=True, text=True, env={**os.environ, 'PATH': str(path), 'PYTHONDONTWRITEBYTECODE': '1'})
    assert (result.returncode == 0) == success, result.stdout+result.stderr
    return json.loads((out/'manifest.json').read_text()) if success else result.stderr


def test_convert_runs_the_converter_on_a_copy_and_reads_back(tmp_path):
    prepared = dxf(tmp_path/'prepared.dxf', site(39_500_000, 3_400_000))
    tool = fake_dwg2dxf(tmp_path/'bin', f"print('Warning: Unknown object, skipping eed/reactors/xdic', file=sys.stderr)\n"
                                        f"shutil.copy({str(prepared)!r}, args[args.index('-o') + 1])\n")
    source = tmp_path/'plan.dwg'
    source.write_bytes(b'AC1032' + bytes(200))
    before = source.read_bytes()
    manifest = run_convert(source, tmp_path/'out', '--version', 'r2013', path=tool)
    assert manifest['source']['release'] == 'R2018' and manifest['converter']['tool'] == 'libredwg'
    command = manifest['converter']['command']
    assert command[:3] == ['dwg2dxf', '-y', '--as'] and command[3] == 'r2013' and command[-1] != str(source)
    assert manifest['readback']['model_space_entities'] == 5 and manifest['readback']['layers_with_entities'] == 4
    assert manifest['readback']['header']['codepage'] == 'ANSI_936'
    assert manifest['warnings'] == ['Warning: Unknown object, skipping eed/reactors/xdic']
    assert (tmp_path/'out'/'plan.dxf').read_bytes() == prepared.read_bytes() and source.read_bytes() == before


def test_convert_refusals(tmp_path):
    source = tmp_path/'plan.dwg'
    source.write_bytes(b'AC1032' + bytes(200))
    empty = tmp_path/'empty'
    empty.mkdir()
    assert 'No ODA File Converter or LibreDWG' in run_convert(source, tmp_path/'a', path=empty, success=False)
    failing = fake_dwg2dxf(tmp_path/'bin', "print('Error: bad DWG section', file=sys.stderr)\nsys.exit(1)\n")
    message = run_convert(source, tmp_path/'b', path=failing, success=False)
    assert 'libredwg failed (exit 1)' in message and 'bad DWG section' in message
    assert 'No oda found' in run_convert(source, tmp_path/'c', '--tool', 'oda', path=failing, success=False)
    fake = tmp_path/'fake.dwg'
    fake.write_bytes(b'not a drawing')
    assert 'does not start like a DWG' in run_convert(fake, tmp_path/'d', path=failing, success=False)
    assert not any((tmp_path/name).exists() for name in 'abcd')


@pytest.mark.skipif(shutil.which('dwg2dxf') is None or shutil.which('dxf2dwg') is None, reason='LibreDWG not installed')
def test_convert_with_libredwg(tmp_path):
    ezdxf = pytest.importorskip('ezdxf')
    doc = ezdxf.new('R2000')
    doc.modelspace().add_line((0, 0), (10, 10))
    doc.saveas(tmp_path/'seed.dxf')
    subprocess.run(['dxf2dwg', '-y', '-o', str(tmp_path/'seed.dwg'), str(tmp_path/'seed.dxf')], capture_output=True, check=True)
    manifest = run_convert(tmp_path/'seed.dwg', tmp_path/'out', path=os.environ['PATH'])
    # LibreDWG's dxf2dwg is experimental, so only the conversion itself is checked here, not its content.
    assert manifest['converter']['tool'] in ('libredwg', 'oda') and manifest['source']['release'] == 'R2000'
    assert manifest['readback']['header']['acadver'] and (tmp_path/'out'/'seed.dxf').stat().st_size > 0
