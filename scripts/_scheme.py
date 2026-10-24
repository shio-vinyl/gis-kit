"""Class schemes: what each display class means, shared by vector and raster display.

A scheme carries semantics only (codes, labels, groups, breaks, counts, unit, status);
colours belong to the map renderer. Contract `class-scheme` v1, see references/display-contracts.md.
"""
import math

import numpy as np

CONTRACT = 'class-scheme'
SCHEMA_VERSION = 1
STATUSES = ('candidate', 'hold', 'accepted')
METHODS = ('manual', 'quantile', 'equal_interval')
MAX_CLASSES = 65536


def _finite(x):
    return not isinstance(x, bool) and isinstance(x, (int, float)) and math.isfinite(x)


def _fmt(x):
    return f'{x:,.0f}' if float(x).is_integer() else f'{x:,.6g}'


def _unit(p):
    unit = p.get('unit')
    if not isinstance(unit, str) or not unit.strip():
        raise ValueError('Explicit unit required (use dimensionless when appropriate)')
    return unit


def _status(p):
    if p.get('status') not in STATUSES:
        raise ValueError('Explicit candidate/hold/accepted status required; a scheme does not grant acceptance')
    return p['status']


def numeric_breaks(values, p):
    """Breaks from explicit values, or computed over the valid values with a recorded method."""
    method = p.get('method', 'manual')
    if method not in METHODS:
        raise ValueError(f'method must be one of {", ".join(METHODS)}')
    if method == 'manual':
        breaks = p.get('breaks')
        if not isinstance(breaks, list) or not breaks or not all(_finite(x) for x in breaks):
            raise ValueError('breaks must be finite numbers')
    else:
        k = p.get('classes')
        if isinstance(k, bool) or not isinstance(k, int) or not 2 <= k <= 64:
            raise ValueError('classes must be an integer from 2 to 64')
        if not values.size:
            raise ValueError(f'{method} needs at least one valid value')
        if method == 'quantile':
            breaks = [float(x) for x in np.quantile(values, [i / k for i in range(1, k)], method='linear')]
        else:
            lo, hi = float(values.min()), float(values.max())
            if lo == hi:
                raise ValueError('equal_interval needs more than one distinct value')
            breaks = [lo + (hi - lo) * i / k for i in range(1, k)]
    if any(a >= b for a, b in zip(breaks, breaks[1:])):
        raise ValueError(f'breaks must be strictly increasing (got {breaks}); use manual breaks when {method} classes collapse')
    return breaks, method


def _labels(p, defaults):
    labels = p.get('labels')
    if labels is None:
        return defaults
    if not isinstance(labels, list) or len(labels) != len(defaults) or not all(isinstance(x, str) and x for x in labels):
        raise ValueError(f'labels must list {len(defaults)} non-empty strings')
    return labels


def build(values, p, field=None):
    """Scheme for 1-D valid values (missing already removed); `values` is a numpy array."""
    unit, status = _unit(p), _status(p)
    values = np.asarray(values)
    scheme = {'contract': CONTRACT, 'schema_version': SCHEMA_VERSION, 'field': field, 'unit': unit, 'status': status,
              'nodata': {'label': p.get('nodata_label', 'NoData / unknown'), 'meaning': 'missing or non-finite; never zero'}}
    if 'categories' in p:
        categories = p['categories']
        if categories == 'unique':
            codes = sorted({x.item() if hasattr(x, 'item') else x for x in values})
            categories = [{'code': c} for c in codes]
        if not isinstance(categories, list) or not categories or len(categories) > MAX_CLASSES:
            raise ValueError(f'categories must be "unique" or a list of 1..{MAX_CLASSES} entries')
        classes = []
        for i, item in enumerate(categories):
            item = item if isinstance(item, dict) else {'code': item}
            code = item.get('code')
            if not (_finite(code) or isinstance(code, str)):
                raise ValueError('Each category needs a numeric or string code')
            entry = {'id': i, 'code': code, 'label': str(item.get('label', code))}
            for key in ('label_en', 'group'):
                if item.get(key) is not None:
                    entry[key] = str(item[key])
            classes.append(entry)
        codes = [c['code'] for c in classes]
        if len(set(map(repr, codes))) != len(codes):
            raise ValueError('Category codes must be unique')
        counts = [int(np.sum(values == c['code'])) for c in classes]
        unlisted = int(values.size - sum(counts))
        if unlisted and not p.get('allow_unlisted', False):
            raise ValueError(f'{unlisted} valid values have no listed category; list them or set allow_unlisted')
        scheme.update(kind='categorical', method='categories', classes=classes, counts=counts, unlisted=unlisted)
        return scheme
    if p.get('kind') == 'continuous':
        if not values.size:
            raise ValueError('continuous scheme needs at least one valid value')
        domain = [float(values.min()), float(values.max())]
        if 'domain' in p:
            domain = p['domain']
            if not isinstance(domain, list) or len(domain) != 2 or not all(map(_finite, domain)) or domain[0] >= domain[1]:
                raise ValueError('domain must be [min, max] with min < max')
        scheme.update(kind='continuous', method='domain', domain=domain, classes=[], counts=[])
        return scheme
    kind = p.get('kind', 'sequential')
    if kind not in ('sequential', 'diverging'):
        raise ValueError('kind must be sequential, diverging, continuous, or omitted with categories')
    breaks, method = numeric_breaks(values, p)
    suffix = '' if unit == 'dimensionless' else f' {unit}'
    defaults = [f'< {_fmt(breaks[0])}{suffix}'] + [f'{_fmt(a)}–<{_fmt(b)}{suffix}' for a, b in zip(breaks, breaks[1:])] + [f'≥ {_fmt(breaks[-1])}{suffix}']
    labels = _labels(p, defaults)
    edges = [None, *breaks, None]
    classes = [{'id': i, 'min': edges[i], 'max': edges[i + 1], 'label': labels[i]} for i in range(len(breaks) + 1)]
    indices = np.searchsorted(breaks, values, side='right')
    counts = [int(np.sum(indices == i)) for i in range(len(classes))]
    scheme.update(kind=kind, method=method, breaks=breaks, interval_rule='lower inclusive, upper exclusive; first/last unbounded',
                  classes=classes, counts=counts, domain=[float(values.min()), float(values.max())] if values.size else None)
    return scheme


def class_index(values, scheme):
    """Class id for each value of a numpy array (values must be valid); -1 for an unlisted category."""
    if scheme['kind'] == 'categorical':
        out = np.full(values.shape, -1, dtype='int64')
        for c in scheme['classes']:
            out[values == c['code']] = c['id']
        return out
    if scheme['kind'] == 'continuous':
        raise ValueError('A continuous scheme has no classes; use value-rgb24 encoding')
    return np.searchsorted(scheme['breaks'], values, side='right').astype('int64')
