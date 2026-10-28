"""Label rules for cad.py label: which CAD texts give which parcel fields, land-use classes and road faces."""
from __future__ import annotations

import re
from pathlib import Path

# Output columns of the parcels layer that a rule field must not shadow.
RESERVED = {'face_id', 'landuse_major', 'road', 'status', 'reason', 'area_m2', 'valid', 'closed_gap',
            'source_handles', 'source_layers', 'label_handles', 'geometry'}


class Rule:
    def __init__(self, where, spec, field=None):
        unknown = set(spec) - {'layers', 'tags', 'pattern'}
        if unknown:
            raise ValueError(f'{where}: unknown keys {", ".join(sorted(unknown))}')
        self.layers = set(spec.get('layers') or [])
        self.tags = set(spec.get('tags') or [])
        if not self.layers and not self.tags:
            raise ValueError(f'{where}: give layers and/or tags')
        if not isinstance(spec.get('pattern'), str):
            raise ValueError(f'{where}: pattern must be a string')
        try:
            self.pattern = re.compile(spec['pattern'])
        except re.error as error:
            raise ValueError(f'{where}: bad pattern ({error})') from error
        self.field = field
        self.groups = list(self.pattern.groupindex)
        if field is None and not self.groups:
            raise ValueError(f'{where}: a combined pattern needs named groups such as (?P<parcel_id>...)')
        for name in ([field] if field else self.groups):
            if name in RESERVED:
                raise ValueError(f'{where}: field name {name} is reserved')

    def applies(self, layer, tag):
        return layer in self.layers or (tag is not None and tag in self.tags)

    def match(self, text):
        found = self.pattern.fullmatch(text or '')
        if not found:
            return None
        if self.field:
            return {self.field: found.group(0)}
        return {k: v for k, v in found.groupdict().items() if v is not None}


class Rules:
    def __init__(self, data):
        if not isinstance(data, dict):
            raise ValueError('Rules must be a mapping')
        unknown = set(data) - {'fields', 'combined', 'landuse_major', 'road'}
        if unknown:
            raise ValueError(f'Unknown rule sections: {", ".join(sorted(unknown))}')
        self.rules = [Rule(f'fields.{name}', spec or {}, name) for name, spec in (data.get('fields') or {}).items()]
        self.rules += [Rule(f'combined[{i}]', spec or {}) for i, spec in enumerate(data.get('combined') or [])]
        if not self.rules:
            raise ValueError('Rules define no fields')
        self.fields = list(dict.fromkeys(name for rule in self.rules for name in ([rule.field] if rule.field else rule.groups)))
        self.majors = {str(k): str(v) for k, v in (data.get('landuse_major') or {}).items()}
        if self.majors and 'landuse' not in self.fields:
            raise ValueError('landuse_major needs a landuse field')
        road = data.get('road') or {}
        unknown = set(road) - {'layers', 'landuse'}
        if unknown:
            raise ValueError(f'road: unknown keys {", ".join(sorted(unknown))}')
        self.road_layers = set(road.get('layers') or [])
        self.road_codes = set(road.get('landuse') or [])

    def classify(self, layer, tag, text):
        """(relevant, {field: value}) for one text: relevant when some rule covers its layer or tag."""
        relevant = [rule for rule in self.rules if rule.applies(layer, tag)]
        values = {}
        for rule in relevant:
            for field, value in (rule.match(text) or {}).items():
                values.setdefault(field, value)
        return bool(relevant), values

    def major(self, landuse):
        """Class of the first code in a land-use string such as R2+C1, by the longest matching prefix."""
        if not landuse or not self.majors:
            return None
        first = re.split(r'[+/、,，]', landuse)[0].strip()
        keys = sorted((k for k in self.majors if first.startswith(k)), key=len, reverse=True)
        return self.majors[keys[0]] if keys else None

    def is_road(self, landuse, source_layers, labelled):
        """A road face has a road land-use code, or no labels at all and only road layers around it."""
        codes = set(re.split(r'[+/、,，]', landuse)) if landuse else set()
        if codes & self.road_codes:
            return True
        layers = set(filter(None, (source_layers or '').split(',')))
        return bool(self.road_layers and not labelled and layers and layers <= self.road_layers)


def load(path):
    import yaml
    text = Path(path).read_text(encoding='utf-8')
    return Rules(yaml.safe_load(text)), text
