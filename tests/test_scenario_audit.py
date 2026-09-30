"""Permanent finite-sensitivity provenance and known spatial structures."""
import importlib.util
import os
from pathlib import Path
import sys
import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import Point
S=Path(__file__).resolve().parents[1]/'scripts';sys.path.insert(0,str(S))

def module(name):
    spec=importlib.util.spec_from_file_location(name,S/f'{name}.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
W=module('suitability');I=module('spatial-inference')


def test_finite_scenario_affected_objects_and_missing():
    g=gpd.GeoDataFrame({'id':['a','b','missing'],'x':[10,0,np.nan],'y':[0,10,5]},geometry=[Point(0,0),Point(1,0),Point(2,0)],crs=32645)
    p=dict(id='id',study='hand calculation',criteria={k:dict(field=k,low=0,high=10,prefer='high') for k in ('x','y')},scenarios=[dict(id='base',weights={'x':3,'y':1},threshold=.5),dict(id='swap',weights={'x':1,'y':3},threshold=.5),dict(id='threshold',weights={'x':3,'y':1},threshold=.8),dict(id='scaled',weights={'x':6,'y':2},threshold=.5)])
    _,d=W.score(g,p);swap,threshold,scaled=d['scenario_comparisons']
    assert swap['normalized_weight_delta']=={'x':-.5,'y':.5}
    assert swap['newly_selected_ids']==['b'] and swap['lost_selected_ids']==['a']
    assert swap['rank_changed_ids']==['a','b'] and swap['score_changed_ids']==['a','b']
    assert threshold['score_changed_ids']==[] and threshold['rank_changed_ids']==[] and threshold['lost_selected_ids']==['a']
    assert scaled['score_changed_ids']==[] and scaled['rank_changed_ids']==[] and scaled['newly_selected_ids']==[]
    assert d['data_insufficient_ids']==['missing']
    _,reordered=W.score(g.iloc[::-1],p);assert d==reordered


@pytest.mark.parametrize('pattern,expected', [('cluster',1.),('alternating',-1.)])
def test_known_moran_structure(tmp_path,pattern,expected):
    backend=os.environ.get('GIS_TEST_PYSAL_PYTHON')
    if not backend:pytest.skip('Explicit PySAL backend required')
    # Four disconnected pairs, each node has exactly one neighbor.
    values=[1,1,1,1,9,9,9,9] if pattern=='cluster' else [1,9]*4
    g=gpd.GeoDataFrame({'id':[str(i) for i in range(8)],'v':values},geometry=[Point(i//2*10,i%2) for i in range(8)],crs=32645)
    p=dict(id='id',value='v',study='synthetic known pair structure; exchangeable labels under null',analysis_crs=32645,operation='infer',radius_m=2,permutations=199,seed=731)
    frame,xy,_=I.prepare(g,p);d=I.infer(frame,xy,p,tmp_path,dict(python=backend,site_packages=os.environ.get('GIS_TEST_PYSAL_SITE'),proj_data=os.environ.get('GIS_TEST_PYSAL_PROJ')))
    assert d['global_I']==pytest.approx(expected)
    back=gpd.read_file(tmp_path/'statistics.gpkg')
    np.testing.assert_allclose(back.local_I,expected*7/8)
    assert back.local_q.between(0,1).all()
    assert (back.local_q>=back.local_p).all()
    assert set(back.local_quadrant)==({1,3} if expected>0 else {2,4})


@pytest.mark.parametrize('study',[None,True,{},'', '  '])
def test_inference_requires_text_assumptions(study):
    g=gpd.GeoDataFrame({'id':['a']},geometry=[Point(0,0)],crs=32645)
    with pytest.raises(ValueError,match='assumptions'):I.prepare(g,dict(id='id',study=study,analysis_crs=32645))
