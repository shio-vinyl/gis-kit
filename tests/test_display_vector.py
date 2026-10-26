"""Display vector at one view's scale, from a small synthetic coverage."""
import json
import subprocess
import sys
from pathlib import Path

import geopandas as gpd
import pytest
from shapely.geometry import box, shape

SCRIPT = Path(__file__).resolve().parents[1]/'scripts'/'display-vector.py'
# Zoom 3 with 512-px tiles: about 11.4 px per degree near the equator.
VIEW = {'center': [12, 5], 'zoom': 3, 'width': 600, 'height': 300}


@pytest.fixture
def units(tmp_path):
    """Two neighbours sharing x = 10, an island and an islet far below one pixel."""
    path = tmp_path/'units.gpkg'
    gpd.GeoDataFrame({'code': ['A', 'B', 'C', 'D'], 'name': ['Alpha', 'Beta', 'Isle', 'Rock']},
                     geometry=[box(0, 0, 10, 10), box(10, 0, 20, 10), box(22, 2, 24, 4), box(25, 5, 25.01, 5.01)],
                     crs='EPSG:4326').to_file(path, layer='units')
    return path


def run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True)


def read(directory, name):
    return json.loads((directory/name).read_text())


def test_areas_boundaries_coast_and_manifest(units, tmp_path):
    out = tmp_path/'display'
    result = run('--source', units, '--layer', 'units', '--id-field', 'code', '--fields', 'name', '--view', json.dumps(VIEW), '--output', out)
    assert result.returncode == 0, result.stderr
    manifest = read(out, 'display-vector.json')
    assert manifest['contract'] == 'display-vector' and manifest['schema_version'] == 1
    assert manifest['view']['zoom'] == 3 and manifest['view']['tileSize'] == 512
    assert manifest['counts']['units_dropped'] == ['D'] and 'D' in manifest['warnings'][0]
    areas = read(out, 'areas.geojson')['features']
    assert [(f['properties']['code'], f['properties']['name']) for f in areas] == [('A', 'Alpha'), ('B', 'Beta'), ('C', 'Isle')]
    boundaries = read(out, 'boundaries.geojson')['features']
    assert [(f['properties']['a'], f['properties']['b']) for f in boundaries] == [('A', 'B')]
    shared = shape(boundaries[0]['geometry'])
    # Coordinates sit on a 1/20-pixel grid (about 0.004° here); the boundary is the shared edge, without spurs along the coast.
    assert shared.bounds[0] == pytest.approx(10, abs=0.005) and shared.bounds[2] == pytest.approx(10, abs=0.005) and shared.length == pytest.approx(10, abs=0.2)
    coast = [shape(f['geometry']) for f in read(out, 'coast.geojson')['features']]
    # The coast runs round the union of A and B and round the island, never along the shared edge.
    assert sum(line.length for line in coast) == pytest.approx(60 + 8, abs=0.5)
    assert not any(line.intersection(box(9.99, 0.5, 10.01, 9.5)).length > 0 for line in coast)
    record = read(out, 'record.json')
    assert record['status'] == 'complete' and record['cache']['status'] == 'off'
    assert record['files']['areas']['sha256'] == manifest['files']['areas']['sha256']


def test_repeatable_bytes_cache_and_frame(units, tmp_path):
    common = ['--source', units, '--layer', 'units', '--id-field', 'code', '--view', json.dumps(VIEW), '--cache', tmp_path/'cache']
    first, second, third = tmp_path/'one', tmp_path/'two', tmp_path/'three'
    assert run(*common, '--output', first).returncode == 0
    assert run(*common, '--output', second).returncode == 0
    assert run('--source', units, '--layer', 'units', '--id-field', 'code', '--view', json.dumps(VIEW), '--output', third).returncode == 0
    for name in ('areas', 'boundaries', 'coast'):
        assert (first/f'{name}.geojson').read_bytes() == (second/f'{name}.geojson').read_bytes() == (third/f'{name}.geojson').read_bytes()
    assert read(first, 'record.json')['cache']['status'] == 'miss' and read(second, 'record.json')['cache']['status'] == 'hit'
    assert read(second, 'display-vector.json')['warnings'] == read(first, 'display-vector.json')['warnings']
    # A view cutting through A: the clipping frame is not coastline.
    narrow = tmp_path/'narrow'
    assert run('--source', units, '--layer', 'units', '--id-field', 'code', '--view', json.dumps({**VIEW, 'center': [10, 5], 'width': 120}),
               '--margin', '0', '--output', narrow).returncode == 0
    frame = read(narrow, 'display-vector.json')['view']['bbox']
    edges = box(frame[0], frame[1], frame[0] + 0.05, frame[3]).union(box(frame[2] - 0.05, frame[1], frame[2], frame[3]))
    coast = [shape(f['geometry']) for f in read(narrow, 'coast.geojson')['features']]
    # Coast lines may cross the frame (A's top and bottom), never run along it.
    assert coast and all(line.intersection(edges).length < 0.2 for line in coast)


def test_refusals(units, tmp_path):
    bad_view = run('--source', units, '--layer', 'units', '--id-field', 'code', '--view', '{"center": [0, 0], "zoom": 3}', '--output', tmp_path/'a')
    assert bad_view.returncode == 1 and 'width and height' in bad_view.stderr
    missing = run('--source', units, '--layer', 'units', '--id-field', 'nope', '--view', json.dumps(VIEW), '--output', tmp_path/'b')
    assert missing.returncode == 1 and 'nope' in missing.stderr
    existing = tmp_path/'c'
    existing.mkdir()
    assert run('--source', units, '--layer', 'units', '--id-field', 'code', '--view', json.dumps(VIEW), '--output', existing).returncode == 1


def test_keep_required_small_units(units, tmp_path):
    out = tmp_path/'kept'
    assert run('--source', units, '--layer', 'units', '--id-field', 'code', '--view', json.dumps(VIEW), '--keep', 'D', '--output', out).returncode == 0
    manifest = read(out, 'display-vector.json')
    assert manifest['counts']['units_dropped'] == [] and manifest['counts']['kept_small'] == ['D']
    assert [f['properties']['code'] for f in read(out, 'areas.geojson')['features']] == ['A', 'B', 'C', 'D']
