"""plot.py map templates are frozen fallbacks; finished map styling belongs to gis-composer."""
import importlib.util
import sys
from pathlib import Path

import geopandas as gpd
from shapely.geometry import box

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))

# Changing this list needs a reason gis-composer cannot cover (references/composer-display.md).
FROZEN = {'location': {'default', 'minimal'}, 'landuse': {'standard', 'custom'}}


def _plot():
    spec = importlib.util.spec_from_file_location('plot_cli', ROOT/'scripts/plot.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_map_templates_are_frozen():
    for category, styles in FROZEN.items():
        assert {p.stem for p in (ROOT/'templates'/category).glob('*.yaml')} == styles, category


def test_each_category_has_a_default_style(tmp_path, monkeypatch):
    plot = _plot()
    assert set(plot._DEFAULT_STYLE) == set(FROZEN)
    assert all(style in FROZEN[c] for c, style in plot._DEFAULT_STYLE.items())
    src = tmp_path/'parcels.gpkg'
    gpd.GeoDataFrame({'code': ['R', 'G']}, geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1)], crs='EPSG:3857').to_file(src)
    out = tmp_path/'landuse.png'
    monkeypatch.setattr(sys, 'argv', ['plot.py', '--inputs', str(src), '--output', str(out),
                                      '--category', 'landuse', '--color-field', 'code', '--dpi', '60'])
    plot.main()
    assert out.stat().st_size > 0
