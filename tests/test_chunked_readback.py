"""Chunked readback helpers must match a full read exactly."""
import hashlib
import math
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest
import rasterio as rio
from rasterio.transform import from_origin
from shapely.geometry import Point

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from _safe_io import iter_vector_chunks
from raster import band_matches
import _raster_numeric as n


@pytest.fixture
def layer(tmp_path):
    path = tmp_path/'points.gpkg'
    frame = gpd.GeoDataFrame({'name': [f'p{i}' for i in range(7)], 'value': np.arange(7.)},
                             geometry=[Point(i, -i) for i in range(7)], crs='EPSG:3857')
    frame.to_file(path, layer='points', engine='pyogrio')
    gpd.GeoDataFrame({'name': []}, geometry=[], crs='EPSG:4326').to_file(path, layer='empty', engine='pyogrio')
    return path


@pytest.mark.parametrize('size', [1, 3, 7, 100])
def test_vector_chunks_reassemble_full_read(layer, size):
    full = gpd.read_file(layer, layer='points', engine='pyogrio')
    chunks = list(iter_vector_chunks(layer, layer='points', chunk_size=size))
    assert all(len(frame) <= size for _, frame in chunks)
    assert [start for start, _ in chunks] == list(range(0, size*len(chunks), size))[:len(chunks)]
    for start, frame in chunks:
        part = full.iloc[start:start+len(frame)]
        assert frame.index.equals(part.index) and frame.equals(part) and frame.crs == full.crs
    assert sum(len(frame) for _, frame in chunks) == len(full)


def test_vector_chunks_yield_empty_layer_once(layer):
    chunks = list(iter_vector_chunks(layer, layer='empty'))
    assert len(chunks) == 1 and chunks[0][0] == 0 and chunks[0][1].empty
    assert chunks[0][1].crs == 'EPSG:4326' and 'name' in chunks[0][1].columns


def test_vector_chunks_reject_nonpositive_size(layer):
    with pytest.raises(ValueError):
        next(iter_vector_chunks(layer, chunk_size=0))


@pytest.fixture
def grid(tmp_path):
    rng = np.random.default_rng(7)
    data = rng.normal(size=(2, 37, 23))
    data[0, 5, 5] = np.nan
    data[1, 30:, :4] = -9999.
    path = tmp_path/'grid.tif'
    with rio.open(path, 'w', driver='GTiff', width=23, height=37, count=2, dtype='float64', nodata=-9999.,
                  crs='EPSG:3857', transform=from_origin(0, 37, 1, 1)) as ds:
        ds.write(data)
    return path, data


@pytest.mark.parametrize('size', [1, 5, 1024])
def test_band_matches_detects_single_cell_change(grid, size):
    path, data = grid
    with rio.open(path) as ds:
        assert band_matches(ds, 1, data[0], size=size)
        assert not band_matches(ds, 1, data[0], equal_nan=False, size=size)
        changed = data[0].copy(); changed[36, 22] += 1
        assert not band_matches(ds, 1, changed, size=size)
        assert not band_matches(ds, 1, data[0][:-1], size=size)


@pytest.mark.parametrize('pixels', [1, 23, 50, 10**6])
def test_strip_digest_and_fsum_match_full_read(grid, pixels):
    path, _ = grid
    with rio.open(path) as ds:
        full = hashlib.sha256(ds.read().astype('<f8').tobytes()).hexdigest()
        strips = hashlib.sha256()
        for band, strip in n.row_strips(ds, pixels):
            strips.update(ds.read(band, window=strip).astype('<f8').tobytes())
        assert strips.hexdigest() == full
        assert math.isnan(n.band_fsum(ds, 1, pixels))  # unmasked NaN propagates as in a full read
        assert n.band_fsum(ds, 2, pixels) == math.fsum(ds.read(2, masked=True).compressed())
