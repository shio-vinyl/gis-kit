"""Cross-ID correspondence first. No implicit geometry adoption or rubber sheeting."""
import json
import math
import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point,LineString
from shapely.ops import substring
from _daily import stable
from _metric import analysis_frames
from _cartography import positive,impacts


def candidates(left,right,p):
    key=p['id'];rkey=p.get('right_id',key);stable(left,key);stable(right,rkey)
    (a,b),factor=analysis_frames([left,right],p.get('analysis_crs'))
    a=a.sort_values(key,key=lambda x:x.astype(str)).reset_index(drop=True)
    b=b.sort_values(rkey,key=lambda x:x.astype(str)).reset_index(drop=True)
    if not a.geom_type.isin(['Point','LineString']).all() or not b.geom_type.isin(['Point','LineString']).all():raise ValueError('Matching requires Point or simple LineString; explode multipart explicitly')
    if any(not g.is_simple or (g.geom_type=='LineString' and g.is_ring) for g in [*a.geometry,*b.geometry]):raise ValueError('Simple open lines required')
    radius=positive(p,'radius_m')/factor;angle_limit=positive(p,'max_angle_deg',True)
    if angle_limit>90:raise ValueError('max_angle_deg must be at most 90')
    min_overlap=positive(p,'min_overlap_m',True)/factor
    limit=int(p.get('max_pairs',100000)); rows=[]; by_source={str(x):[] for x in a[key]}
    for i,row in a.iterrows():
        g=row.geometry
        for j in sorted(b.sindex.query(g.buffer(radius))):
            ref=b.iloc[j];h=ref.geometry
            if h.geom_type!=g.geom_type or g.distance(h)>radius:continue
            attr={f:bool(pd.notna(row[f]) and pd.notna(ref[t]) and row[f]==ref[t]) for f,t in p.get('attributes',{}).items()}
            if p.get('require_attributes',False) and not all(attr.values()):continue
            start=end=tstart=tend=angle=shape=None
            if g.geom_type=='LineString':
                start,end=sorted([g.project(Point(h.coords[0])),g.project(Point(h.coords[-1]))])
                if end-start<=min_overlap:continue
                part=substring(g,start,end)
                tstart,tend=sorted([h.project(Point(part.coords[0])),h.project(Point(part.coords[-1]))])
                if tend-tstart<=min_overlap:continue
                target=substring(h,tstart,tend)
                va=np.subtract(part.coords[-1],part.coords[0]);vb=np.subtract(target.coords[-1],target.coords[0]);den=np.linalg.norm(va)*np.linalg.norm(vb)
                if den==0:continue
                angle=math.degrees(math.acos(float(np.clip(abs(np.dot(va,vb)/den),0,1))))
                shape=part.hausdorff_distance(target)*factor
                if angle>angle_limit or shape>radius*factor:continue
            else:shape=g.distance(h)*factor
            evidence={'source_id':str(row[key]),'target_id':str(ref[rkey]),'distance_m':g.distance(h)*factor,'shape_distance_m':shape,'angle_deg':angle,
                      'source_start_m':start*factor if start is not None else None,'source_end_m':end*factor if end is not None else None,
                      'target_start_m':tstart*factor if tstart is not None else None,'target_end_m':tend*factor if tend is not None else None,
                      'attributes':attr,'status':'candidate'}
            rows.append(evidence);by_source[str(row[key])].append(evidence)
            if len(rows)>limit:raise ValueError('Matching exceeds max_pairs')
    return a,b,factor,rows,by_source


