#!/usr/bin/env python3
"""Real local elevation -> grid means / classified display -> portable Composer PNGs.

Diagnostic grid is constructed here, not an administrative/observed boundary.
This is a runnable acceptance example, not a workflow engine or delivery protocol.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import geopandas as gpd
import numpy as np
from PIL import Image
import rasterio as rio
from rasterio.warp import transform_bounds, transform
from shapely import contains_xy
from shapely.geometry import box

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from _delivery import bundle, digest, write_json

BREAKS = [1000, 2000, 3000, 4000]
COLORS = ['#edf8b1', '#a1dab4', '#41b6c4', '#2c7fb8', '#253494']
LABELS = ['< 1,000 m', '1,000–<2,000 m', '2,000–<3,000 m', '3,000–<4,000 m', '≥ 4,000 m']


def run(command, cwd, log, success=True):
    result = subprocess.run(list(map(str, command)), cwd=cwd, capture_output=True, text=True)
    write_json(log, {'command': list(map(str, command)), 'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
    if success and result.returncode:
        raise RuntimeError(f'Command failed; see {log}: {result.stderr[-1200:]}')
    if not success and result.returncode == 0:
        raise AssertionError(f'Expected failure: {command}')
    return result


def scene(kind, coordinates, bounds, semantics):
    label_source = {'id':'names', 'type':'geojson', 'path':'grid labels.geojson'}
    unknown = '#d9d9d9'
    paint = ['case', ['==', ['get','mean'], None], unknown,
             ['step', ['get','mean'], COLORS[0], *[x for pair in zip(BREAKS,COLORS[1:]) for x in pair]]]
    s = {'id':kind, 'theme':'data-focus', 'renderer':'maplibre',
         'title': '勃朗峰周边 · ' + ('网格平均高程' if kind == 'vector' else '高程分级'),
         'subtitle':'本地 GEBCO 2023 标记样区 | candidate · 来源与垂直基准未独立核验 · 注记为网格均值',
         'canvas':{'width':1400,'height':1000,'pixelRatio':1},
         'map':{'bounds':bounds,'fitPadding':{'top':150,'bottom':100,'left':60,'right':330},'renderWorldCopies':False,'background':'#f6f7f8'},
         'typography':{'fontFamily':'AnalysisSans','mapFontFamily':'AnalysisSans','fonts':[{'family':'AnalysisSans','path':'font.otf','license':'SIL OFL 1.1; font-license.txt'}]},
         'sources':[{'id':'grid','type':'geojson','path':'grid means.geojson'},label_source],
         'layers':[], 'analysis':semantics,
         'overlay':{'frame':False,'scaleBar':False,'title':{'size':34},'subtitle':{'size':17},
                    'legend':{'x':1080,'y':190,'width':275,'fontSize':17,'items':[{'type':'classified','title':('网格均值' if kind=='vector' else '像元高程')+' / m',
                    'classes':[{'color':c,'label':l} for c,l in zip(COLORS,LABELS)]+[{'color':unknown,'label':'未知 / NoData（无有效像元）'}]}]}},
         'qa':{'requiredLabels':[{'layerId':'names','ids':[f'{i:02}' for i in range(1,13)]}]}}
    if kind == 'raster':
        s['sources'].insert(0, {'id':'elevation','type':'image','path':'display/display.png','coordinates':coordinates})
        s['layers'] += [{'id':'unknown','source':'grid','type':'fill','fill':unknown,'legend':False},
                        {'id':'elevation','source':'elevation','type':'raster','paint':{'raster-resampling':'nearest','raster-fade-duration':0},'legend':False}]
    else:
        s['layers'].append({'id':'means','source':'grid','type':'fill','paint':{'fill-color':paint},'legend':False})
    s['layers'] += [{'id':'grid-lines','source':'grid','type':'line','paint':{'line-color':'#ffffff','line-width':1.4},'legend':False},
                    {'id':'names','source':'names','type':'symbol','text':{'field':'name','size':16,'color':'#172c3f','haloColor':'#ffffff','haloWidth':2},'placement':{'optional':False},'legend':False}]
    return s


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('source', help='Real elevation sample in metres, self-contained GeoTIFF')
    p.add_argument('output', help='New output directory, outside source repositories')
    p.add_argument('--composer', required=True)
    p.add_argument('--source-description', required=True)
    args=p.parse_args()
    source=Path(args.source).resolve(); output=Path(args.output).resolve(); composer=Path(args.composer).resolve()
    output.mkdir(parents=True,exist_ok=False)
    logs=output/'logs'; logs.mkdir()
    before=digest(source)
    entry=composer/'src/agent-entry.js'
    counter=0
    def command(cmd, cwd=output, success=True):
        nonlocal counter
        counter+=1
        return run(cmd,cwd,logs/f'{counter:02}.json',success)
    def gis(script,*args):
        return command([sys.executable,SCRIPTS/script,*args])
    def raster(operation,input,params,dest):
        settings=output/f'params-{dest.name}.json'; write_json(settings,params)
        gis('raster.py',operation,input,'--params',settings,'--output',dest)
        return json.loads((dest/'record.json').read_text())
    with bundle(output/'task original') as stage:
        shutil.copy2(source,stage/'source.tif')
        for src,dest in [('SourceHanSansCN-Normal.otf','font.otf'),('SourceHanSansCN-Normal.license.txt','font-license.txt')]:
            shutil.copy2(composer/'assets/fonts'/src,stage/dest)
        warped=raster('warp',stage/'source.tif',{'band':1,'crs':'EPSG:3857','kind':'continuous','resampling':'nearest','resolution':250},stage/'warp')
        with rio.open(stage/'warp/result.tif') as ds:
            left,bottom,right,top=ds.bounds
        dx=(right-left)/3; dy=(top-bottom)/3
        grid=gpd.GeoDataFrame({'id':[f'{i:02}' for i in range(1,13)]},geometry=[box(left+x*dx,bottom+y*dy,left+(x+1)*dx,bottom+(y+1)*dy) for y in range(3) for x in range(4)],crs=3857)
        grid.to_file(stage/'zones.gpkg',layer='diagnostic_grid')
        # Decoy layer catches accidentally relying on first/default layer.
        grid.iloc[:1].to_file(stage/'zones.gpkg',layer='decoy')
        zonal=raster('zonal',stage/'source.tif',{'vector':str(stage/'zones.gpkg'),'layer':'diagnostic_grid','id':'id','band':1,'method':'center'},stage/'zonal')
        # Independent polygon contains tests over actual source pixel centres.
        with rio.open(stage/'source.tif') as ds:
            data=ds.read(1,masked=True); rows,cols=np.indices(data.shape)
            xs=ds.transform.c+(cols+.5)*ds.transform.a; ys=ds.transform.f+(rows+.5)*ds.transform.e
            for geom,row in zip(grid.to_crs(ds.crs).geometry,zonal['zones']):
                valid=contains_xy(geom,xs,ys) & ~np.ma.getmaskarray(data) & np.isfinite(data.data)
                assert row['valid_pixels']==int(valid.sum())
                expected=float(data.data[valid].mean()) if valid.any() else None
                assert row['mean'] is None if expected is None else np.isclose(row['mean'],expected,rtol=1e-12)
        grid['mean']=[r['mean'] for r in zonal['zones']]
        grid['valid_pixels']=[r['valid_pixels'] for r in zonal['zones']]
        grid['status']='candidate'; grid['unit']='m'
        grid.to_file(stage/'analysis.gpkg',layer='means')
        gis('inspect-data.py',stage/'analysis.gpkg','--layer','means','summary','--json')
        gis('stats.py','describe',stage/'analysis.gpkg','--layer','means','--json')
        gis('convert.py',stage/'analysis.gpkg','--layer','means','--crs','EPSG:4326','--output',stage/'grid means.geojson')
        reread=gpd.read_file(stage/'grid means.geojson')
        assert np.allclose(grid['mean'],reread['mean'],equal_nan=True)
        names=gpd.GeoDataFrame({'id':grid.id,'name':[f'网格 {i}\n{v:.0f} m' if np.isfinite(v) else f'网格 {i}\n未知' for i,v in zip(grid.id,grid['mean'])]},geometry=grid.geometry.centroid,crs=3857).to_crs(4326)
        names.to_file(stage/'grid labels.geojson')
        display=raster('display',stage/'warp/result.tif',{'band':1,'breaks':BREAKS,'colors':COLORS,'unit':'m','status':'candidate'},stage/'display')
        bounds=list(transform_bounds('EPSG:3857','EPSG:4326',left,bottom,right+dx,top))
        semantics={'status':'candidate','source':{'file':'source.tif','sha256':before,'description':args.source_description},
                   'vector':{'file':'analysis.gpkg','layer':'means','field':'mean','unit':'m','denominator':'valid source pixel count, equal-weight centre inclusion','zones':'constructed diagnostic grid; not administrative units'},
                   'raster':{'file':'warp/result.tif','band':1,'unit':'m','resampling':'nearest, explicit EPSG:3857 display grid'},
                   'breaks':BREAKS,'colors':COLORS,'interval_rule':display['interval_rule'],'unknown':'null mean / no valid pixels; transparent raster over gray diagnostic grid; never zero',
                   'upstream_identity_verified':False,'vertical_datum':'unknown'}
        controls=[]
        with rio.open(stage/'warp/result.tif') as ds:
            values=ds.read(1,masked=True)
            # Interior, away from grid lines and centre labels; compare actual PNG color.
            for i,(rf,cf) in enumerate(((.13,.13),(.23,.77),(.44,.21),(.61,.87),(.83,.41))):
                row,col=int(ds.height*rf),int(ds.width*cf)
                x,y=ds.xy(row,col)
                lon,lat=transform(ds.crs,'EPSG:4326',[x],[y])
                color=COLORS[int(np.searchsorted(BREAKS,values[row,col],side='right'))]
                controls.append({'id':str(i),'coordinates':[lon[0],lat[0]],'expected_rgb':[int(color[j:j+2],16) for j in (1,3,5)]})
        for kind in ('vector','raster'):
            spec=scene(kind,display['coordinates'],bounds,semantics)
            if kind=='raster': spec['qa']['controlPoints']=controls
            write_json(stage/f'{kind}.scene.json',spec)
            command(['node',entry,stage/f'{kind}.scene.json','--out',stage/f'{kind}.png'])
            assert json.loads((stage/f'{kind}.qa.json').read_text())['summary']['ok']
            metrics=json.loads((stage/f'{kind}.metrics.json').read_text())
            assert metrics['render']['resourceUrls']
            assert all(url.startswith(('http://127.0.0.1:','data:','blob:')) for url in metrics['render']['resourceUrls'])
            if kind=='raster':
                pixels=np.asarray(Image.open(stage/'raster.png').convert('RGB'))
                for observed,expected in zip(metrics['render']['maps']['main']['controlPoints'],controls):
                    x,y=observed['pixel']; assert np.array_equal(pixels[round(y),round(x)],expected['expected_rgb']), (observed,expected,pixels[round(y),round(x)])
        # Publication is all-or-nothing, using the existing directory bundle helper.
        write_json(stage/'record.json',{'delivery':'complete','analysis_status':'candidate','analysis':semantics,
                  'files':{str(f.relative_to(stage)):digest(f) for f in stage.rglob('*') if f.is_file()}})
    task=output/'task original'; moved=output/'moved task with spaces'; task.rename(moved)
    protected={str(f.relative_to(moved)):digest(f) for f in moved.rglob('*') if f.suffix in ('.tif','.gpkg','.geojson')}
    comparisons={}
    for kind in ('vector','raster'):
        # Fresh subprocess and fresh Chrome; deliberately call from a different cwd.
        repeat=output/f'{kind}-repeat.png'
        command(['node',entry,moved/f'{kind}.scene.json','--out',repeat])
        with Image.open(moved/f'{kind}.png') as a,Image.open(repeat) as b:
            assert np.array_equal(np.asarray(a.convert('RGBA')),np.asarray(b.convert('RGBA')))
        assert digest(moved/f'{kind}.png')==digest(repeat)
        comparisons[kind]={'rgba_equal':True,'png_sha256':digest(repeat)}
    styled=json.loads((moved/'vector.scene.json').read_text()); styled['layers'][1]['paint']['line-color']='#263238'
    write_json(moved/'style-only.scene.json',styled)
    command(['node',entry,moved/'style-only.scene.json','--out',output/'style-only.png'])
    assert digest(output/'style-only.png') != digest(moved/'vector.png')
    failures=[]
    for case in ('missing-resource','outside-root','qa-failed','write-failed'):
        bad=copy.deepcopy(styled)
        if case=='missing-resource': bad['sources'][0]['path']='missing.geojson'
        if case=='outside-root': bad['sources'][0]['path']='../outside.geojson'
        if case=='qa-failed': bad['qa']['requiredLabels'][0]['ids'].append('not-present')
        destination=output/f'rejected-{case}'
        try:
            with bundle(destination) as stage:
                (stage/'analysis.json').write_text('{}')
                write_json(moved/'failure.scene.json',bad)
                png=stage/'map.png'
                if case=='write-failed': png.mkdir()
                result=command(['node',entry,moved/'failure.scene.json','--out',png],success=False)
                assert result.stderr or result.stdout
                raise RuntimeError('expected consumer failure')
        except RuntimeError as e:
            assert str(e)=='expected consumer failure'
        assert not destination.exists()
        failures.append(case)
    (moved/'failure.scene.json').unlink()
    assert protected=={str(f.relative_to(moved)):digest(f) for f in moved.rglob('*') if f.suffix in ('.tif','.gpkg','.geojson')}
    assert digest(source)==before
    write_json(output/'acceptance.json',{'source_sha256':before,'repeat':comparisons,'failure_cases':failures,'analysis_unchanged':True,'interior_raster_color_controls':5,'observed_page_resources':'loopback/data/blob only; no basemap requests',
               'visual_review':'pending human/agent image inspection','limits':['constructed grid','upstream identity and vertical datum unverified','exact glyph bounds unavailable'],
               'delivery':str(moved)})
    print(output/'acceptance.json')


if __name__=='__main__': main()
