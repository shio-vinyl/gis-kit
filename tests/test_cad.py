"""cad.py inspect: header, layer inventory, outliers and candidate CRS on hand-written R12 DXF files."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import geopandas as gpd
import pyogrio
import pytest
from shapely.geometry import LineString, Point, Polygon

SCRIPTS = Path(__file__).resolve().parents[1]/'scripts'


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
                                'codepage': 'ANSI_936', 'extent': None}
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
    garbled = inspect(source, tmp_path/'garbled')
    assert any('--encoding CP936' in w for w in garbled['warnings']) and '道路' not in {r['layer'] for r in garbled['layers']}
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
    assert {row['name']: row['viewports'] for row in report['layouts']}['图则'] == 1
    assert report['header']['release'] == 'R2018' and codes(report)[0] == 'EPSG:4526'


def test_refuses_dwg_and_other_inputs(tmp_path):
    (tmp_path/'plan.dwg').write_bytes(b'AC1032')
    assert 'save it as DXF' in inspect(tmp_path/'plan.dwg', tmp_path/'a', success=False)
    assert '.dxf' in inspect(tmp_path/'missing.dxf', tmp_path/'b', success=False)
    assert not (tmp_path/'a').exists() and not (tmp_path/'b').exists()
