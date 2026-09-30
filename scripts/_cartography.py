"""Bounded vector morphology, summaries and derived cartographic candidates."""
import json
import math
import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import LineString
from _daily import stable, metric, number


def positive(p, key, zero=False):
    n=number(p[key])
    if n<0 or (not zero and n==0): raise ValueError(f'Invalid {key}')
    return n


def prepare(frame,p):
    if hasattr(shapely,'has_m') and shapely.has_m(frame.geometry.to_numpy()).any():raise ValueError('Explicit 2D input required')
    stable(frame,p['id'])
    work,factor=metric(frame,p.get('analysis_crs'))
    work=work.sort_values(p['id'],key=lambda x:x.astype(str)).reset_index(drop=True)
    return work,factor


def morphology(frame,p):
    work,factor=prepare(frame,p); method=p.get('method','metrics')
    if method not in ('metrics','convex_hull','concave_hull','oriented_envelope'): raise ValueError('Unknown morphology method')
    ratio=number(p.get('ratio',0.5))
    if not 0<=ratio<=1: raise ValueError('Hull ratio outside [0,1]')
    cols=['long_m','short_m','aspect','direction_deg','compactness']
    if set(cols)&set(work): raise ValueError('Morphology field collision')
    rows=[]
    for geom in work.geometry:
        env=geom.minimum_rotated_rectangle
        long=short=direction=aspect=None
        if env.geom_type=='Polygon':
            xy=np.asarray(env.exterior.coords); delta=np.diff(xy,axis=0); lengths=np.linalg.norm(delta,axis=1)
            k=int(np.argmax(lengths)); long=float(max(lengths))*factor; short=float(min(lengths))*factor
            aspect=long/short if short else None
            # Square has no unique principal direction.
            if not math.isclose(long,short,rel_tol=1e-12): direction=math.degrees(math.atan2(delta[k,1],delta[k,0]))%180
        elif env.geom_type=='LineString':
            a,b=env.coords;long=env.length*factor;short=0.
            direction=math.degrees(math.atan2(b[1]-a[1],b[0]-a[0]))%180
        rows.append([long,short,aspect,direction,4*math.pi*geom.area/geom.length**2 if geom.area and geom.length else None])
    for k,c in enumerate(cols):work[c]=[r[k] for r in rows]
    if method=='convex_hull':work.geometry=shapely.convex_hull(work.geometry.to_numpy())
    elif method=='concave_hull':work.geometry=shapely.concave_hull(work.geometry.to_numpy(),ratio=ratio,allow_holes=bool(p.get('allow_holes',False)))
    elif method=='oriented_envelope':work.geometry=shapely.oriented_envelope(work.geometry.to_numpy())
    return work,{'method':method,'metrics_on':'source geometry','direction':'long envelope axis, degrees CCW from projected east modulo 180; square undefined','analysis_crs':str(work.crs),'status':'candidate' if method!='metrics' else 'complete','adopted':False}


def weighted_summary(frame,p):
    group=p['group']; kind=p['quantity_type']
    if frame[group].isna().any() or frame[group].astype(str).eq('').any(): raise ValueError('Missing summary group')
    weights=pd.to_numeric(frame[p['weight']],errors='raise').to_numpy(dtype=float)
    if not np.isfinite(weights).all() or (weights<0).any(): raise ValueError('Weights must be finite nonnegative')
    values=frame[p['value']]
    if values.isna().any():raise ValueError('Unknown values require explicit preparation')
    data=pd.DataFrame({'group':frame[group].to_numpy(),'value':values.to_numpy(),'weight':weights})
    if kind=='ratio':
        data['value']=pd.to_numeric(data['value'],errors='raise')
        if not np.isfinite(data['value']).all():raise ValueError('Nonfinite ratio')
        rows=[]
        for g,d in data.groupby('group',sort=True):
            total=float(d.weight.sum())
            if not math.isfinite(total) or not math.isfinite(float(np.dot(d.value,d.weight))):raise ValueError('Weighted summary overflow')
            rows.append({'group':g,'weighted_ratio':float(np.dot(d.value,d.weight)/total) if total else None,'weight_sum':total,'count':len(d),'status':'complete' if total else 'undefined_zero_weight'})
        return pd.DataFrame(rows),{'quantity_type':'ratio','assumption':p['weight_meaning'],'zero_weight':'undefined'}
    if kind!='category':raise ValueError('Expected ratio or category')
    if values.astype(str).eq('').any():raise ValueError('Empty category')
    out=data.groupby(['group','value'],sort=True).agg(weight_sum=('weight','sum'),count=('weight','size')).reset_index().rename(columns={'value':'category'})
    sums=out.groupby('group').weight_sum.transform('sum');out['fraction']=out.weight_sum/sums.replace(0,np.nan)
    return out,{'quantity_type':'category','assumption':p['weight_meaning'],'ties':'all categories retained; no winner inferred'}


