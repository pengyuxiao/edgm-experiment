"""Stage 2G bounded preparation (<=6 P0 per seed) and 30 stratified trials."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor,as_completed
import difflib
import json
from pathlib import Path
import sys
import time
from .environment import PROJECT,run_process,inspect_environment,load_config
from .bitfield_validation import collect_integer_profile
from .active_enrichment import FAMILIES,enumerate_positions,plan_enrichment,verify_enrichment
from .active_oracle import scan_prepared,build_oracle,verify_active_oracle
from .active_validation import observe_active
from .bitfield_validation import observe_errors
from .source_edit import digest
from .seeds import validate_seed


def save(path,data):
    with Path(path).open('x') as f:json.dump(data,f,ensure_ascii=False,indent=2)


def initialize(root):
    """Create a fresh evidence root; no original source or environment writes."""
    root.mkdir(parents=True,exist_ok=False)
    env=inspect_environment(load_config());save(root/'environment.json',env)
    if not env['ready']:raise ValueError('PINNED_ENVIRONMENT_NOT_READY')
    save(root/'integer_profile.json',collect_integer_profile(env))
    repo=PROJECT.parent
    tracked=run_process(['git','-C',str(repo),'ls-files','-z'],10)
    if tracked['status']!='ok':raise ValueError('TRACKED_BASELINE_UNAVAILABLE')
    files={f:digest((repo/f).read_bytes()) if (repo/f).is_file() else None
           for f in tracked['stdout'].split('\0') if f}
    items=json.loads((PROJECT/'evidence/stage2b/corpus/records.json').read_text())
    assert len(items)==50
    snapshots=root/'source_snapshots';snapshots.mkdir();index=[];seeds={}
    for i,item in enumerate(items,1):
        source=Path(item['source']);data=source.read_bytes()
        if digest(data)!=item['sha256']:raise ValueError('FIXED_SOURCE_CHANGED')
        target=snapshots/f'seed_{i:03}.c'
        with target.open('xb') as f:f.write(data)
        seeds[str(source)]=digest(data)
        index.append(dict(index=i,seed_id=item['seed_id'],source=str(source),sha256=digest(data),snapshot=str(target)))
    save(root/'before_hashes.json',dict(files=files,seeds=seeds))
    save(root/'source_snapshot_index.json',index)


def prepare_seed(item,root):
    env=json.loads((root/'environment.json').read_text());profile=json.loads((root/'integer_profile.json').read_text())
    out=root/'enriched'/f"seed_{item['index']:03}";out.mkdir()
    path=Path(item['source']);s=path.read_bytes()
    if digest(s)!=item['sha256']:raise ValueError('FIXED_SOURCE_CHANGED')
    ctx,positions,reasons=enumerate_positions(path,env['runtime']['path'])
    save(out/'positions.json',dict(positions=positions,rejected_parameter_reasons=reasons))
    records=[]
    for family in FAMILIES:
        eligible=[p for p in positions if (family!='typedef_callback' or p['kind']=='INT') and (family!='nested_two_addresses' or p['nested_supported'])]
        # Fixed, pre-feedback rule: prefer non-main, then source position; prefer
        # a different function for the second position if one is available.
        chosen=eligible[:1]
        if chosen:
            rest=[p for p in eligible[1:] if p['function_usr']!=chosen[0]['function_usr']]
            if rest:chosen.append(rest[0])
            elif len(eligible)>1:chosen.append(eligible[1])
        for rank,position in enumerate(chosen):
            folder=out/f'{family}_{rank:02}';folder.mkdir()
            result=dict(seed_id=item['seed_id'],seed_index=item['index'],source_kind='Enriched',s_file=str(path),s_sha256=digest(s),
                position_id=position['position_id'],mr_id='MR_TYPEDEF_INDIRECT_STRUCT_ARG' if family=='typedef_callback' else 'MR_MEMBER_BITFIELD_ADDRESSABILITY',
                enrichment_family=family,status='ENRICHMENT_FAILED',directory=str(folder))
            try:
                enrichment,data=plan_enrichment(ctx,position,family);verify_enrichment(enrichment,s,data)
                p0=folder/'P0.c'
                with p0.open('xb') as f:f.write(data)
                save(folder/'enrichment.json',enrichment)
                with (folder/'S_to_P0.diff').open('x') as f:f.write(''.join(difflib.unified_diff(s.decode().splitlines(True),data.decode().splitlines(True),fromfile=str(path),tofile=str(p0))))
                result.update(status='P0_INVALID',P0_id=digest(data),p0_file=str(p0),
                    semantic_context_signature=enrichment['semantic_context_signature'],semantic_context=enrichment['semantic_context'])
                admission=validate_seed(p0,env);save(folder/'P0_validation.json',admission)
                if admission['status']=='valid':
                    result['status']='MR_NOT_APPLICABLE'
                    scan=scan_prepared(p0,enrichment,env['runtime']['path'],profile);save(folder/'formal_scan.json',scan)
                    if scan['status']=='APPLICABLE':
                        result.update(status='VALID_CANDIDATE',formal_candidates=scan['candidates'])
                        result['candidate_id']=digest(json.dumps([item['sha256'],result['P0_id'],result['mr_id'],position['position_id'],family],sort_keys=True).encode())
                else:result['failure_detail']=admission['status']
            except Exception as exc:result.update(failure_detail=f'{type(exc).__name__}: {exc}')
            save(folder/'result.json',result);records.append(result)
    result=dict(**item,position_count=len(positions),rejected_parameter_reasons=reasons,actions=records)
    save(out/'summary.json',result)
    if path.read_bytes()!=s:raise ValueError('ORIGINAL_CHANGED')
    return result


def prepare(root):
    (root/'enriched').mkdir()
    raw=json.loads((PROJECT/'evidence/stage2b/corpus/records.json').read_text());assert len(raw)==50
    items=[dict(index=i+1,seed_id=r['seed_id'],source=r['source'],sha256=r['sha256']) for i,r in enumerate(raw)]
    for item in items:
        if digest(Path(item['source']).read_bytes())!=item['sha256']:raise ValueError('FIXED_SOURCE_CHANGED')
    save(root/'selection.json',items)
    save(root/'policy.json',dict(maximum_positions_per_family_per_seed=2,families=list(FAMILIES),
        position_order='non-main then byte offset; second prefers a distinct function',
        p1_selection='10 distinct seeds per family; family i rotates fixed seed order by i*10, first position per seed; no diagnostic-dependent replacement',
        p1_limit=30,expected_seed_count=50,original_seed_generation=False))
    results=[]
    def one(item):
        command=[sys.executable,'-m','edgm_second.active_run','--phase','worker','--index',str(item['index']),'--out-dir',str(root)]
        process=run_process(command,300)
        if process['status']=='ok':
            try:r=json.loads(process['stdout'])
            except ValueError:r=dict(**item,status='WORKER_FAILED',actions=[],stdout=process['stdout'])
        else:r=dict(**item,status='SCAN_TIMEOUT' if process['status']=='timeout' else 'WORKER_FAILED',actions=[])
        process['stdout']='see seed summary' if process['status']=='ok' else process['stdout'];r['worker']=process
        return r
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending=[pool.submit(one,item) for item in items]
        for future in as_completed(pending):
            result=future.result();results.append(result)
            save(root/'enriched'/f"worker_{result['index']:03}.json",result)
            print(f"prepared {len(results)}/50 {result['seed_id']} valid={sum(r['status']=='VALID_CANDIDATE' for r in result['actions'])}",flush=True)
    results.sort(key=lambda r:r['index']);actions=[a for r in results for a in r['actions']]
    valid=[a for a in actions if a['status']=='VALID_CANDIDATE']
    ids=[a['candidate_id'] for a in valid];assert len(ids)==len(set(ids))
    save(root/'candidate_pool.json',dict(schema='stage2g-pool-v1',valid_candidates=valid,all_attempts=actions,
        distinction='candidate identity includes source/position/MR/family; semantic_context_signature deliberately excludes generated names and source coordinates'))
    save(root/'semantic_contexts.json',dict(families=list(FAMILIES),signatures={a['semantic_context_signature']:a['semantic_context'] for a in valid},
        candidate_to_signature={a['candidate_id']:a['semantic_context_signature'] for a in valid}))
    summary=dict(original_seed_count=50,completed_workers=sum('status' not in r for r in results),
        worker_failures=[dict(seed_id=r['seed_id'],status=r['status']) for r in results if 'status' in r],
        seeds_with_valid_enrichment=len({a['seed_id'] for a in valid}),position_count=sum(r.get('position_count',0) for r in results),
        attempted_P0=len(actions),legal_P0=sum(a['status'] in ('VALID_CANDIDATE','MR_NOT_APPLICABLE') for a in actions),
        valid_actions=len(valid),mr_counts=dict(Counter(a['mr_id'] for a in valid)),family_counts=dict(Counter(a['enrichment_family'] for a in valid)),
        unique_semantic_signatures=len({a['semantic_context_signature'] for a in valid}),
        seeds_with_multiple_actions=sum(n>1 for n in Counter(a['seed_id'] for a in valid).values()),
        original_function_roles=dict(Counter(a['semantic_context']['function_role'] for a in valid)),
        original_input_types=dict(Counter(a['semantic_context']['input_type'] for a in valid)),
        original_operators=dict(Counter(a['semantic_context']['original_operator'] for a in valid)),
        status_counts=dict(Counter(a['status'] for a in actions)))
    save(root/'enrichment_summary.json',summary);print(json.dumps(summary,ensure_ascii=False,indent=2))


def freeze_pool(root):
    """Conservative semantic dedup within each original seed before P1 feedback.

    Distinct S remain distinct test inputs, but names/columns/position hashes
    alone cannot multiply identical MR-context actions within the same S.
    All independently validated P0 and excluded entries remain inspectable.
    """
    if (root/'semantic_dedup.json').exists():return
    original=json.loads((root/'candidate_pool.json').read_text())
    if original.get('origin')=='controlled artificial fixtures, excluded from real corpus counts':return
    unique=[];duplicates=[];seen=set()
    for action in original['valid_candidates']:
        key=(action['s_sha256'],action['mr_id'],action['semantic_context_signature'])
        if key in seen:duplicates.append(action)
        else:seen.add(key);unique.append(action)
    (root/'candidate_pool.json').rename(root/'candidate_pool_before_semantic_dedup.json')
    save(root/'candidate_pool.json',dict(original,valid_candidates=unique,excluded_context_duplicates=duplicates,
        semantic_dedup_rule='within S: MR_id + normalized semantic_context_signature; first fixed-order representative'))
    contexts=json.loads((root/'semantic_contexts.json').read_text())
    contexts['candidate_to_signature']={a['candidate_id']:a['semantic_context_signature'] for a in unique}
    contexts['excluded_candidate_to_signature']={a['candidate_id']:a['semantic_context_signature'] for a in duplicates}
    (root/'semantic_contexts.json').rename(root/'semantic_contexts_before_semantic_dedup.json')
    save(root/'semantic_contexts.json',contexts)
    summary=json.loads((root/'enrichment_summary.json').read_text())
    summary.update(raw_formally_valid_actions=len(original['valid_candidates']),excluded_context_duplicates=len(duplicates),
        valid_actions=len(unique),mr_counts=dict(Counter(a['mr_id'] for a in unique)),family_counts=dict(Counter(a['enrichment_family'] for a in unique)),
        seeds_with_multiple_actions=sum(n>1 for n in Counter(a['seed_id'] for a in unique).values()),
        original_function_roles=dict(Counter(a['semantic_context']['function_role'] for a in unique)),
        original_input_types=dict(Counter(a['semantic_context']['input_type'] for a in unique)),
        original_operators=dict(Counter(a['semantic_context']['original_operator'] for a in unique)))
    (root/'enrichment_summary.json').rename(root/'enrichment_summary_before_semantic_dedup.json');save(root/'enrichment_summary.json',summary)
    save(root/'semantic_dedup.json',dict(rule='S SHA + MR + normalized semantic signature',before=len(original['valid_candidates']),after=len(unique),
        excluded_ids=[a['candidate_id'] for a in duplicates],frozen_before_P1_compilation=True,time_ns=time.time_ns()))


def verify_pool(root):
    freeze_pool(root)
    out=root/'experiment';out.mkdir();env=json.loads((root/'environment.json').read_text());profile=json.loads((root/'integer_profile.json').read_text())
    pool=json.loads((root/'candidate_pool.json').read_text())['valid_candidates'];selection=[]
    for number,family in enumerate(FAMILIES):
        options=sorted([a for a in pool if a['enrichment_family']==family],key=lambda a:(a['seed_index'],a['position_id']))
        per_seed={}
        for a in options:per_seed.setdefault(a['seed_index'],a)
        indices=sorted(per_seed);offset=(number*10)%len(indices) if indices else 0
        order=indices[offset:]+indices[:offset]
        selection.extend(per_seed[i] for i in order[:10])
    save(out/'selection_before_compilation.json',selection)
    results=[]
    for index,a in enumerate(selection,1):
        folder=out/f'action_{index:03}';folder.mkdir();p0=Path(a['p0_file']);p1path=folder/'P1.c'
        enrichment=json.loads((Path(a['directory'])/'enrichment.json').read_text());admission=json.loads((Path(a['directory'])/'P0_validation.json').read_text())
        record=dict(candidate_id=a['candidate_id'],seed_id=a['seed_id'],family=a['enrichment_family'],mr_id=a['mr_id'],status='ORACLE_FAILED')
        try:
            oracle,p1=build_oracle(p0,p1path,enrichment,env['runtime']['path'],profile,admission)
            if p1 is None:record['detail']=oracle
            else:
                with p1path.open('xb') as f:f.write(p1)
                save(folder/'Oracle.json',oracle);oracle_hash=digest((folder/'Oracle.json').read_bytes())
                save(folder/'oracle_before_compilation.json',dict(oracle_sha256=oracle_hash,persisted_before_P1_compilation=True,
                    time_ns=time.time_ns(),independent_replay=verify_active_oracle(oracle,Path(oracle['s_file']).read_bytes(),p0.read_bytes(),p1)))
                with (folder/'P0_to_P1.diff').open('x') as f:f.write(''.join(difflib.unified_diff(p0.read_text().splitlines(True),p1.decode().splitlines(True),fromfile=str(p0),tofile=str(p1path))))
                validation=validate_seed(p1path,env);save(folder/'P1_validation.json',validation);observations={}
                for name,process in validation['checks'].items():
                    for stream in ('stdout','stderr'):
                        with (folder/f'{name}.{stream}.txt').open('x') as f:f.write(process[stream])
                    observations[name]=observe_active(process,oracle['formal_witness'])
                save(folder/'diagnostics.json',observations)
                replay=verify_active_oracle(json.loads((folder/'Oracle.json').read_text()),Path(oracle['s_file']).read_bytes(),p0.read_bytes(),p1)
                unchanged=digest((folder/'Oracle.json').read_bytes())==oracle_hash
                execution_failure=any(o['status'] in ('COMPILER_TIMEOUT','COMPILER_CRASH','EXECUTION_FAILED') for o in observations.values())
                outcome=('COMPILER_EXECUTION_FAILURE' if execution_failure else
                         'VALIDATED' if replay and unchanged and all(o['status']=='TARGET_ERRORS_OBSERVED' for o in observations.values()) else 'DIAGNOSTIC_REVIEW_CANDIDATE')
                record.update(status=outcome,
                    p0_file=str(p0),p1_file=str(p1path),p0_sha256=digest(p0.read_bytes()),p1_sha256=digest(p1),
                    observations=observations,oracle_replay=replay,oracle_unchanged=unchanged,confirmed_bug=False)
        except Exception as exc:record['detail']=f'{type(exc).__name__}: {exc}'
        save(folder/'result.json',record);results.append(record)
        print(f"verified {index}/{len(selection)} {a['enrichment_family']} {record['status']}",flush=True)
    summary=dict(selected_actions=len(selection),selected_seeds=len({a['seed_id'] for a in selection}),
        actual_P1_count=sum((out/f'action_{i:03}'/'P1.c').exists() for i in range(1,len(selection)+1)),
        status_counts=dict(Counter(r['status'] for r in results)),family_counts=dict(Counter(r['family'] for r in results)),
        compiler_status_counts={name:dict(Counter(r.get('observations',{}).get(name,{}).get('status','NOT_COMPILED') for r in results)) for name in ('gcc','clang')},
        results=results,confirmed_bugs=0)
    save(out/'summary.json',summary);print(json.dumps({k:v for k,v in summary.items() if k!='results'},ensure_ascii=False,indent=2))


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--phase',choices=('init','prepare','verify','worker'),required=True)
    parser.add_argument('--out-dir',type=Path,required=True);parser.add_argument('--index',type=int)
    args=parser.parse_args();root=args.out_dir.resolve()
    if not root.is_relative_to(PROJECT):parser.error('output must be within second_point')
    if args.phase=='init':initialize(root)
    elif args.phase=='prepare':prepare(root)
    elif args.phase=='verify':verify_pool(root)
    else:
        items=json.loads((root/'selection.json').read_text());print(json.dumps(prepare_seed(items[args.index-1],root)))


if __name__=='__main__':main()
