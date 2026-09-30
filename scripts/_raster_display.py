"""Classified Web Mercator display only; analytical rasters remain untouched."""
import hashlib
import math
import re

import numpy as np
from PIL import Image
from rasterio.warp import transform


def display(ds, values, parameters, stage):
    if ds.crs.to_epsg() != 3857:
        raise ValueError('Display requires EPSG:3857; warp explicitly before coloring')
    breaks = parameters['breaks']
    colors = parameters['colors']
    if not isinstance(breaks, list) or any(isinstance(x, bool) or not isinstance(x, (float, int)) or not math.isfinite(x) for x in breaks):
        raise ValueError('breaks must be finite numbers')
    if any(a >= b for a, b in zip(breaks, breaks[1:])):
        raise ValueError('breaks must be strictly increasing')
    if not isinstance(colors, list) or len(colors) != len(breaks) + 1 or any(not isinstance(c, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', c) for c in colors):
        raise ValueError('colors must contain one #RRGGBB per interval')
    if not isinstance(parameters.get('unit'), str) or not parameters['unit'].strip():
        raise ValueError('Explicit unit required (use dimensionless when appropriate)')
    if parameters.get('status') not in ('candidate', 'hold', 'accepted'):
        raise ValueError('Explicit candidate/hold/accepted status required; display does not grant acceptance')
    bound = 20037508.342789244
    if any(abs(v) > bound + 1e-7 for v in ds.bounds):
        raise ValueError('Display outside Web Mercator world bounds')
    valid = ~np.ma.getmaskarray(values)
    indices = np.searchsorted(breaks, values.data, side='right')
    rgba = np.zeros((*values.shape, 4), dtype='uint8')
    counts = []
    for i, color in enumerate(colors):
        selected = valid & (indices == i)
        rgba[selected] = [int(color[j:j+2], 16) for j in (1, 3, 5)] + [255]
        counts.append(int(selected.sum()))
    target = stage / 'display.png'
    Image.fromarray(rgba).save(target)
    with Image.open(target) as decoded:
        if not np.array_equal(np.asarray(decoded.convert('RGBA')), rgba):
            raise ValueError('Display PNG readback differs')
    left, bottom, right, top = ds.bounds
    xs, ys = transform(ds.crs, 'EPSG:4326', [left, right, right, left], [top, top, bottom, bottom])
    return {'artifact': 'display.png', 'artifact_sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
            'coordinates': [list(p) for p in zip(xs, ys)], 'unit': parameters['unit'],
            'status': parameters['status'], 'breaks': breaks, 'colors': colors,
            'interval_rule': 'lower inclusive, upper exclusive; first/last unbounded',
            'class_pixels': counts, 'valid_pixels': int(valid.sum()),
            'nodata_pixels': int((~valid).sum()), 'nodata_display': 'transparent; unknown, not zero',
            'display_crs': 'EPSG:3857', 'resampling': 'none; source grid retained'}
