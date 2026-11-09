"""Stage 2D bounded experiment. All outputs exclusive-create; originals read-only."""
import argparse
from collections import Counter
import csv
import difflib
import json
from pathlib import Path
import subprocess
import sys
import time
from .environment import PROJECT, EDGM, load_config, inspect_environment, run_process
from .source_edit import digest
from .seeds import validate_seed
from .native_bitfield_candidates import scan_native
from .seed_enrichment import enrich_seed


def save(path,value):
    with Path(path).open('x') as stream:json.dump(value,stream,ensure_ascii=False,indent=2)


def isolated(action, path, runtime, output=None, timeout=45):
    command=[sys.executable,'-m','edgm_second.enrichment_run','--worker',action,'--seed',str(path),'--runtime',str(runtime)]
    if output:command+=['--output',str(output)]
    start=time.perf_counter()
    try:
        proc=subprocess.run(command,capture_output=True,timeout=timeout)
        if proc.returncode==0:
            try:result=json.loads(proc.stdout)
            except (ValueError,TypeError):result=dict(status='WORKER_FAILED',candidate_count=None)
        else:result=dict(status='WORKER_FAILED',candidate_count=None)
        result['worker']=dict(command=command,returncode=proc.returncode,stderr=proc.stderr.decode(errors='replace'))
    except subprocess.TimeoutExpired as exc:
        result=dict(status='SCAN_TIMEOUT',candidate_count=None,worker=dict(command=command,returncode=None,
            stderr=(exc.stderr or b'').decode(errors='replace'),stdout=(exc.stdout or b'').decode(errors='replace')))
    except OSError as exc:
        result=dict(status='WORKER_FAILED',candidate_count=None,exception=str(exc))
    result['elapsed_seconds']=time.perf_counter()-start
    return result


def dynamic_fixture(original,enhanced,environment,out):
    records={}
    for name in ('gcc','clang'):
        group=[]
        for label,path in [('original',original),('enhanced',enhanced)]:
            binary=out/(name+'_'+label)
            build=run_process([environment['compilers'][name]['path'],'-std=c11','-w',str(path),'-o',str(binary)],20)
            execution=run_process([str(binary)],5) if build['status']=='ok' else None
            group.append(dict(build=build,execution=execution))
        records[name]=dict(runs=group,equal=all(r['execution'] and r['execution']['status']=='ok' for r in group)
            and all(group[0]['execution'][key]==group[1]['execution'][key] for key in ('returncode','stdout','stderr')))
    return records


