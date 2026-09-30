"""Analytical values, display edges, machine output and failed publication."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import geopandas as gpd
import numpy as np
from PIL import Image
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('raster_cli', SCRIPTS / 'raster.py')
raster = importlib.util.module_from_spec(spec)
spec.loader.exec_module(raster)
from _delivery import bundle


def fixture(tmp_path, crs='EPSG:3857', scale=1):
    source = tmp_path / 'two bands.tif'
    with rasterio.open(source, 'w', driver='GTiff', width=3, height=2, count=2, dtype='float64',
                       crs=crs, transform=from_origin(0, 200, 100, 100), nodata=-9999) as ds:
        ds.write(np.full((2, 3), 999.), 1)
        ds.write(np.array([[0., 10., 20.], [-9999, 9., 19.]]), 2)
        ds.scales = (1, scale)
    return source


def params(**extra):
    return dict(band=2, breaks=[10, 20], colors=['#112233', '#445566', '#778899'], unit='m', status='hold', **extra)


def test_display_band_boundaries_zero_nodata_and_readback(tmp_path):
    source = fixture(tmp_path)
    before = source.read_bytes()
    result = raster.process('display', [source], params(), tmp_path)
    assert result['class_pixels'] == [2, 2, 1]
    assert result['nodata_pixels'] == 1 and result['status'] == 'hold'
    a = np.asarray(Image.open(tmp_path / 'display.png'))
    assert a[0, 0].tolist() == [17, 34, 51, 255]
    assert a[0, 1].tolist() == [68, 85, 102, 255]
    assert a[0, 2].tolist() == [119, 136, 153, 255]
    assert a[1, 0, 3] == 0
    assert result['coordinates'][0][1] > result['coordinates'][3][1]
    assert source.read_bytes() == before


@pytest.mark.parametrize('update', [{'band': 3}, {'breaks': [20, 10]}, {'colors': ['red']}, {'unit': ''}, {'status': 'auto'}, {'max_pixels': 2}])
def test_display_rejects_and_does_not_publish(tmp_path, update):
    source = fixture(tmp_path)
    p = params(); p.update(update)
    settings = tmp_path / 'params.json'; settings.write_text(json.dumps(p))
    output = tmp_path / 'published'
    from argparse import Namespace
    with pytest.raises(ValueError):
        raster.execute(Namespace(operation='display', input=[str(source)], params=str(settings), output=str(output)))
    assert not output.exists()
    assert not list(tmp_path.glob('.raster-*'))


@pytest.mark.parametrize('crs,scale', [('EPSG:4326', 1), ('EPSG:3857', 2)])
def test_display_rejects_unwarped_or_uncalibrated(tmp_path, crs, scale):
    with pytest.raises(ValueError):
        raster.process('display', [fixture(tmp_path, crs, scale)], params(), tmp_path)


def test_injected_png_write_failure_never_publishes(tmp_path, monkeypatch):
    source = fixture(tmp_path)
    settings = tmp_path / 'params.json'; settings.write_text(json.dumps(params()))
    def broken(self, path, *a, **kw):
        Path(path).write_bytes(b'partial')
        raise OSError('injected partial write')
    monkeypatch.setattr(Image.Image, 'save', broken)
    from argparse import Namespace
    with pytest.raises(OSError, match='injected'):
        raster.execute(Namespace(operation='display', input=[str(source)], params=str(settings), output=str(tmp_path / 'published')))
    assert not (tmp_path / 'published').exists()
    assert not list(tmp_path.glob('.raster-*'))


def test_complete_bundle_failure_preserves_source(tmp_path):
    source = fixture(tmp_path); before = source.read_bytes()
    with pytest.raises(OSError):
        with bundle(tmp_path / 'delivery') as stage:
            (stage / 'analysis.json').write_text('{}')
            raise OSError('render failed after analysis')
    assert not (tmp_path / 'delivery').exists()
    assert not list(tmp_path.glob('.delivery-*'))
    assert source.read_bytes() == before


def test_machine_inspection_and_stats_cli(tmp_path):
    data = tmp_path / 'layers.gpkg'
    gpd.GeoDataFrame({'value': [0., None], 'status': ['candidate','hold']}, geometry=[box(0,0,1,1),box(1,0,2,1)], crs=3857).to_file(data, layer='wanted')
    gpd.GeoDataFrame({'other': [99]}, geometry=[box(0,0,3,3)], crs=3857).to_file(data, layer='other')
    def call(script, args):
        return subprocess.run([sys.executable,str(SCRIPTS/script),*map(str,args)],capture_output=True,text=True)
    r = call('inspect-data.py',[data,'--layer','wanted','summary','--json']); assert r.returncode == 0, r.stderr
    v = json.loads(r.stdout); assert v['features'] == 2 and v['numeric']['value']['count'] == 1
    r = call('inspect-data.py',[data,'layers','--json']); assert r.returncode == 0, r.stderr
    assert len(json.loads(r.stdout)) == 2
    r = call('stats.py',['describe',data,'--layer','wanted','--json']); assert r.returncode == 0,r.stderr
    assert json.loads(r.stdout)[0]['mean'] == 0
    r = call('inspect-data.py',[data,'--layer','missing','summary','--json'])
    assert r.returncode != 0 and r.stdout == '' and 'not found' in r.stderr


def test_empty_layer_inventory_is_still_json(tmp_path, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location('inspect_cli', SCRIPTS / 'inspect-data.py')
    inspector = importlib.util.module_from_spec(spec); spec.loader.exec_module(inspector)
    path = tmp_path / 'empty.gpkg'; path.touch()
    monkeypatch.setattr(inspector, 'list_layers', lambda path: [])
    from argparse import Namespace
    inspector.cmd_layers(Namespace(file=str(path), json=True))
    assert json.loads(capsys.readouterr().out) == []