def impacts(before,after,factor,key):
    return [{'id':str(a[key]),'before_wkt':a.geometry.wkt,'after_wkt':b.geometry.wkt,
             'area_delta_m2':(b.geometry.area-a.geometry.area)*factor**2,
             'displacement_m':a.geometry.hausdorff_distance(b.geometry)*factor,
             'holes_before':sum(len(g.interiors) for g in shapely.get_parts(a.geometry) if g.geom_type=='Polygon'),
             'holes_after':sum(len(g.interiors) for g in shapely.get_parts(b.geometry) if g.geom_type=='Polygon')}
            for (_,a),(_,b) in zip(before.iterrows(),after.iterrows())]


def generalize(frame,p):
    import networkx as nx
    work,factor=prepare(frame,p); key=p['id']; method=p['method']; protected=set(map(str,p.get('protected_ids',[])))
    if not protected<=set(work[key].astype(str)):raise ValueError('Unknown protected ID')
    if method in ('aggregate_points','aggregate_polygons'):
        types=['Point'] if method=='aggregate_points' else ['Polygon','MultiPolygon']
        if not work.geom_type.isin(types).all():raise ValueError('Wrong aggregation geometry type')
        distance=positive(p,'distance_m',True)/factor; graph=nx.Graph();graph.add_nodes_from(range(len(work)))
        for i,g in enumerate(work.geometry):
            for j in work.sindex.query(g.buffer(distance) if distance else g):
                if j>i and str(work.iloc[i][key]) not in protected and str(work.iloc[j][key]) not in protected and g.distance(work.geometry.iloc[j])<=distance:
                    graph.add_edge(i,int(j))
        rows=[]; mapping=[]
        for c in sorted(nx.connected_components(graph),key=lambda c:min(c)):
            indices=sorted(c); sub=work.iloc[indices]; ids=sub[key].astype(str).tolist(); union=sub.geometry.union_all()
            geom=union.centroid if method=='aggregate_points' else union
            # Polygon union does not bridge unknown gaps, fill holes, or smooth evidence.
            oid='aggregate:'+ids[0]; rows.append({'derived_id':oid,'source_ids':json.dumps(ids),'geometry':geom})
            mapping.append({'derived_id':oid,'source_ids':ids,'source_area_m2':sum(sub.area)*factor**2,'output_area_m2':geom.area*factor**2,'max_displacement_m':max(g.hausdorff_distance(geom) for g in sub.geometry)*factor})
        return gpd.GeoDataFrame(rows,geometry='geometry',crs=work.crs),{'status':'candidate','adopted':False,'mapping':mapping,'protected_ids':sorted(protected),'semantics':'single-link groups; polygon exact union retains gaps as multipart and existing holes'}
    if method=='regularize_buildings':
        if not work.geom_type.isin(['Polygon','MultiPolygon']).all():raise ValueError('Buildings require polygons')
        limit=positive(p,'max_displacement_m',True); area=positive(p,'max_area_change_m2',True)
        out=work.copy(); reasons=[]
        for i,g in enumerate(work.geometry):
            candidate=g.minimum_rotated_rectangle; reason='envelope_candidate'
            if str(work.iloc[i][key]) in protected or g.geom_type!='Polygon' or len(g.interiors):candidate=g;reason='protected_hole_or_multipart'
            if g.hausdorff_distance(candidate)*factor>limit or abs(candidate.area-g.area)*factor**2>area:candidate=g;reason='impact_limit'
            # Reject new overlap or changes to any shared edge: coverage simplification is a separate existing operation.
            for j in work.sindex.query(candidate):
                if i!=j and (candidate.intersection(work.geometry.iloc[j]).area>g.intersection(work.geometry.iloc[j]).area+1e-12 or (g.boundary.intersection(work.geometry.iloc[j].boundary).length>0 and not candidate.equals(g))):candidate=g;reason='neighbor_conflict';break
            out.at[i,out.geometry.name]=candidate;reasons.append({'id':str(work.iloc[i][key]),'reason':reason})
        # Pairwise candidates can overlap even when neither overlaps the other's original.
        for i,g in enumerate(out.geometry):
            for j in out.sindex.query(g):
                if j>i and g.intersection(out.geometry.iloc[j]).area>work.geometry.iloc[i].intersection(work.geometry.iloc[j]).area+1e-12:raise ValueError('Regularized candidates conflict')
        return out,{'status':'candidate','adopted':False,'impacts':impacts(work,out,factor,key),'decisions':reasons,'method':'bounded minimum rotated rectangle; holes/multipart/shared edges protected'}
    if method=='thin_network':
        if not work.geom_type.eq('LineString').all():raise ValueError('Network requires simple line features and explicit node fields')
        u,v=p['from'],p['to']; priority=pd.to_numeric(work[p['priority']],errors='raise')
        if not np.isfinite(priority).all():raise ValueError('Invalid road priority')
        coords={}; graph=nx.MultiGraph()
        for i,row in work.iterrows():
            for field,xy in ((u,row.geometry.coords[0]),(v,row.geometry.coords[-1])):
                node=str(row[field])
                if pd.isna(row[field]) or not node:raise ValueError('Missing network node')
                if node in coords and coords[node]!=tuple(xy):raise ValueError('Node coordinate mismatch; explicit noding required')
                coords[node]=tuple(xy)
            graph.add_edge(str(row[u]),str(row[v]),key=i,weight=float(priority.iloc[i]),row=i)
        # Maximum spanning forest preserves every original endpoint-component; important cycles retained explicitly.
        forest=nx.maximum_spanning_tree(graph); keep={d['row'] for *_,d in forest.edges(data=True)}
        keep|={i for i,row in work.iterrows() if str(row[key]) in protected or priority.iloc[i]>=number(p['keep_priority_at_least'])}
        out=work.iloc[sorted(keep)].copy(); check=nx.Graph();check.add_nodes_from(graph.nodes)
        check.add_edges_from((str(r[u]),str(r[v])) for _,r in out.iterrows())
        components=lambda g:sorted(sorted(c) for c in nx.connected_components(g))
        if components(graph)!=components(check):raise ValueError('Network connectivity changed')
        return out,{'status':'candidate','adopted':False,'source_ids':work[key].astype(str).tolist(),'excluded':[{'id':str(work.iloc[i][key]),'wkt':work.geometry.iloc[i].wkt,'reason':'redundant low-priority cycle'} for i in range(len(work)) if i not in keep],'components_before':components(graph),'components_after':components(check),'semantics':'undirected endpoint connectivity only; no route cost, turn, or directional preservation'}
    if method=='color':
        if not work.geom_type.isin(['Polygon','MultiPolygon']).all():raise ValueError('Color requires polygons')
        if 'color_index' in work:raise ValueError('Color field collision')
        graph=nx.Graph();graph.add_nodes_from(range(len(work)))
        for i,g in enumerate(work.geometry):
            for j in work.sindex.query(g):
                if j>i:
                    other=work.geometry.iloc[j]
                    if g.intersection(other).area>1e-12:raise ValueError('Overlapping polygons require repair')
                    if g.intersects(other) and (p.get('point_touch',True) or g.boundary.intersection(other.boundary).length>0):graph.add_edge(i,int(j))
        colors=nx.coloring.greedy_color(graph,strategy='largest_first');work['color_index']=[colors[i] for i in range(len(work))]
        return work,{'semantics':'display adjacency color only; not a semantic class','edges':[[str(work.iloc[i][key]),str(work.iloc[j][key])] for i,j in graph.edges],'color_count':len(set(colors.values()))}
    raise ValueError('Unknown generalization method')