def main():
    p=argparse.ArgumentParser();p.add_argument('--worker',choices=['native','enrich']);p.add_argument('--seed');p.add_argument('--runtime')
    p.add_argument('--output');p.add_argument('--out-dir',type=Path);p.add_argument('--scan-timeout',type=float,default=45)
    args=p.parse_args()
    if args.worker:
        r=scan_native(args.seed,args.runtime) if args.worker=='native' else enrich_seed(args.seed,args.output,args.runtime)
        print(json.dumps(r,ensure_ascii=False));return
    out=args.out_dir;out.mkdir(parents=True,exist_ok=False)
    config=load_config();environment=inspect_environment(config);save(out/'environment.json',environment)
    if not environment['ready']:raise RuntimeError('existing environment not ready')
    started=time.perf_counter()
    manifest=json.loads((PROJECT/'evidence/stage2b/corpus/records.json').read_text())
    originals=json.loads((PROJECT/'evidence/original_hashes.json').read_text())
    before={str(EDGM/k):digest((EDGM/k).read_bytes()) for k in originals}
    # Minimal sample must pass before any real-corpus experiment.
    source=PROJECT/'examples/stage2d/original.c'; enhanced=out/'minimal_enriched.c'
    minimal=enrich_seed(source,enhanced,config['runtime'])
    minimal['original_validation']=validate_seed(source,environment)
    if enhanced.exists():minimal['enhanced_validation']=validate_seed(enhanced,environment)
    minimal['dynamic']=dynamic_fixture(source,enhanced,environment,out) if enhanced.exists() else {}
    save(out/'minimal_validation.json',minimal)
    if minimal['status']!='ENRICHED_UNVALIDATED' or minimal.get('enhanced_validation',{}).get('status')!='valid' or not all(v['equal'] for v in minimal['dynamic'].values()):
        raise RuntimeError('minimal validation failed: no real experiment started')
    records=[]
    for index, item in enumerate(manifest,1):
        path=Path(item['source']);sha=digest(path.read_bytes())
        record=dict(seed_id=item['seed_id'],source=str(path),sha256=sha,
                    stage2b_legality=item['validation']['status'])
        if sha!=item['sha256']:
            record['native']=dict(status='SOURCE_CHANGED',candidate_count=None)
        else:record['native']=isolated('native',path,config['runtime'],timeout=args.scan_timeout)
        record['source_unchanged']=digest(path.read_bytes())==sha
        records.append(record);save(out/f'native_{index:03d}.json',record)
        print(json.dumps(dict(progress=index,seed=item['seed_id'],status=record['native']['status'],candidates=record['native']['candidate_count'])),flush=True)
    save(out/'native_records.json',records)
    enhancements=[]
    # Exactly first five fixed-manifest distinct sources, no success-based selection.
    for item in manifest[:5]:
        path=Path(item['source']);destination=out/(item['seed_id']+'_P0.c')
        r=isolated('enrich',path,config['runtime'],destination,timeout=args.scan_timeout)
        r['seed_id']=item['seed_id'];r['original_validation']=validate_seed(path,environment)
        if destination.exists():
            r['enhanced_validation']=validate_seed(destination,environment)
            r['diff']=''.join(difflib.unified_diff(path.read_text().splitlines(True),destination.read_text().splitlines(True),
                            fromfile=str(path),tofile=str(destination)))
            (out/(item['seed_id']+'.diff')).write_text(r['diff'])
        r['original_hash_matches_manifest']=digest(path.read_bytes())==item['sha256']
        enhancements.append(r);save(out/(item['seed_id']+'_enrichment.json'),r)
    save(out/'enrichment_records.json',enhancements)
    valid=[r for r in records if r['native']['status'] in ('APPLICABLE','NOT_APPLICABLE') and r['source_unchanged']]
    reasons=Counter();seed_reasons=Counter()
    for r in valid:
        counts=r['native']['rejection_counts'];reasons.update(counts);seed_reasons.update(counts.keys())
    summary=dict(total_seeds=len(records),valid_native_scans=len(valid),
        native_candidate_fields=sum(r['native']['candidate_count'] for r in valid),
        native_candidate_seeds=sum(r['native']['candidate_count']>0 for r in valid),
        native_address_targets=sum(len(c['address_anchors']) for r in valid for c in r['native']['candidates']),
        native_zero_candidate_seeds=sum(r['native']['candidate_count']==0 for r in valid),
        raw_address_relations=sum(r['native']['raw_address_relation_count'] for r in valid),
        rejected_field_reasons=dict(reasons),rejection_seed_counts=dict(seed_reasons),
        native_status_counts=dict(Counter(r['native']['status'] for r in records)),
        enrichment_attempted=len(enhancements),
        enrichment_planned_safe=sum(r['status']=='ENRICHED_UNVALIDATED' for r in enhancements),
        enrichment_dual_valid=sum(r['status']=='ENRICHED_UNVALIDATED' and r.get('enhanced_validation',{}).get('status')=='valid'
                                  and r['original_validation']['status']=='valid' for r in enhancements),
        enrichment_status_counts=dict(Counter(r['status'] for r in enhancements)),
        elapsed_seconds=time.perf_counter()-started,
        candidate_scope='complete only within explicitly documented conservative acceptance subset; not an exhaustive C11 safety decision')
    save(out/'summary.json',summary)
    with (out/'native_records.csv').open('x',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['seed_id','sha256','status','candidate_count','raw_address_relations','elapsed_seconds']);writer.writeheader()
        for r in records:writer.writerow(dict(seed_id=r['seed_id'],sha256=r['sha256'],status=r['native']['status'],
            candidate_count=r['native']['candidate_count'],raw_address_relations=r['native'].get('raw_address_relation_count'),elapsed_seconds=r['native'].get('elapsed_seconds')))
    save(out/'readonly_check.json',dict(original_files_checked=len(before),changed=[p for p,sha in before.items() if not Path(p).exists() or digest(Path(p).read_bytes())!=sha],
        all_fifty_hashes_unchanged=all(digest(Path(r['source']).read_bytes())==r['sha256'] for r in manifest)))
    print(json.dumps(summary,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
