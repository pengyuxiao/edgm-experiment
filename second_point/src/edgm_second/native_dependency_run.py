"""Bounded stage 2F reproduction: fixed 50 inputs, one opt-in extension."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import difflib
import json
from pathlib import Path
import sys
import time
from .environment import PROJECT, inspect_environment, load_config, run_process
from .bitfield_validation import collect_integer_profile, observe_errors
from .mr_member_bitfield_addressability import scan_candidates, build_mutation, verify_bitfield_witness
from .native_bitfield_candidates import scan_native
from .scoped_layout import scan_native_scoped_layout
from .native_contexts import extract_contexts
from .seed_enrichment import Context, ParseFailure
from .seeds import validate_seed
from .source_edit import digest

COMPLETE = {'APPLICABLE','NOT_APPLICABLE'}
MANUAL = '''struct Target { int x; };
int compute(int input) {
    struct Target object={0};
    int unrelated[3]={0};
    (void)sizeof(unrelated); (void)sizeof(int); (void)_Alignof(int);
    int *p=&object.x;
    { int *q=&object.x; return input + *p + *q; }
}
int main(void) { return compute(0); }
'''


def save(path,data):
    with Path(path).open('x') as f:json.dump(data,f,ensure_ascii=False,indent=2)


def selection():
    raw=json.loads((PROJECT/'evidence/stage2b/corpus/records.json').read_text())
    if len(raw)!=50:raise ValueError('fixed corpus must have 50 entries')
    result=[]
    for index,r in enumerate(raw,1):
        if digest(Path(r['source']).read_bytes())!=r['sha256'] or r['validation']['status']!='valid':
            raise ValueError('fixed source or historical admission changed')
        result.append(dict(index=index,seed_id=r['seed_id'],source=r['source'],sha256=r['sha256']))
    return result


def worker(path,runtime,profile,phase):
    discovery=scan_native(path,runtime)
    if phase=='baseline':return dict(formal=scan_candidates(path,runtime,profile),discovery=discovery)
    if discovery['status'] not in COMPLETE:return dict(formal=discovery,discovery=discovery,contexts=[])
    try:
        ctx=Context(path,runtime)
        scoped=scan_native_scoped_layout(path,runtime,context=ctx,legacy=discovery)
        formal=scan_candidates(path,runtime,profile,native_scanner=lambda p,r:scoped)
        contexts=extract_contexts(path,runtime,formal,scoped,context=ctx) if formal['status'] in COMPLETE else []
        return dict(formal=formal,discovery=scoped,contexts=contexts,legacy_reasons=discovery)
    except ParseFailure as exc:
        return dict(formal=dict(status='PARSE_FAILED',candidate_count=None,diagnostics=exc.args[0]),discovery=discovery,contexts=[])
    except Exception as exc:
        return dict(formal=dict(status='SCAN_FAILED',candidate_count=None,exception=f'{type(exc).__name__}: {exc}'),
                    discovery=discovery,contexts=[])


def isolated(item,runtime,profile_path,phase,timeout):
    command=[sys.executable,'-m','edgm_second.native_dependency_run','--worker',item['source'],
        '--runtime',runtime,'--profile',str(profile_path),'--phase',phase]
    process=run_process(command,timeout)
    if process['status']=='timeout':status='SCAN_TIMEOUT'
    elif process['status']!='ok':status='WORKER_FAILED'
    else:status=None
    try:
        data=json.loads(process['stdout']) if status is None else None
    except ValueError:status='WORKER_FAILED';data=None
    if data is None:data=dict(formal=dict(status=status,candidate_count=None),discovery=dict(status=status,candidate_count=None),contexts=[])
    process['stdout']='structured JSON is persisted in this scan record' if status is None else process['stdout']
    return dict(data,**item,worker=process)


def summarize(records):
    valid=[r for r in records if r['formal']['status'] in COMPLETE]
    observed=[r for r in records if r['discovery']['status'] in COMPLETE]
    fields=[(r,e) for r in observed for e in r['discovery']['candidates']+r['discovery']['rejected_fields']]
    return dict(total_seeds=len(records),formal_status_counts=dict(Counter(r['formal']['status'] for r in records)),
        formal_completed_seeds=len(valid),effective_denominator=len(valid),
        ast_success_seeds=sum(r['discovery'].get('ast_status')=='OK' for r in records),
        discovery_status_counts=dict(Counter(r['discovery']['status'] for r in records)),
        safe_fields=sum(r['formal']['candidate_count'] for r in valid),
        applicable_seeds=sum(r['formal']['candidate_count']>0 for r in valid),
        applicability_rate=sum(r['formal']['candidate_count']>0 for r in valid)/len(valid) if valid else None,
        raw_address_relation_instances=sum(r['discovery'].get('raw_address_relation_count',0) for r in observed),
        address_seeds=sum(r['discovery'].get('raw_address_relation_count',0)>0 for r in observed),
        distinct_main_file_fields=len(fields),canonical_basic_type_fields=dict(Counter(e['canonical_type'] for _,e in fields)),
        overlapping_discovery_reason_field_counts=dict(Counter(reason for _,e in fields for reason in e.get('reasons',[]))),
        formal_first_reason_field_counts=dict(Counter(e['reason'] for r in valid for e in r['formal']['rejected_fields'])),
        per_seed=[dict(seed_id=r['seed_id'],status=r['formal']['status'],candidate_count=r['formal']['candidate_count']) for r in records],
        counting='unique field = source SHA256 + canonical USR; overlapping reasons are not a partition; failure count stays null')


def validate_one(path,out,env,profile):
    out.mkdir();data=path.read_bytes()
    with (out/'P0.c').open('xb') as f:f.write(data)
    admission=validate_seed(path,env);save(out/'P0_validation.json',admission)
    if admission['status']!='valid':return dict(status='P0_ADMISSION_FAILED',admission=admission)
    scan,p1,witness=build_mutation(path,out/'P1.c',env['runtime']['path'],profile,admission,
        dict(source_kind='Native',original_path=str(path),original_sha256=digest(data)),native_scanner=scan_native_scoped_layout)
    save(out/'scan.json',scan)
    if witness is None:return dict(status='REJECTED',scan_status=scan['status'])
    with (out/'P1.c').open('xb') as f:f.write(p1)
    with (out/'change.diff').open('x') as f:f.write(''.join(difflib.unified_diff(data.decode().splitlines(True),p1.decode().splitlines(True),fromfile=str(path),tofile=str(out/'P1.c'))))
    with (out/'Oracle.json').open('x') as f:f.write(witness.to_json())
    sha=digest((out/'Oracle.json').read_bytes())
    save(out/'oracle_before_compilation.json',dict(oracle_sha256=sha,persisted_before_p1_compile=True,
        barrier_time_ns=time.time_ns(),independent_replay=verify_bitfield_witness(witness,data,p1,data)))
    validation=validate_seed(out/'P1.c',env);save(out/'P1_validation.json',validation)
    observations={}
    for name,process in validation['checks'].items():
        for stream in ('stdout','stderr'):
            with (out/f'{name}.{stream}.txt').open('x') as f:f.write(process[stream])
        observations[name]=observe_errors(process,witness)
    save(out/'diagnostics.json',observations)
    unchanged=digest((out/'Oracle.json').read_bytes())==sha
    replay=verify_bitfield_witness(json.loads((out/'Oracle.json').read_text()),data,p1,data)
    contexts=extract_contexts(path,env['runtime']['path'],scan,scan_native_scoped_layout(path,env['runtime']['path']))
    save(out/'semantic_contexts.json',contexts)
    result=dict(status='VALIDATED' if unchanged and all(v['status']=='TARGET_ERRORS_OBSERVED' for v in observations.values()) else 'REVIEW_CANDIDATE',
        p0_sha256=digest(data),p1_sha256=digest(p1),all_expected_addresses=len(witness.affected_uses),observations=observations,
        oracle_unchanged=unchanged,independent_replay=replay,original_unchanged=path.read_bytes()==data,confirmed_bug=False)
    save(out/'result.json',result);return result


def run(out,phase,timeout=300):
    if not out.resolve().is_relative_to(PROJECT):raise ValueError('output must be within second_point')
    out.mkdir();start=time.perf_counter()
    env=inspect_environment(load_config());save(out/'environment.json',env)
    if not env['ready']:raise RuntimeError('required environment unavailable')
    profile=collect_integer_profile(env);save(out/'integer_profile.json',profile)
    if profile['status']!='VERIFIED':raise RuntimeError('integer model unverified')
    if phase=='manual':
        path=out/'manual_input.c';path.write_text(MANUAL)
        result=validate_one(path,out/'validation',env,profile);save(out/'summary.json',result)
        if result['status']!='VALIDATED':raise RuntimeError('manual validation failed')
        print(json.dumps(result,ensure_ascii=False,indent=2));return
    items=selection();save(out/'selection.json',items);(out/'scans').mkdir();records=[]
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending={pool.submit(isolated,r,env['runtime']['path'],out/'integer_profile.json',phase,timeout):r for r in items}
        for future in as_completed(pending):
            result=future.result()
            if digest(Path(result['source']).read_bytes())!=result['sha256']:raise ValueError('source changed during scan')
            save(out/'scans'/f"seed_{result['index']:03}.json",result);records.append(result)
            print(f"completed {len(records)}/50 {result['seed_id']} {result['formal']['status']}",flush=True)
    records.sort(key=lambda r:r['index']);summary=summarize(records);summary['elapsed_seconds']=time.perf_counter()-start
    if phase=='extended':
        # All 50 scans finish before selection. No fallback enrichment; at most
        # one field per seed and three distinct seeds in original fixed order.
        selected=[r for r in records if r['formal']['status']=='APPLICABLE'][:3]
        real=[]
        for r in selected:real.append(dict(seed_id=r['seed_id'],result=validate_one(Path(r['source']),out/f"real_{r['index']:03}",env,profile)))
        summary.update(real_native_p1_count=len(real),real_results=real,selection_rule='first up to three applicable distinct seeds in immutable stage2b order',new_enriched_programs=0)
    save(out/'summary.json',summary);print(json.dumps(summary,ensure_ascii=False,indent=2))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--phase',choices=('baseline','manual','extended'),required=True)
    p.add_argument('--out-dir',type=Path);p.add_argument('--timeout',type=float,default=300)
    p.add_argument('--worker');p.add_argument('--runtime');p.add_argument('--profile',type=Path)
    args=p.parse_args()
    if args.worker:print(json.dumps(worker(args.worker,args.runtime,json.loads(args.profile.read_text()),args.phase)))
    elif args.out_dir:run(args.out_dir.resolve(),args.phase,args.timeout)
    else:p.error('--out-dir required')


if __name__=='__main__':main()
