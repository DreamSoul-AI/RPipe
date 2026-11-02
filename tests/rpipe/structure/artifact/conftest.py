"""Synthetic formal saved records; no scientific execution or source lookup."""
import hashlib
import json
from pathlib import Path

import pytest

from rpipe.structure.control import RunSpec, resolve_control
from rpipe.structure.artifact import io


@pytest.fixture
def saved_run(tmp_path):
    def create(name='study', *, seed=0, metrics=None, history=(1.0,0.5), checkpoint=None,
               source='saved', version='default'):
        output = tmp_path/'output'
        config = resolve_control({'experiment':'saved','seed':seed,
            'data':{'name':'Toy','source':'stub'},'model':{'name':'linear'},
            'algorithm':{'mode':'train'},'system':{'device':'cpu'}}).to_dict()
        files = {'study/study.yaml':hashlib.sha256(source.encode()).hexdigest()}
        digest = hashlib.sha256(json.dumps(sorted(files.items()),separators=(',',':')).encode()).hexdigest()
        spec = RunSpec(config['id'],seed,config,tmp_path/'source'/name,output,digest,version)
        ref = spec.ref
        io.write_config(ref.run_dir/'config.yaml',config)
        attempt = 'a'*32
        result = {'id':ref.id,'identity':ref.identity,'attempt':attempt,'status':'succeeded',
                  'quality':True,'evidence_complete':True,'metrics':{'loss':0.5} if metrics is None else metrics}
        record = {'schema':1,'identity':ref.identity,'attempt':attempt,'owner_token':'b'*32,
                  'phase':'closing','candidate_status':'succeeded','quality':True,
                  'evidence_complete':True,'scientific_result_published':True,
                  'derived_status':'not_run','error':None,'cleanup_errors':[],'cancelled':False}
        io.write_json(ref.result_path,result)
        io.write_json(ref.run_dir/'execution.json',record)
        io.write_json(ref.run_dir/'attempts'/attempt/'outcome.json',record)
        scalar = ref.assets_dir/io.TRACKER_JSONL
        scalar.parent.mkdir(parents=True,exist_ok=True)
        scalar.write_text(''.join(json.dumps({'split':'train','name':'Loss','mean':value,
                                               'optimizer_step':index})+'\n'
                                  for index,value in enumerate(history,1)),encoding='utf-8')
        index_path = ref.study_root/'index.json'
        index = io.read_json(index_path) if index_path.exists() else {
            'schema':3,'provenance':{'files':files,'digest':digest,'version':ref.version},'experiments':[]}
        group = next((group for group in index['experiments'] if group['id']==ref.experiment),None)
        if group is None:
            group = {'id':ref.experiment,'factors':{'algorithm.mode':'train'},'runs':[]}
            index['experiments'].append(group)
        group['runs'].append({'id':ref.id,'run_dir':ref.id,'seed':seed,'train_id':None,'identity':ref.identity})
        io.write_json(index_path,index)
        if checkpoint is not None:
            from rpipe.structure.artifact.checkpoint import save_snapshot
            save_snapshot(ref.assets_dir/io.CHECKPOINTS/'latest.pt',checkpoint)
        return ref
    return create