def match(left,right,p):
    a,b,factor,rows,groups=candidates(left,right,p)
    for oid,group in groups.items():
        # All alternatives retained. Two disjoint line sections are not automatically ambiguous.
        for r in group:
            r['ambiguous']=any(s is not r and (r['source_start_m'] is None or min(r['source_end_m'],s['source_end_m'])>max(r['source_start_m'],s['source_start_m'])+1e-9) for s in group)
    cols=['source_id','target_id','distance_m','shape_distance_m','angle_deg','source_start_m','source_end_m','target_start_m','target_end_m','attributes','status','ambiguous']
    table=pd.DataFrame([{**r,'attributes':json.dumps(r['attributes'],sort_keys=True)} for r in rows],columns=cols)
    return table,{'status':'candidate','adopted':False,'unmatched':[k for k,v in groups.items() if not v],'unmatched_target':[str(x) for x in b[p.get('right_id',p['id'])] if str(x) not in {r['target_id'] for r in rows}],
                  'analysis_crs':str(a.crs),'semantics':'distance/undirected chord angle/overlap Hausdorff/attribute evidence; no probability or automatic adoption; projected endpoint correspondence may reject strongly curved alternatives'}


def match_split(left,right,p):
    a,b,factor,rows,groups=candidates(left,right,p);key=p['id']
    if not a.geom_type.eq('LineString').all():raise ValueError('Split requires lines')
    out=[]
    for _,row in a.iterrows():
        g=row.geometry;group=groups[str(row[key])];breaks=sorted({0.,g.length,*[r[k]/factor for r in group for k in ('source_start_m','source_end_m')]})
        for n,(start,end) in enumerate(zip(breaks,breaks[1:])):
            if end-start<=1e-12:continue
            middle=(start+end)*factor/2;targets=sorted(r['target_id'] for r in group if r['source_start_m']<=middle<=r['source_end_m'])
            out.append({'segment_id':json.dumps([str(row[key]),n]),'source_id':str(row[key]),'start_m':start*factor,'end_m':end*factor,'target_ids':json.dumps(targets),'match_status':'unmatched' if not targets else ('ambiguous' if len(targets)>1 else 'candidate'),'geometry':substring(g,start,end)})
    result=gpd.GeoDataFrame(out,geometry='geometry',crs=a.crs)
    for _,row in a.iterrows():
        parts=result[result.source_id==str(row[key])].geometry
        if not math.isclose(parts.length.sum(),row.geometry.length,rel_tol=1e-12,abs_tol=1e-9) or row.geometry.hausdorff_distance(parts.union_all())>1e-9:raise ValueError('Segment reconstruction failed')
    return result,{'status':'candidate','adopted':False,'correspondences':rows,'source_length_m':float(a.length.sum()*factor),'output_length_m':float(result.length.sum()*factor),'semantics':'cuts only source; all alternatives and unmatched intervals retained'}


def match_transfer(left,right,p,hashes):
    if not p.get('approved_by') or p.get('source_sha256')!=hashes[0] or p.get('reference_sha256')!=hashes[1]:raise ValueError('Missing or stale fingerprint-bound approval')
    a,b,factor,rows,groups=candidates(left,right,p);key=p['id'];rkey=p.get('right_id',key)
    selections=p['selections']; source_ids=set(a[key].astype(str))
    if not set(selections)<=source_ids:raise ValueError('Unknown selected source')
    fields=p['transfer_fields']
    if set(fields)&set(a):raise ValueError('Transfer field collision')
    out=a.copy()
    for target,source in fields.items():
        if source not in b or source==b.geometry.name:raise ValueError('Unknown or geometry transfer field')
        out[target]=pd.Series([None]*len(out),dtype=object)
    adopted=[]
    for i,row in a.iterrows():
        oid=str(row[key])
        if oid not in selections:continue
        tid=str(selections[oid]);c=[r for r in groups[oid] if r['target_id']==tid]
        if len(c)!=1:raise ValueError('Approved target is not a current candidate')
        if row.geometry.geom_type=='LineString' and (c[0]['source_start_m']>1e-9 or abs(c[0]['source_end_m']-row.geometry.length*factor)>1e-9):raise ValueError('Partial match must be split and reviewed before attribute transfer')
        ref=b.loc[b[rkey].astype(str)==tid].iloc[0]
        for target,source in fields.items():out.at[i,target]=ref[source]
        adopted.append({'source_id':oid,'target_id':tid,'evidence':c[0]})
    for target in fields:
        out[target]=out[target].convert_dtypes()
        if pd.api.types.is_integer_dtype(out[target].dtype) and out[target].isna().any() and any(abs(int(v))>2**53 for v in out[target].dropna()):raise ValueError('Nullable large integers cannot be safely read back; prepare explicit string field')
    return out,{'adopted':True,'approved_by':p['approved_by'],'adoption_scope':'explicit attribute fields only; geometry unchanged','selections':adopted,'unmatched_or_unselected':sorted(source_ids-set(selections))}


