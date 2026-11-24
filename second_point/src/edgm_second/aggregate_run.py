"""Stage 2H: fixed preparation, frozen exhaustive execution, evidence replay."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor,as_completed
import difflib
import json
from pathlib import Path
import sys
import time
from .environment import PROJECT,run_process,inspect_environment,load_config
from .active_run import save
from .active_enrichment import enumerate_positions,verify_enrichment
from .aggregate_enrichment import plan,route,FAMILIES,MR_ID
from .mr_member_const_aggregate_write import scan,build,verify
from .active_oracle import build_oracle,verify_active_oracle
from .aggregate_validation import observe
from .seeds import validate_seed
from .source_edit import digest


def read(p):return json.loads(Path(p).read_text())


def diff(path,a,b,old,new):
    with Path(path).open('x') as f:f.write(''.join(difflib.unified_diff(a.decode().splitlines(True),b.decode().splitlines(True),fromfile=str(old),tofile=str(new))))


def setup(root):
    env=inspect_environment(load_config());save(root/'environment.json',env)
    if not env['ready']:raise ValueError('PINNED_ENVIRONMENT_NOT_READY')
    profile=read(PROJECT/'evidence/stage2g/integer_profile.json');save(root/'integer_profile.json',profile)
    items=read(PROJECT/'evidence/stage2g/selection.json');assert len(items)==50
    for a in items:assert digest(Path(a['source']).read_bytes())==a['sha256']
    if not (root/'before_hashes.json').exists():
        tracked=run_process(['git','-C',str(PROJECT.parent),'ls-files','-z'],10)
        if tracked['status']!='ok':raise ValueError('BASELINE_UNAVAILABLE')
        save(root/'before_hashes.json',dict(files={f:digest((PROJECT.parent/f).read_bytes()) if (PROJECT.parent/f).is_file() else None
            for f in tracked['stdout'].split('\0') if f},seeds={a['source']:a['sha256'] for a in items}))
    save(root/'selection.json',items)
    save(root/'policy.json',dict(maximum_positions_per_family_per_seed=2,position_order='non-main then offset; second prefers different function',
        assignments_by_position_rank=[1,2],families=list(FAMILIES),dedup='S SHA + MR + normalized semantic context signature',
        execution='all stage2g valid candidates in unchanged order, followed by all unique new candidates in seed/family/position order',
        workers=3,worker_timeout_seconds=600,no_diagnostic_dependent_selection=True))


def prepare_seed(root,index):
    item=read(root/'selection.json')[index-1];folder=root/'enriched'/f'seed_{index:03}';folder.mkdir()
    env=read(root/'environment.json');ctx,positions,reasons=enumerate_positions(item['source'],env['runtime']['path'])
    save(folder/'positions.json',dict(positions=positions,rejected_parameter_reasons=reasons));actions=[]
    chosen=positions[:1]
    if chosen:
        rest=[p for p in positions[1:] if p['function_usr']!=chosen[0]['function_usr']]
        if rest:chosen.append(rest[0])
        elif len(positions)>1:chosen.append(positions[1])
    for family in FAMILIES:
        for rank,p in enumerate(chosen):
            out=folder/f'{family}_{rank:02}';out.mkdir();p0=out/'P0.c'
            a=dict(seed_id=item['seed_id'],seed_index=index,s_file=item['source'],s_sha256=item['sha256'],source_kind='Enriched',
                mr_id=MR_ID,enrichment_family=family,position_id=p['position_id'],directory=str(out),origin_stage='2H',status='ENRICHMENT_FAILED')
            try:
                r,data=plan(ctx,p,family,rank+1);verify_enrichment(r,ctx.data,data);p0.write_bytes(data);save(out/'enrichment.json',r)
                a.update(p0_file=str(p0),P0_id=digest(data),semantic_context=r['semantic_context'],semantic_context_signature=r['semantic_context_signature'],status='P0_INVALID')
                diff(out/'S_to_P0.diff',ctx.data,data,item['source'],p0)
                admission=validate_seed(p0,env);save(out/'P0_validation.json',admission)
                if admission['status']=='valid':
                    a['status']='MR_NOT_APPLICABLE';s=scan(p0,env['runtime']['path']);save(out/'formal_scan.json',s)
                    if len(s['candidates'])==1:
                        route(r,p0,env['runtime']['path'],s['candidates'][0]);a['status']='VALID_CANDIDATE'
                        a['candidate_id']=digest(json.dumps([item['sha256'],digest(data),MR_ID,p['position_id'],family],sort_keys=True).encode())
                else:a['detail']=admission['status']
            except Exception as exc:a['detail']=f'{type(exc).__name__}: {exc}'
            save(out/'result.json',a);actions.append(a)
    result=dict(**item,positions=len(positions),actions=actions);save(folder/'summary.json',result);return result


def parallel(root,phase,items):
    results=[]
    def worker(i):
        command=[sys.executable,'-m','edgm_second.aggregate_run','--phase',phase,'--out-dir',str(root),'--index',str(i)]
        process=run_process(command,600)
        if process['status']=='ok':
            try:return json.loads(process['stdout']),dict(process,stdout='see result file')
            except ValueError:pass
        return dict(status='WORKER_TIMEOUT' if process['status']=='timeout' else 'WORKER_FAILED',index=i,worker_failed=True),process
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures={pool.submit(worker,i):i for i in items}
        for future in as_completed(futures):
            i=futures[future];r,process=future.result();save(root/f'{phase}_{i:03}.json',dict(result=r,worker=process));results.append((i,r))
            print(f'{phase} {len(results)}/{len(items)} index={i} status={r.get("status","prepared")}',flush=True)
    return [r for _,r in sorted(results)]


def prepare(root):
    (root/'enriched').mkdir();results=parallel(root,'prepare_worker',list(range(1,51)))
    actions=[a for r in results for a in r.get('actions',[])];valid=[];duplicate=[];seen=set()
    for a in actions:
        if a['status']!='VALID_CANDIDATE':continue
        key=(a['s_sha256'],a['mr_id'],a['semantic_context_signature'])
        if key in seen:duplicate.append(a)
        else:seen.add(key);valid.append(a)
    save(root/'candidate_pool.json',dict(schema='stage2h-pool-v1',valid_candidates=valid,all_attempts=actions,excluded_context_duplicates=duplicate,
        worker_failures=[r for r in results if r.get('worker_failed')]))
    summary=dict(original_seed_count=50,seeds_with_valid_enrichment=len({a['seed_id'] for a in valid}),attempted_P0=len(actions),
        legal_P0=sum(a['status'] in ('VALID_CANDIDATE','MR_NOT_APPLICABLE') for a in actions),valid_actions=len(valid),excluded_duplicates=len(duplicate),
        family_counts=dict(Counter(a['enrichment_family'] for a in valid)),unique_semantic_signatures=len({a['semantic_context_signature'] for a in valid}),
        status_counts=dict(Counter(a['status'] for a in actions)),worker_failures=sum(r.get('worker_failed',False) for r in results))
    save(root/'enrichment_summary.json',summary)
    old=read(PROJECT/'evidence/stage2g/candidate_pool.json')['valid_candidates']
    combined=[dict(a,origin_stage='2G') for a in old]+valid
    assert len({a['candidate_id'] for a in combined})==len(combined)
    save(root/'combined_candidate_pool.json',dict(schema='stage2h-combined-v1',valid_candidates=combined,stage2g_pool_sha256=digest((PROJECT/'evidence/stage2g/candidate_pool.json').read_bytes()),
        mr_counts=dict(Counter(a['mr_id'] for a in combined))))
    save(root/'semantic_contexts.json',dict(signatures={a['semantic_context_signature']:a['semantic_context'] for a in combined},
        candidate_to_signature={a['candidate_id']:a['semantic_context_signature'] for a in combined},new_families=list(FAMILIES)))
    save(root/'selection_before_compilation.json',dict(time_ns=time.time_ns(),actions=combined,rule='exhaustive fixed pool, no feedback filtering'))


def replay(w):
    data=[Path(w[k]).read_bytes() for k in ('s_file','p0_file','p1_file')]
    return verify(w,*data) if w['mr_id']==MR_ID else verify_active_oracle(w,*data)


def execute_action(root,index,a,out):
    out.mkdir();env=read(root/'environment.json');p0=Path(a['p0_file']);p1=out/'P1.c'
    result=dict(candidate_id=a['candidate_id'],seed_id=a['seed_id'],mr_id=a['mr_id'],family=a['enrichment_family'],
        semantic_context_signature=a['semantic_context_signature'],origin_stage=a.get('origin_stage','manual'),status='ORACLE_INVALID')
    try:
        assert digest(p0.read_bytes())==a['P0_id']
        admission=validate_seed(p0,env);save(out/'P0_validation.json',admission)
        if admission['status']!='valid':raise ValueError('CURRENT_P0_ADMISSION_FAILED:'+admission['status'])
        r=read(Path(a['directory'])/'enrichment.json')
        if a['mr_id']==MR_ID:w,data=build(p0,p1,env['runtime']['path'],admission,r)
        else:w,data=build_oracle(p0,p1,r,env['runtime']['path'],read(root/'integer_profile.json'),admission)
        if data is None:raise ValueError('FORMAL_MR_NOT_APPLICABLE:'+str(w))
        p1.write_bytes(data);save(out/'Oracle.json',w)
        oracle_hash=digest((out/'Oracle.json').read_bytes());assert replay(w)
        save(out/'oracle_before_compilation.json',dict(time_ns=time.time_ns(),oracle_sha256=oracle_hash,persisted_before_P1_compilation=True,replay=True))
        diff(out/'P0_to_P1.diff',p0.read_bytes(),data,p0,p1)
        validation=validate_seed(p1,env);save(out/'P1_validation.json',validation);observations={}
        formal=w if a['mr_id']==MR_ID else w['formal_witness']
        for compiler,process in validation['checks'].items():
            for stream in ('stdout','stderr'):(out/f'{compiler}.{stream}.txt').write_text(process[stream])
            observations[compiler]=observe(process,formal)
        save(out/'diagnostics.json',observations)
        assert digest((out/'Oracle.json').read_bytes())==oracle_hash and replay(read(out/'Oracle.json'))
        result.update(status='TARGET_ERRORS_OBSERVED' if all(o['status']=='TARGET_ERRORS_OBSERVED' for o in observations.values()) else 'REVIEW_REQUIRED',
            observations=observations,p0_file=str(p0),p1_file=str(p1),p0_sha256=digest(p0.read_bytes()),p1_sha256=digest(data),oracle_replay=True,oracle_unchanged=True,confirmed_bug=False)
        if result['status']!='TARGET_ERRORS_OBSERVED':
            # Recompile the very same bytes/environment; do not modify the Oracle.
            repeated=validate_seed(p1,env);save(out/'repeat_validation.json',repeated)
            repeated_observations={c:observe(v,formal) for c,v in repeated['checks'].items()};save(out/'repeat_diagnostics.json',repeated_observations)
            failure={'TARGET_ERRORS_OBSERVED','COMPILER_TIMEOUT','COMPILER_CRASH','EXECUTION_FAILED'}
            result['stable_statuses']={c:observations[c]['status'] not in failure and observations[c]==repeated_observations[c] for c in observations}
    except Exception as exc:result['detail']=f'{type(exc).__name__}: {exc}'
    save(out/'result.json',result);return result


def execute(root):
    selection=read(root/'selection_before_compilation.json')['actions'];(root/'experiment').mkdir()
    results=parallel(root,'execute_worker',list(range(1,len(selection)+1)))
    summary=dict(selected_actions=len(selection),actual_P1_count=len(list((root/'experiment').glob('action_*/P1.c'))),
        unique_P1_hashes=len({r['p1_sha256'] for r in results if 'p1_sha256' in r}),
        status_counts=dict(Counter(r['status'] for r in results)),mr_counts=dict(Counter(r['mr_id'] for r in results if 'mr_id' in r)),
        compiler_status_counts={c:dict(Counter(r.get('observations',{}).get(c,{}).get('status',r['status']) for r in results)) for c in ('gcc','clang')},
        results=results,confirmed_bugs=0)
    summary['by_mr']={mr:dict(actions=sum(r.get('mr_id')==mr for r in results),
        compiler_status_counts={c:dict(Counter(r.get('observations',{}).get(c,{}).get('status',r['status']) for r in results if r.get('mr_id')==mr)) for c in ('gcc','clang')},
        error_categories={c:dict(Counter(e['category'] for r in results if r.get('mr_id')==mr for e in r.get('observations',{}).get(c,{}).get('errors',[]))) for c in ('gcc','clang')})
        for mr in sorted({a['mr_id'] for a in selection})}
    save(root/'experiment/summary.json',summary)
    save(root/'diagnostic_review_index.json',dict(reviews=[dict(index=i+1,**r) for i,r in enumerate(results) if r['status']!='TARGET_ERRORS_OBSERVED'],
        stable_diagnostic_candidates=sum(any(r.get('stable_statuses',{}).values()) for r in results),confirmed_bugs=0,
        scope='execution/Oracle failures distinct from diagnostic review; no external bug reports'))


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--phase',required=True,choices=['setup','prepare','prepare_worker','execute','execute_worker'])
    parser.add_argument('--out-dir',required=True,type=Path);parser.add_argument('--index',type=int);args=parser.parse_args();root=args.out_dir.resolve()
    if not root.is_relative_to(PROJECT):parser.error('output must stay within second_point')
    if args.phase=='setup':setup(root)
    elif args.phase=='prepare':prepare(root)
    elif args.phase=='execute':execute(root)
    elif args.phase=='prepare_worker':print(json.dumps(prepare_seed(root,args.index)))
    else:
        a=read(root/'selection_before_compilation.json')['actions'][args.index-1]
        print(json.dumps(execute_action(root,args.index,a,root/'experiment'/f'action_{args.index:03}')))


if __name__=='__main__':main()
