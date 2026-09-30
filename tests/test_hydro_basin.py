"""Permanent independent basin checks used by real frozen-reference acceptance."""
import importlib.util
from pathlib import Path
import numpy as np
import pytest
spec=importlib.util.spec_from_file_location('basin_acceptance',Path(__file__).with_name('run_hydro_basin_acceptance.py'));h=importlib.util.module_from_spec(spec);spec.loader.exec_module(h)


def test_upstream_direction_and_disconnected_cells():
    # Eight neighbours flow into centre, and one corner is an outward boundary.
    d=np.array([[7,6,5],[8,0,4],[1,2,-1]])
    result=h.upstream_mask(d,(1,1))
    assert result.sum()==8 and not result[2,2]
    assert h.upstream_mask(d,(2,2)).sum()==1


def test_cycle_terminates_and_does_not_import_unconnected():
    d=np.array([[8,4,0],[0,0,0],[0,0,0]])
    assert h.upstream_mask(d,(0,0)).sum()==2


def test_basin_metrics_rejects_proximity_only_and_boundary_inflow():
    ref=np.zeros((5,5),bool);ref[1:4,1:4]=True
    candidate=ref.copy();a=np.ones((5,5));m=h.basin_metrics(candidate,ref,a)
    assert m['iou']==1 and m['relative_area_error']==0 and m['boundary_contact_cells']==0
    candidate[0,2]=True;a[1,1]=-3;m=h.basin_metrics(candidate,ref,a)
    assert m['iou']==pytest.approx(.9) and m['boundary_contact_cells']==1 and m['negative_accumulation_cells']==1
    unrelated=np.zeros((5,5),bool);unrelated[0,:]=True
    assert h.basin_metrics(unrelated,ref,a)['iou']==0
    with pytest.raises(ValueError,match='Empty'):h.basin_metrics(candidate,np.zeros((5,5),bool),a)
    with pytest.raises(ValueError,match='Aligned'):h.basin_metrics(candidate,ref[:2],a)


def test_reference_identity_is_catalogue_bound_and_directional():
    import geopandas as gpd
    from shapely.geometry import box,LineString
    b=gpd.GeoDataFrame({'HYBAS_ID':[1],'NEXT_DOWN':[2]},geometry=[box(0,0,10,10)],crs=32645)
    n=gpd.GeoDataFrame({'HYBAS_ID':[2]},geometry=[box(10,0,20,10)],crs=32645)
    rivers=gpd.GeoDataFrame({'Reach_ID':[3,4],'Next_down':[4,0]},geometry=[LineString([(2,5),(10,5)]),LineString([(10,5),(20,5)])],crs=32645)
    p=dict(basin_id=1,river_outlet_id=3,old_endpoint_identity_m=.01,reference_boundary_identity_m=1,outlet_reference=[10,5])
    report=h.reference_identity(b,n,rivers,p)
    assert report['downstream_endpoint_index']==1 and report['reference_endpoint_boundary_distance_m']==0
    assert report['next_reach_id']==4 and report['next_basin_id']==2
    with pytest.raises(ValueError,match='coordinate'):h.reference_identity(b,n,rivers,dict(p,outlet_reference=[10,6]))
    with pytest.raises(ValueError,match='NEXT_DOWN'):h.reference_identity(b,n.assign(HYBAS_ID=8),rivers,p)
    with pytest.raises(ValueError,match='ambiguous'):h.reference_identity(b,n,rivers.assign(Next_down=9),p)