def edge_match(left,right,p):
    key=p['id'];rkey=p.get('right_id',key);stable(left,key);stable(right,rkey)
    (a,b),factor=analysis_frames([left,right],p.get('analysis_crs'));a=a.reset_index(drop=True);b=b.reset_index(drop=True)
    if not a.geom_type.eq('LineString').all() or not b.geom_type.eq('LineString').all():raise ValueError('Endpoint seam candidates require LineString')
    maximum=positive(p,'max_displacement_m',True)/factor;out=a.copy();changes=[];seen=set()
    for selection in p['endpoint_pairs']:
        oid,tid=str(selection['source_id']),str(selection['target_id']);end,tend=selection['source_end'],selection['target_end']
        if end not in (0,-1) or tend not in (0,-1) or (oid,end) in seen:raise ValueError('Unique endpoint selections required')
        seen.add((oid,end));indices=a.index[a[key].astype(str)==oid];refs=b[b[rkey].astype(str)==tid]
        if len(indices)!=1 or len(refs)!=1:raise ValueError('Unknown seam ID')
        i=indices[0];coords=list(out.geometry.iloc[i].coords);target=tuple(refs.iloc[0].geometry.coords[tend]);origin=tuple(a.geometry.iloc[i].coords[end])
        if Point(origin).distance(Point(target))>maximum:raise ValueError('Seam displacement exceeds limit')
        # Junction identity is geometric here: move all coincident source endpoints together.
        for j,g in enumerate(out.geometry):
            xy=list(g.coords)
            for k in (0,-1):
                if tuple(a.geometry.iloc[j].coords[k])==origin:xy[k]=target
            candidate=LineString(xy)
            if candidate.length==0 or not candidate.is_simple:raise ValueError('Seam creates collapsed or non-simple line')
            out.at[j,out.geometry.name]=candidate
        changes.append({'source_id':oid,'target_id':tid,'source_end':end,'target_end':tend})
    # Reject inconsistent approvals and new geometric intersections, including unsplit crossings.
    for selection in p['endpoint_pairs']:
        g=out[out[key].astype(str)==str(selection['source_id'])].iloc[0].geometry
        h=b[b[rkey].astype(str)==str(selection['target_id'])].iloc[0].geometry
        if tuple(g.coords[selection['source_end']])!=tuple(h.coords[selection['target_end']]):raise ValueError('Conflicting shared endpoint targets')
    for i,g in enumerate(out.geometry):
        for j in out.sindex.query(g):
            if j>i and not g.intersection(out.geometry.iloc[j]).equals(a.geometry.iloc[i].intersection(a.geometry.iloc[j])):
                old=a.geometry.iloc[i].intersection(a.geometry.iloc[j]);new=g.intersection(out.geometry.iloc[j])
                # A pre-existing shared endpoint may move; other changes require separate topology review.
                if old.geom_type!='Point' or new.geom_type!='Point' or not all(any(new.equals(Point(h.coords[k])) for k in (0,-1)) for h in (g,out.geometry.iloc[j])):raise ValueError('Seam changes crossing topology')
    return out,{'status':'candidate','adopted':False,'endpoint_pairs':changes,'impacts':impacts(a,out,factor,key),'semantics':'explicit endpoint pairs; coincident source junction endpoints move together; use cleanup_adopt for reviewed geometry'}
