"""Read area values and their measurement method through the field CLI."""
import os
import subprocess
import sys
from pathlib import Path

import geopandas as gpd
from pyproj import Geod
import pytest
from shapely.geometry import MultiPolygon, Polygon, box


def run_area(tmp_path, geom, crs, *args):
    frame = gpd.GeoDataFrame(geometry=[geom], crs=4326).to_crs(crs)
    src, dest = tmp_path / "source.gpkg", tmp_path / "areas.gpkg"
    frame.to_file(src)
    tool = Path(__file__).resolve().parents[1] / "scripts" / "field.py"
    subprocess.run([sys.executable, str(tool), "add-area", str(src), "--output", str(dest), *args],
                   check=True, capture_output=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    output = gpd.read_file(dest)
    assert output.crs == frame.crs
    return output.iloc[0]


@pytest.mark.parametrize("west,lat,crs", [(5.9, 40, 4326), (10, 85, 4326), (10, 40, 32632)])
def test_geodesic_area_cross_zone_polar_and_projected(tmp_path, west, lat, crs):
    outer = box(west, lat, west + .2, lat + .1)
    hole = box(west + .02, lat + .02, west + .04, lat + .05)
    island = box(west + .3, lat, west + .4, lat + .01)
    shape = MultiPolygon([Polygon(outer.exterior, [hole.exterior]), island])
    geod = Geod(ellps="WGS84")
    def reference(rect):
        return abs(geod.geometry_area_perimeter(rect)[0])
    expected = reference(outer) - reference(hole) + reference(island)
    row = run_area(tmp_path, shape, crs, "--geodesic")
    assert row.area_m2 == pytest.approx(expected, rel=1e-9)
    assert row.area_method == "ellipsoidal geodesic (WGS 84)"


@pytest.mark.parametrize("crs,args,method", [
    (4326, [], "automatic UTM 32N (EPSG:32632)"),
    (32632, [], "source projected CRS (EPSG:32632)"),
    (4326, ["--crs", "EPSG:6933"], "explicit projected CRS (EPSG:6933)"),
])
def test_area_method_names_actual_projection(tmp_path, crs, args, method):
    row = run_area(tmp_path, box(10, 40, 10.01, 40.01), crs, *args)
    assert row.area_method == method and row.area_m2 > 0


@pytest.mark.parametrize("command", ["far", "density", "summary", "coverage"])
def test_indicator_geodesic_areas_across_zones(tmp_path, command):
    import json

    parcels = [box(5.8, 70, 6, 70.2), box(6, 70, 6.2, 70.2)]
    footprint = box(5.9, 70.05, 6.1, 70.15)
    parcels_path, building_path = tmp_path / "lots.gpkg", tmp_path / "buildings.gpkg"
    gpd.GeoDataFrame({"id": ["a", "b"]}, geometry=parcels, crs=4326).to_file(parcels_path)
    gpd.GeoDataFrame({"floors": [3]}, geometry=[footprint], crs=4326).to_file(building_path)
    output = tmp_path / "result.gpkg"
    args = ["--geodesic", "--output", str(output)]
    if command == "coverage":
        args += ["--boundary", str(parcels_path), "--target", str(building_path)]
    else:
        args += ["--parcels", str(parcels_path), "--buildings", str(building_path), "--parcel-id", "id",
                 "--report-json", str(tmp_path / "report.json")]
        if command in ("far", "summary"):
            args += ["--floors-field", "floors"]
        if command == "summary":
            args += ["--green", str(building_path)]
    tool = Path(__file__).resolve().parents[1] / "scripts" / "indicators.py"
    subprocess.run([sys.executable, str(tool), command, *args], check=True, capture_output=True)
    rows = gpd.read_file(output)
    geod = Geod(ellps="WGS84")
    def area(geom):
        return abs(geod.geometry_area_perimeter(geom)[0])
    assert set(rows.area_method) == {"ellipsoidal geodesic (WGS 84)"}
    if command == "coverage":
        assert rows.green_area_m2.iloc[0] == pytest.approx(area(footprint), rel=1e-9)
        assert rows.coverage_ratio.iloc[0] == pytest.approx(area(footprint) / area(parcels[0].union(parcels[1])), rel=1e-9)
    else:
        report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
        assert report["analysis_method"] == rows.area_method.iloc[0]
        assert report["unallocated_footprint_area_m2"] == 0
        assert report["allocated_footprint_area_m2"] == pytest.approx(area(footprint), rel=1e-9)
        assert rows.parcel_area_m2.tolist() == pytest.approx([area(p) for p in parcels], rel=1e-9)
        if command in ("density", "summary"):
            assert rows.density.iloc[0] == pytest.approx(0.5 * area(footprint) / area(parcels[0]), rel=1e-9)
        if command in ("far", "summary"):
            assert rows.FAR.iloc[0] == pytest.approx(1.5 * area(footprint) / area(parcels[0]), rel=1e-9)
        if command == "summary":
            assert rows.green_area_m2.iloc[0] == pytest.approx(area(footprint.intersection(parcels[0])), rel=1e-9)
