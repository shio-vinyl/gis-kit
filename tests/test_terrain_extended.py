import importlib.util
import json
import os
from pathlib import Path
import sys

import geopandas as gpd
import numpy as np
import pytest
import rasterio as rio
from rasterio.transform import from_origin
from shapely.geometry import LineString

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0,str(SCRIPTS))

def module(name):
    spec=importlib.util.spec_from_file_location(name,SCRIPTS/f'{name}.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

D=module('terrain-detail');B=module('terrain-backend')
P=dict(vertical_unit='metre',vertical_datum='unknown',source_description='synthetic regression')
T=from_origin(500000,3100000,10,10)

def raster(path,z):
    with rio.open(path,'w',driver='GTiff',width=z.shape[1],height=z.shape[0],count=1,dtype='float64',crs='EPSG:32645',transform=T) as d:d.write(z.astype(float),1)
    return path


def test_contour_centres_and_gap():
    z=np.tile(np.arange(5.),(5,1));g=D.contours(z,T,'EPSG:32645',[1.5],100)
    assert len(g)==1
    np.testing.assert_allclose(np.asarray(g.geometry.iloc[0].coords)[:,0],500020)
    assert g.total_bounds[1]==3099955 and g.total_bounds[3]==3099995
    z[2,2]=np.nan;g=D.contours(z,T,'EPSG:32645',[1.5],100)
    assert all(not x.intersects(LineString([(500015,3099975),(500025,3099975)])) for x in g.geometry)


@pytest.mark.parametrize('levels',[[1,1],[2,1],[float('nan')],[],[True]])
def test_bad_levels(levels):
    with pytest.raises(ValueError):D.contours(np.ones((3,3)),T,'EPSG:32645',levels,100)


def test_profiles_source_measures_missing_and_reorder():
    z=np.tile(np.arange(5.),(5,1));z[2,2]=np.nan
    g=gpd.GeoDataFrame({'id':['one','two']},geometry=[LineString([(500005,3099975),(500065,3099975)]),LineString([(500005,3099995),(500025,3099995)])],crs='EPSG:32645')
    p=dict(id='id',distance_m=10)
    a,_=D.profile_points(g,p,z,T,g.crs);b,_=D.profile_points(g.iloc[::-1],p,z,T,g.crs)
    one=a[a.source_id=='one']
    assert one.measure_m.tolist()==[0,10,20,30,40,50,60]
    assert one.sample_status.tolist()==['valid','valid','nodata','valid','valid','outside','outside']
    assert sorted(a.sample_id)==sorted(b.sample_id)


def test_detail_file_chain_and_failure(tmp_path):
    source=raster(tmp_path/'dem.tif',np.tile(np.arange(5.),(5,1)))
    D.execute(source,dict(P,levels_m=[1.5]),tmp_path/'out')
    g=gpd.read_file(tmp_path/'out/contours.gpkg');assert len(g)==1 and g.elevation_m.iloc[0]==1.5
    with pytest.raises(ValueError):D.execute(source,dict(P,levels_m=[1.5]),tmp_path/'out')
    with pytest.raises(ValueError):D.execute(source,dict(P,levels_m=[1.5],max_features=0),tmp_path/'bad')
    assert not (tmp_path/'bad').exists()


@pytest.mark.parametrize('changes',[{'flow_method':'foo'},{'threshold_cells':0},{'threshold_cells':True},{'outlet':[0,0]},{'vertical_unit':'foot'},{'surprise':1},{'flow_method':'MFD','outlet':[500005,3099995]}])
def test_backend_parameter_contract(tmp_path,changes):
    source=raster(tmp_path/'dem.tif',np.ones((5,5)))
    params=dict(P,flow_method='D8',threshold_cells=5);params.update(changes)
    with rio.open(source) as ds:
        with pytest.raises(ValueError):B.validate('hydrology',params,ds)


@pytest.fixture
def grass():
    value=os.environ.get('GIS_TEST_GRASS')
    if not value:pytest.skip('Set GIS_TEST_GRASS to explicitly enable real backend tests')
    return value


def read(path):
    with rio.open(path) as d:return d.read(1)


def test_real_grass_d8_plane_outlet(tmp_path,grass):
    source=raster(tmp_path/'dem.tif',np.tile(np.arange(12.,0,-1),(12,1)))
    B.execute('hydrology',source,dict(P,flow_method='D8',threshold_cells=10,outlet=[500105,3099935]),tmp_path/'out',grass)
    np.testing.assert_allclose(read(tmp_path/'out/drainage.tif')[1:-1,1:-1],8)
    np.testing.assert_allclose(read(tmp_path/'out/accumulation.tif')[6],[-1,-1,-2,-3,-4,-5,-6,-7,-8,-9,-10,-11])
    catch=read(tmp_path/'out/catchment.tif');assert np.isfinite(catch).sum()==11 and np.all(catch[6,:11]==1)
    assert not list(tmp_path.glob('.grass-work-*'))


def test_real_grass_visibility_flat_and_wall(tmp_path,grass):
    z=np.zeros((15,15));source=raster(tmp_path/'dem.tif',z)
    p=dict(P,observers=[dict(id='west',x=500035,y=3099925,height_m=2),dict(id='east',x=500125,y=3099925,height_m=2)],target_height_m=0,max_distance_m=200)
    B.execute('viewshed',source,p,tmp_path/'flat',grass)
    a=read(tmp_path/'flat/visible_count.tif');assert np.all(a==2)
    z[:,7]=100;source=raster(tmp_path/'wall.tif',z)
    B.execute('viewshed',source,p,tmp_path/'wall',grass)
    a=read(tmp_path/'wall/view_0.tif');assert a[7,5]==1 and a[7,11]==0
    b=read(tmp_path/'wall/view_1.tif');assert b[7,11]==1 and b[7,3]==0


def test_real_grass_mfd_and_gap_rejection(tmp_path,grass):
    z=np.tile(np.arange(8.,0,-1),(8,1));source=raster(tmp_path/'dem.tif',z)
    B.execute('hydrology',source,dict(P,flow_method='MFD',threshold_cells=5),tmp_path/'out',grass)
    assert np.isfinite(read(tmp_path/'out/accumulation.tif')).all()
    z[3,3]=np.nan;source=raster(tmp_path/'gap.tif',z)
    with pytest.raises(ValueError,match='complete DEM'):B.execute('hydrology',source,dict(P,flow_method='D8',threshold_cells=5),tmp_path/'bad',grass)
    assert not (tmp_path/'bad').exists()


def test_grass_text_region_roundoff_not_half_pixel_shift():
    from affine import Affine
    expected=Affine(450,0,334047.6898001569,0,-450,3141789.049735672)
    actual=Affine(450,0,334047.68980016,0,-450,3141789.04973567)
    assert B.grid_error(actual,expected,(47,63))<1e-9
    with pytest.raises(ValueError,match='shifted'):B.grid_error(expected*Affine.translation(.5,0),expected,(47,63))


def test_observer_snap_and_ids_are_explicit(tmp_path):
    source=raster(tmp_path/'dem.tif',np.zeros((5,5)))
    p=dict(P,observers=[dict(id='one',x=500005,y=3099995,height_m=2)],target_height_m=0,max_distance_m=100)
    with rio.open(source) as ds:
        B.validate('viewshed',p,ds)
        p['observers'][0]['x']+=1
        with pytest.raises(ValueError,match='pixel centres'):B.validate('viewshed',p,ds)


def test_empty_contours_remain_valid_file(tmp_path):
    source=raster(tmp_path/'dem.tif',np.zeros((5,5)))
    D.execute(source,dict(P,levels_m=[10]),tmp_path/'out')
    assert gpd.read_file(tmp_path/'out/contours.gpkg').empty


def test_backend_failure_publishes_nothing(tmp_path,monkeypatch):
    from types import SimpleNamespace
    source=raster(tmp_path/'dem.tif',np.zeros((5,5)))
    monkeypatch.setattr(B.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=1,stdout='',stderr=''))
    with pytest.raises(ValueError,match='no result published'):
        B.execute('hydrology',source,dict(P,flow_method='D8',threshold_cells=5),tmp_path/'out',sys.executable)
    assert not (tmp_path/'out').exists() and not list(tmp_path.glob('.grass-work-*')) and not list(tmp_path.glob('.delivery-*'))
