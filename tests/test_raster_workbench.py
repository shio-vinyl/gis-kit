"""Synthetic geometry tests; these do not measure model/map accuracy."""
from pathlib import Path
import copy
import importlib.util
import json
import tempfile
import unittest
import sys

import numpy as np
from PIL import Image
from shapely.geometry import LineString, Point

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))

def module(name,file):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/file)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result

A=module('annotation','raster-annotate.py')
G=module('georef','raster-georef.py')
ACTOR={'model':'test-vision-model','reasoning_effort':None}


def square():
    state={g:{} for g in A.GROUPS}
    state.update(revision=0,crossings=[])
    state['nodes']={f'n{i}':{'xy':p} for i,p in enumerate([[10,10],[50,10],[50,50],[10,50]])}
    state['edges']={f'e{i}':{'start':f'n{i}','end':f'n{(i+1)%4}','kind':'administrative',
                           'status':'visible','geometry':{'type':'polyline','vertices':[]}} for i in range(4)}
    state['faces']={'f1':{'name':'A','status':'candidate','outer':[{'edge':f'e{i}'} for i in range(4)],'holes':[]}}
    state['points']={'p1':{'xy':[20,20],'names':['Alpha','Alfa'],'status':'visible'}}
    return state


class Workbench(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.image=self.root/'map.png';self.run=self.root/'run'
        Image.new('RGB',(100,80),'white').save(self.image)
        A.init(self.image,self.run,'synthetic geometry test')

    def patch(self,state=None,**kwargs):
        data={'actor':ACTOR,'base_revision':0,'mode':'draft','put':{g:(state or square())[g] for g in A.GROUPS}}
        data.update(kwargs)
        # UUID avoids filename collision between intentionally rejected requests.
        import uuid
        path=self.root/(uuid.uuid4().hex+'.json');A.write(path,data)
        return path

    def test_shared_rings_holes_and_incomplete(self):
        state=square()
        self.assertEqual(A.face_geom(state['faces']['f1'],state,.25).area,1600)
        reverse=[{'edge':f'e{i}','reverse':True} for i in reversed(range(4))]
        self.assertEqual(A.face_geom({'outer':reverse},state,.25).area,1600)
        state['faces']['f1']['outer'].pop()
        with self.assertRaisesRegex(ValueError,'open'):A.validate_structure(state)
        state['faces']['f1']['status']='incomplete';A.validate_structure(state)
        self.assertIn('incomplete_face',[i['kind'] for i in A.diagnose(state,100,80)['issues']])
        state=square()
        for i,p in enumerate([[20,20],[30,20],[30,30],[20,30]]):state['nodes'][f'h{i}']={'xy':p}
        for i in range(4):state['edges'][f'h{i}']={'start':f'h{i}','end':f'h{(i+1)%4}','kind':'lake','status':'visible','geometry':{'type':'polyline','vertices':[]}}
        state['faces']['f1']['holes']=[[{'edge':f'h{i}'} for i in range(4)]]
        self.assertEqual(A.face_geom(state['faces']['f1'],state,.25).area,1500)

    def test_revision_actor_and_shape_freeze(self):
        A.apply_patch(self.run,self.patch())
        self.assertEqual(A.load(self.run)['revision'],1)
        selected=A.summary(self.run,['f1'])['objects']
        self.assertEqual(len(selected['edges']),4)
        self.assertEqual(len(selected['nodes']),4)
        self.assertEqual(len(selected['points']),0)
        with self.assertRaisesRegex(ValueError,'Stale'):A.apply_patch(self.run,self.patch())
        with self.assertRaisesRegex(ValueError,'requires'):A.apply_patch(self.run,self.patch(actor={'model':'','reasoning_effort':'low'}))
        p=self.patch(base_revision=1,mode='shape',put={'nodes':{'n0':{'xy':[11,10]}}})
        with self.assertRaisesRegex(ValueError,'only change'):A.apply_patch(self.run,p)
        self.assertEqual(A.load(self.run)['revision'],1)
        edge=copy.deepcopy(square()['edges']['e0']);edge['geometry']['vertices']=[[30,11]]
        A.apply_patch(self.run,self.patch(base_revision=1,mode='shape',put={'edges':{'e0':edge}}))
        self.assertEqual(A.load(self.run)['revision'],2)
        self.assertEqual(len(list((self.run/'requests').glob('*.json'))),5)
        self.assertEqual(A.load(self.run,0)['edges'],{})

    def test_view_patch_conversion_and_atomic_rejection(self):
        result=A.view(self.run,[10,20,90,70],40)
        point={'xy':[0,0],'names':['view origin'],'status':'visible'}
        path=self.patch(put={'points':{'p':point}},view_id=result['view_id'])
        A.apply_patch(self.run,path)
        self.assertEqual(A.load(self.run)['points']['p']['xy'],[10.5,20.5])
        with self.assertRaisesRegex(ValueError,'stale revision'):
            A.apply_patch(self.run,self.patch(base_revision=1,mode='topology',put={},view_id=result['view_id']))
        bad=self.root/'bad.json'
        with self.assertRaises(ValueError):A.write(bad,{'x':float('nan')})
        self.assertFalse(bad.exists())

    def test_progressive_append_shared_node_and_fresh_view(self):
        first = A.view(self.run, [10, 10, 60, 60], 100, zoom=2)
        edge = {'start':'a', 'end':'join', 'kind':'administrative', 'status':'visible',
                'geometry':{'type':'polyline', 'vertices':[[40.5, 20.5]]}}
        A.apply_patch(self.run, self.patch(mode='append', view_id=first['view_id'], put={
            'nodes':{'a':{'xy':[0.5, 0.5]}, 'join':{'xy':[80.5, 40.5]}},
            'edges':{'left':edge}}))
        before = A.load(self.run)
        self.assertEqual(before['nodes']['join']['xy'], [50, 30])
        second = A.view(self.run, [40, 20, 90, 70], 50)
        right = copy.deepcopy(edge)
        right.update(start='join', end='b', geometry={'type':'polyline', 'vertices':[[20, 15]]})
        A.apply_patch(self.run, self.patch(base_revision=1, mode='append',
            view_id=second['view_id'], put={'nodes':{'b':{'xy':[40, 20]}}, 'edges':{'right':right}}))
        after = A.load(self.run)
        self.assertEqual(after['nodes']['b']['xy'], [80, 40])
        self.assertEqual(after['edges']['right']['geometry']['vertices'], [[60, 35]])
        self.assertEqual(after['edges']['left'], before['edges']['left'])
        self.assertEqual(after['nodes']['join'], before['nodes']['join'])
        self.assertEqual(A.edge_coords(after['edges']['left'], after['nodes'])[-1],
                         A.edge_coords(after['edges']['right'], after['nodes'])[0])
        self.assertEqual(A.check(self.run, .25)['issue_count'], 0)
        with self.assertRaisesRegex(ValueError, 'stale revision'):
            A.apply_patch(self.run, self.patch(base_revision=2, mode='append',
                view_id=second['view_id'], put={'points':{'p':{'xy':[2, 2]}}}))
        self.assertEqual(A.load(self.run)['revision'], 2)
        self.assertEqual(len(list((self.run/'requests').glob('*.json'))), 3)

    def test_append_rejects_overwrites_and_mutations_atomically(self):
        A.apply_patch(self.run, self.patch())
        before = A.load(self.run)
        requests = [dict(put={group:{next(iter(before[group])):next(iter(before[group].values()))}})
                    for group in A.GROUPS]
        requests += [dict(put={}, delete={'edges':['e0']}),
                     dict(put={}, splice=[{'edge':'e0','start':0,'delete_count':0,'vertices':[[20, 10]]}]),
                     dict(put={'nodes':{'new':{'xy':[5, 5]}}}, crossings=[]),
                     dict(put={}), dict(put={'unknown':{'x':{}}})]
        for request in requests:
            with self.subTest(request=request):
                with self.assertRaises(ValueError):
                    A.apply_patch(self.run, self.patch(base_revision=1, mode='append', **request))
                self.assertEqual(A.load(self.run), before)

    def test_append_face_reuses_existing_boundary(self):
        state = square()
        face = state['faces'].pop('f1')
        A.apply_patch(self.run, self.patch(state))
        before = A.load(self.run)
        A.apply_patch(self.run, self.patch(base_revision=1, mode='append', put={'faces':{'f1':face}}))
        after = A.load(self.run)
        self.assertEqual(after['nodes'], before['nodes'])
        self.assertEqual(after['edges'], before['edges'])
        self.assertEqual(A.face_geom(after['faces']['f1'], after, .25).area, 1600)

    def test_human_review_pack_and_feedback_preserve_geometry(self):
        A.apply_patch(self.run, self.patch())
        before = A.load(self.run)
        pack = A.review_pack(self.run, [[0, 0, 60, 60], [40, 0, 100, 80]])
        self.assertEqual([r['id'] for r in pack['regions']], ['R01', 'R02'])
        self.assertTrue(all(r['status']=='pending' for r in pack['regions']))
        self.assertTrue(Path(pack['index']).is_file())
        with Image.open(Path(pack['regions'][0]['directory'])/'overlay.png') as overlay:
            self.assertEqual(overlay.getpixel((30, 10)), (0, 123, 196))
        marked = self.root/'circled.png'
        Image.new('RGB', (30, 30), 'red').save(marked)
        path = self.root/'feedback.json'
        A.write(path, {'reviewer':'human', 'decisions':{
            'R01':{'status':'rejected', 'note':'Inspect this bend', 'marked_image':'circled.png'}}})
        result = A.feedback(self.run, pack['pack_id'], path)
        self.assertEqual(result['rejected_ids'], ['R01'])
        self.assertEqual(result['decisions']['R02']['status'], 'pending')
        attachment = result['decisions']['R01']['marked_image']
        self.assertEqual(attachment['sha256'], A.digest(marked))
        self.assertEqual((Path(result['record']).parent/attachment['file']).read_bytes(), marked.read_bytes())
        second = self.root/'feedback2.json'
        A.write(second, {'reviewer':'human', 'decisions':{'R02':{'status':'accepted'}}})
        latest = A.feedback(self.run, pack['pack_id'], second)
        self.assertEqual(latest['rejected_ids'], ['R01'])
        self.assertEqual(latest['decisions']['R02']['status'], 'accepted')
        self.assertEqual(A.load(self.run), before)
        self.assertEqual(A.read(result['record'])['decisions']['R02']['status'], 'pending')

    def test_feedback_rejects_stale_or_invalid_decisions(self):
        pack = A.review_pack(self.run, [[0, 0, 60, 60]])
        folder = Path(pack['directory'])/'feedback'
        for i, record in enumerate([
                {'reviewer':'model', 'decisions':{'R01':{'status':'accepted'}}},
                {'reviewer':'human', 'decisions':{'R99':{'status':'rejected'}}},
                {'reviewer':'human', 'decisions':{'R01':{'status':'maybe'}}}]):
            path = self.root/f'reject-{i}.json'
            A.write(path, record)
            with self.assertRaises(ValueError): A.feedback(self.run, pack['pack_id'], path)
        self.assertEqual(list(folder.glob('*.json')), [])
        valid = self.root/'human.json'
        A.write(valid, {'reviewer':'human', 'decisions':{'R01':{'status':'rejected'}}})
        A.apply_patch(self.run, self.patch())
        with self.assertRaisesRegex(ValueError, 'Stale review pack'):
            A.feedback(self.run, pack['pack_id'], valid)
        self.assertEqual(A.load(self.run)['revision'], 1)
        self.assertEqual(len(list((Path(pack['directory'])/'requests').glob('*.json'))), 4)

    def test_human_review_bounds_are_validated_before_generating(self):
        for bounds in [[], [[0,0,101,80]], [[0,0,50.5,80]], [[0,0,50,50]]*7]:
            with self.assertRaises(ValueError): A.review_pack(self.run, bounds)
        self.assertEqual(list((self.run/'views').iterdir()), [])

    def test_adjacent_faces_reuse_edge(self):
        state=square()
        state['nodes'].update({'r0':{'xy':[90,10]},'r1':{'xy':[90,50]}})
        for key,start,end in [('r0','n1','r0'),('r1','r0','r1'),('r2','r1','n2')]:
            state['edges'][key]={'start':start,'end':end,'kind':'administrative','status':'visible','geometry':{'type':'polyline'}}
        state['faces']['f2']={'status':'candidate','outer':[{'edge':'r0'},{'edge':'r1'},{'edge':'r2'},{'edge':'e1','reverse':True}]}
        A.validate_structure(state)
        first=A.face_geom(state['faces']['f1'],state,.25)
        second=A.face_geom(state['faces']['f2'],state,.25)
        self.assertEqual(first.intersection(second).length,40)
        self.assertEqual(first.intersection(second).area,0)
        self.assertEqual(A.diagnose(state,100,80)['issues'],[])

    def test_center_crop_mapping_and_overlay(self):
        A.apply_patch(self.run,self.patch())
        result=A.view(self.run,[10,20,90,70],31,True,labels=True)
        sx,sy=result['view_to_original']['scale'];ox,oy=result['view_to_original']['offset']
        self.assertAlmostEqual(ox,10+(sx-1)/2)
        self.assertAlmostEqual(oy,20+(sy-1)/2)
        w,h=result['size']
        self.assertAlmostEqual((w-.5)*sx+ox,89.5)
        self.assertAlmostEqual((h-.5)*sy+oy,69.5)
        folder=Path(result['directory'])
        self.assertTrue((folder/'original.png').exists());self.assertTrue((folder/'overlay.png').exists())
        self.assertIsNone(result['actually_viewed'])
        self.assertEqual(len(result['label_map']),5)

    def test_cubic_preserved_and_overshoot_bounded(self):
        p0,p1,p2,p3=[0,0],[100,0],[-100,0],[1,0]
        pts=A.flatten_cubic(p0,p1,p2,p3,.05)
        self.assertGreater(len(pts),2)
        line=LineString(pts)
        for t in np.linspace(0,1,1000):
            p=(1-t)**3*np.array(p0)+3*(1-t)**2*t*np.array(p1)+3*(1-t)*t*t*np.array(p2)+t**3*np.array(p3)
            self.assertLessEqual(Point(p).distance(line),.051)
        state=square();state['edges']['e0']['geometry']={'type':'cubic','segments':[{'c1':[20,5],'c2':[40,5],'end':[50,10]}]}
        svg=A.svg_text(state,100,80);self.assertIn(' C ',svg)
        del state['edges']['e0']['geometry']['segments'][0]['end']
        self.assertEqual(A.edge_coords(state['edges']['e0'],state['nodes'])[-1],[50,10])
        self.assertEqual(A.svg_text(state,100,80),svg)
        with self.assertRaises(ValueError):A.edge_coords(state['edges']['e0'],state['nodes'],0)

    def test_crossing_disconnected_and_bounds(self):
        state=square();state['faces']={};state['edges']={
            'a':{'start':'n0','end':'n2','kind':'test','status':'visible','geometry':{'type':'polyline'}},
            'b':{'start':'n1','end':'n3','kind':'test','status':'visible','geometry':{'type':'polyline'}}}
        self.assertIn('unresolved_crossing',[i['kind'] for i in A.diagnose(state,100,80)['issues']])
        state['crossings']=[{'edges':['a','b'],'xy':[30,30],'relation':'disconnected'}]
        self.assertNotIn('unresolved_crossing',[i['kind'] for i in A.diagnose(state,100,80)['issues']])
        state['points']['p1']['xy']=[101,10]
        self.assertIn('out_of_image',[i['kind'] for i in A.diagnose(state,100,80)['issues']])

    def test_export_pixel_crs_and_session_unknowns(self):
        import geopandas as gpd
        A.apply_patch(self.run,self.patch())
        result=A.export(self.run);folder=Path(result['directory'])
        frame=gpd.read_file(folder/'pixel-candidates.gpkg',layer='faces')
        self.assertEqual(len(frame),1)
        self.assertTrue(frame.crs is None or frame.crs.to_epsg() is None)
        record=self.root/'session.json';A.write(record,{**ACTOR,'status':'failed','viewed_ids':[]})
        result=A.session(self.run,record)
        self.assertIsNone(result['usage']['input_tokens'])
        record=self.root/'bad-session.json';A.write(record,{**ACTOR,'status':'completed','usage':{'input_tokens':2,'cached_input_tokens':3}})
        with self.assertRaisesRegex(ValueError,'subset'):A.session(self.run,record)

    def test_splice_keeps_context_and_rejects_overlap(self):
        state=square();state['edges']['e0']['geometry']['vertices']=[[20,10],[30,10],[40,10]]
        A.apply_patch(self.run,self.patch(state))
        A.apply_patch(self.run,self.patch(base_revision=1,mode='shape',put={},splice=[
            {'edge':'e0','start':0,'delete_count':1,'vertices':[[20,11]]},
            {'edge':'e0','start':2,'delete_count':1,'vertices':[[40,11]]}]))
        self.assertEqual(A.load(self.run)['edges']['e0']['geometry']['vertices'],[[20,11],[30,10],[40,11]])
        with self.assertRaisesRegex(ValueError,'Overlapping'):
            A.apply_patch(self.run,self.patch(base_revision=2,mode='shape',put={},splice=[
                {'edge':'e0','start':0,'delete_count':2,'vertices':[]},
                {'edge':'e0','start':1,'delete_count':1,'vertices':[]}]))
        self.assertEqual(A.load(self.run)['revision'],2)

    def test_normalize_exact_locus_and_idempotence(self):
        state=square();state['edges']['e0']['geometry']['vertices']=[[10,10],[20,10],[20,10],[50,10]]
        A.apply_patch(self.run,self.patch(state))
        before=A.load(self.run)
        result=A.normalize(self.run)
        after=A.load(self.run)
        self.assertEqual(result['revision'],2)
        self.assertEqual(after['edges']['e0']['geometry']['vertices'],[[20,10]])
        self.assertEqual(before['nodes'],after['nodes'])
        self.assertTrue(LineString(A.edge_coords(before['edges']['e0'],before['nodes'])).equals(LineString(A.edge_coords(after['edges']['e0'],after['nodes']))))
        self.assertEqual(A.normalize(self.run)['revision'],2)

    def test_local_review_and_repair_never_auto_adopts(self):
        state=square();state['nodes']['n1']['xy']=[50,50];state['nodes']['n2']['xy']=[50,10];state['nodes']['n3']['xy']=[10,50]
        A.apply_patch(self.run,self.patch(state))
        result=A.review(self.run,padding=8,zoom=3,preprocess='autocontrast')
        self.assertIn('bounds',result['issue'])
        with Image.open(Path(result['directory'])/'original.png') as image:
            self.assertGreater(image.width,17)
        self.assertTrue((Path(result['directory'])/'enhanced.png').exists())
        original=A.read(self.run/'manifest.json')['sha256']
        candidate=A.repair_candidate(self.run,'f1')
        self.assertEqual(candidate['component_types'],['Polygon','Polygon'])
        self.assertEqual(A.load(self.run)['revision'],1)
        self.assertEqual(A.source(self.run)[0]['sha256'],original)

    def test_indexed_diagnostics_and_local_self_crossing(self):
        state={g:{} for g in A.GROUPS};state.update(revision=0,crossings=[])
        for i in range(100):
            state['nodes'][f'a{i}']={'xy':[i*5,0]};state['nodes'][f'b{i}']={'xy':[i*5,3]}
            state['edges'][f'e{i}']={'start':f'a{i}','end':f'b{i}','kind':'test','status':'visible','geometry':{'type':'polyline'}}
        result=A.diagnose(state,600,100)
        self.assertEqual(result['candidate_pairs']['edges'],0)
        self.assertEqual(result['issues'],[])
        hits=A.self_crossings([[0,0],[10,10],[10,0],[0,10]])
        self.assertEqual(hits[0]['bounds'],[5,5,5,5])
        self.assertEqual(hits[0]['segments'],[0,2])

    def test_preprocessing_geometry_halo_and_original(self):
        from _raster_preprocess import enhance,crop_aid
        rng=np.random.default_rng(24)
        raw=rng.integers(40,240,(120,140,3),dtype=np.uint8)
        im=Image.fromarray(raw)
        for mode in ('gray','autocontrast','neutral-ink','palette-denoise','local-contrast'):
            processed=enhance(im,mode)
            self.assertEqual(processed.size,im.size)
        self.assertTrue(np.array_equal(np.asarray(im),raw))
        full=enhance(im,'neutral-ink').crop((40,40,90,90))
        local=crop_aid(im,(40,40,90,90),'neutral-ink')
        self.assertTrue(np.array_equal(np.asarray(full),np.asarray(local)))
        with self.assertRaises(ValueError):enhance(im,'neutral-ink',20)

    def test_local_contrast_registered_aid(self):
        from _raster_preprocess import enhance
        raw=np.full((80,100,3),200,dtype=np.uint8)
        raw[20:50,40:43]=120
        Image.fromarray(raw).save(self.image)
        # New source requires a fresh immutable run.
        run=self.root/'contrast-run';A.init(self.image,run,'contrast test')
        result=A.view(run,[20,10,80,60],300,preprocess='local-contrast',zoom=3)
        folder=Path(result['directory'])
        with Image.open(folder/'original.png') as original, Image.open(folder/'enhanced.png') as aid:
            self.assertEqual(original.size,aid.size)
            self.assertEqual(original.size,(180,150))
            self.assertFalse(np.array_equal(np.asarray(original),np.asarray(aid)))
        self.assertEqual(result['preprocess']['local_contrast_parameters']['original_weight'],.5)
        self.assertEqual(A.digest(folder/'enhanced.png'),result['preprocess']['enhanced_sha256'])
        self.assertEqual(A.digest(self.image),A.source(run)[0]['sha256'])
        uniform=enhance(Image.new('RGB',(64,64),(150,150,150)),'local-contrast')
        self.assertEqual(len(np.unique(np.asarray(uniform).reshape(-1,3),axis=0)),1)

    def gcps(self):
        points=[]
        for i,(x,y) in enumerate([(0,0),(90,0),(0,70),(90,70),(40,30)]):
            points.append({'id':f'g{i}','pixel':[x,y],'world':[500000+2*x+.2*y,4000000+.1*x-2*y], 'role':'fit' if i<4 else 'check'})
        return {'frame':{'id':'main','pixel_region':{'type':'Polygon','coordinates':[[[0,0],[99,0],[99,79],[0,79],[0,0]]]}},'acceptance_policy':{'max_check_rmse':.01,'max_check_error':.02,'min_fit_coverage':.8,'rationale':'synthetic tolerance'},'actor':ACTOR,'source_sha256':A.digest(self.image),'source_crs':'EPSG:32631','target_crs':'EPSG:32631','coordinate_reference':'synthetic affine in UTM; not historical data','points':points}

    def test_affine_heldout_center_attach_and_vectors(self):
        import rasterio
        import geopandas as gpd
        data=self.gcps();path=self.root/'gcp.json';A.write(path,data)
        transform=self.root/'transform.json';result=G.fit(path,transform)
        self.assertLess(result['check']['max'],1e-7)
        G.preview(self.image,transform,self.root/'gcp-review.png')
        self.assertTrue((self.root/'gcp-review.png').exists())
        out=self.root/'map.tif';G.attach(self.image,transform,out)
        with rasterio.open(out) as src:
            self.assertTrue(np.allclose(src.xy(0,0),[500000,4000000]))
            self.assertTrue(np.all(src.read()==255))
        A.apply_patch(self.run,self.patch());folder=Path(A.export(self.run)['directory'])
        vectors=self.root/'world.gpkg';G.vectors(folder/'pixel-candidates.gpkg',transform,vectors,folder/'export.json')
        point=gpd.read_file(vectors,layer='points').geometry.iloc[0]
        self.assertAlmostEqual(point.x,500044,places=6)
        self.assertAlmostEqual(point.y,3999962,places=6)
        with self.assertRaisesRegex(ValueError,'different image'):
            G.vectors(vectors,transform,self.root/'wrong.gpkg',folder/'export.json')

    def test_georef_quality_gate_and_frame(self):
        data=self.gcps()
        data['points'][-1]['world'][0]+=20
        path=self.root/'bad-check.json';A.write(path,data)
        transform=self.root/'hold.json';result=G.fit(path,transform)
        self.assertEqual(result['acceptance']['status'],'hold')
        with self.assertRaisesRegex(ValueError,'hold'):
            G.attach(self.image,transform,self.root/'blocked.tif')
        self.assertFalse((self.root/'blocked.tif').exists())
        G.attach(self.image,transform,self.root/'diagnostic.tif',True)
        self.assertTrue((self.root/'diagnostic.tif.manifest.json').exists())
        data=self.gcps();data.pop('acceptance_policy')
        path=self.root/'no-policy.json';A.write(path,data)
        self.assertEqual(G.fit(path,self.root/'no-policy-report.json')['acceptance']['status'],'hold')
        data=self.gcps();data['acceptance_policy']['min_fit_coverage']=1
        path=self.root/'coverage.json';A.write(path,data)
        result=G.fit(path,self.root/'coverage-report.json')
        self.assertIn('Fit-point coverage below threshold',result['acceptance']['reasons'])
        data=self.gcps();data['frame']['pixel_region']['coordinates']=[[[0,0],[20,0],[20,20],[0,20],[0,0]]]
        path=self.root/'wrong-frame.json';A.write(path,data)
        with self.assertRaisesRegex(ValueError,'belong'):G.fit(path,self.root/'wrong-frame-report.json')

    def test_frame_mask_omissions_and_handoff(self):
        import rasterio
        data=self.gcps()
        data['frame']['pixel_region']['coordinates'].append([[18,18],[18,22],[22,22],[22,18],[18,18]])
        path=self.root/'inset.json';A.write(path,data)
        transform=self.root/'inset-transform.json';G.fit(path,transform)
        out=self.root/'masked.tif';G.attach(self.image,transform,out)
        with rasterio.open(out) as src:
            self.assertEqual(src.dataset_mask()[20,20],0)
            self.assertEqual(src.dataset_mask()[10,10],255)
        A.apply_patch(self.run,self.patch());folder=Path(A.export(self.run)['directory'])
        output=self.root/'isolated.gpkg'
        result=G.vectors(folder/'pixel-candidates.gpkg',transform,output,folder/'export.json')
        record=A.read(result['manifest'])
        self.assertEqual(record['output']['sha256'],A.digest(output))
        self.assertEqual(record['pixel_export']['manifest']['revision'],1)
        self.assertEqual({x['layer'] for x in record['omitted_objects']},{'points','faces'})

    def test_gcp_duplicate_and_bad_fit(self):
        data=self.gcps();data['points'][-1]['pixel']=data['points'][0]['pixel']
        path=self.root/'dup.json';A.write(path,data)
        with self.assertRaisesRegex(ValueError,'Duplicate pixel'):G.fit(path,self.root/'no.json')
        data=self.gcps()
        for i,p in enumerate(data['points']):p['pixel']=[i,i]
        path=self.root/'collinear.json';A.write(path,data)
        with self.assertRaisesRegex(ValueError,'Rank'):G.fit(path,self.root/'no.json')

if __name__=='__main__':unittest.main()
