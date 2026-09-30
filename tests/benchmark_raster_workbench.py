"""Deterministic microbenchmark; no model inference or image downloads."""
import argparse
import importlib.util
import json
from pathlib import Path
import statistics
import sys
import time

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0,str(SCRIPTS))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--script',type=Path,default=SCRIPTS/'raster-annotate.py')
    parser.add_argument('--repeats',type=int,default=3)
    args=parser.parse_args()
    if args.repeats<1:parser.error('repeats must be positive')
    spec=importlib.util.spec_from_file_location('workbench',args.script)
    a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
    state={g:{} for g in a.GROUPS};state.update(revision=0,crossings=[])
    for i in range(500):
        x,y=(i%25)*30,(i//25)*30
        state['nodes'][f'a{i}']={'xy':[x,y]};state['nodes'][f'b{i}']={'xy':[x+10,y+10]}
        state['edges'][f'e{i}']={'start':f'a{i}','end':f'b{i}','status':'visible','kind':'synthetic','geometry':{'type':'polyline'}}
    def measure(fn):
        samples=[]
        for _ in range(args.repeats):
            t=time.perf_counter();fn();samples.append(time.perf_counter()-t)
        return {'median_seconds':statistics.median(samples),'samples':samples}
    print(json.dumps({'repeats':args.repeats,'scope':'in-process compute only; excludes imports, model, downloads and export',
          'disjoint_500_diagnose':measure(lambda:a.diagnose(state,1000,1000)),
          'cubic_200':measure(lambda:[a.flatten_cubic([0,0],[0,100],[100,100],[100,0],.25) for _ in range(200)])}))

if __name__=='__main__':main()
