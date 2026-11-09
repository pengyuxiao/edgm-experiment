"""Profile exactly the saved stage-2B source manifest, in bounded worker processes."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import subprocess
import sys
import time
from .environment import PROJECT, EDGM, inspect_environment, load_config
from .source_edit import digest
from .applicability_run import decode, messages


def save(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def isolated_profile(path, runtime, timeout):
    started = time.perf_counter()
    command = [sys.executable, '-m', 'edgm_second.semantic_profile_run', '--worker', '--seed', str(path), '--runtime', str(runtime)]
    process_record = dict(command=command, returncode=None, stderr='')
    try:
        process = subprocess.run(command, capture_output=True, timeout=timeout)
        process_record.update(returncode=process.returncode, stderr=decode(process.stderr))
        events = messages(process.stdout)
        finals = [v['result'] for v in events if v.get('event') == 'result']
        if process.returncode == 0 and len(finals) == 1:
            result = finals[0]
        else:
            result = dict(status='WORKER_FAILED', ast_status='UNKNOWN', stdout=decode(process.stdout))
    except subprocess.TimeoutExpired as exc:
        events = messages(exc.stdout)
        ready = next((e for e in events if e.get('event') == 'ast_ready'), None)
        result = dict(status='PROFILE_TIMEOUT', ast_status='OK' if ready else 'UNKNOWN',
                      phase='relations' if ready else 'parse_or_traversal', partial_stdout=decode(exc.stdout))
        process_record['stderr'] = decode(exc.stderr)
    except OSError as exc:
        result = dict(status='WORKER_FAILED', ast_status='UNKNOWN', exception=str(exc))
    result.update(worker_seconds=time.perf_counter()-started, worker_process=process_record)
    return result


def aggregate(records):
    valid = [r for r in records if r['profile']['status'] == 'PROFILED' and r['profile'].get('source_unchanged')]
    structures, relations, histograms = {}, {}, {}
    for field, output in (('structures', structures), ('relations', relations)):
        keys = sorted({key for r in valid for key in r['profile'][field]})
        for key in keys:
            entries = [r['profile'][field][key] for r in valid]
            output[key] = dict(seed_count=sum(e['instances'] > 0 for e in entries),
                instances=sum(e['instances'] for e in entries),
                dependency_relations=sum(e['instances'] if field == 'relations' else e.get('dependency_relations', 0) for e in entries),
                examples=[dict(seed_id=r['seed_id'], **e) for r in valid for e in r['profile'][field][key]['examples']][:5])
    for r in valid:
        for name, counts in r['profile']['histograms'].items():
            histograms.setdefault(name, Counter()).update(counts)
    return dict(total_seeds=len(records), completed_seeds=len(valid),
        status_counts=dict(Counter(r['profile']['status'] for r in records)),
        main_file_nodes=sum(r['profile']['main_file_nodes'] for r in valid), structures=structures, relations=relations,
        histograms={name:dict(counter) for name,counter in histograms.items()},
        unresolved_operator_nodes=sum(r['profile']['details']['unresolved_operators'] for r in valid),
        confirmed_mr_candidate_count=None, mr_applicability='NOT_ASSESSED')


def semantic_projection(value):
    if isinstance(value, dict):
        return {k:semantic_projection(v) for k,v in value.items() if k not in ('elapsed_seconds','worker_seconds','worker_process')}
    if isinstance(value,list):
        return [semantic_projection(v) for v in value]
    return value


def run(directory, timeout=90):
    directory = Path(directory).resolve()
    if directory.exists() or PROJECT.resolve() not in directory.parents:
        raise ValueError('output must be a new directory in second_point')
    started = time.perf_counter()
    config = load_config()
    environment = inspect_environment(config)
    if not environment['ready']:
        raise RuntimeError('existing environment is not ready')
    old = json.loads((PROJECT/'evidence/stage2b/corpus/records.json').read_text())
    if len(old) != 50 or any(r['validation']['status'] != 'valid' for r in old):
        raise ValueError('expected the existing fifty legal stage-2B sources')
    directory.mkdir(parents=True)
    save(directory/'environment.json', environment)
    save(directory/'selection.json', dict(source_manifest=str(PROJECT/'evidence/stage2b/corpus/records.json'),
        source_manifest_sha256=digest((PROJECT/'evidence/stage2b/corpus/records.json').read_bytes()),
        ordered_sources=[dict(seed_id=r['seed_id'],source=r['source'],sha256=r['sha256']) for r in old],
        legal_admission='reuse stage2B dual-compiler receipts only after exact SHA256 validation; no new P1 compilation', timeout=timeout))
    records = []
    for index, previous in enumerate(old):
        path = Path(previous['source'])
        if digest(path.read_bytes()) != previous['sha256']:
            profile = dict(status='SOURCE_CHANGED', ast_status='NOT_RUN')
        else:
            profile = isolated_profile(path, config['runtime'], timeout)
            if digest(path.read_bytes()) != previous['sha256']:
                profile['status'] = 'SOURCE_CHANGED'
        record = dict(seed_id=previous['seed_id'], source=previous['source'], sha256=previous['sha256'],profile=profile)
        records.append(record)
        save(directory/f'seed_{index+1:03d}.json',record)
        print(json.dumps(dict(index=index+1,total=len(old),seed=path.name,status=profile['status'])),flush=True)
    summary = aggregate(records)
    summary.update(wall_seconds=time.perf_counter()-started, worker_seconds=sum(r['profile'].get('worker_seconds',0) for r in records))
    save(directory/'summary.json',summary)
    save(directory/'records.json',records)
    save(directory/'semantic_results_digest.json',dict(sha256=digest(json.dumps(semantic_projection(records),sort_keys=True).encode())))
    with (directory/'summary.csv').open('x',newline='',encoding='utf-8') as stream:
        writer=csv.writer(stream); writer.writerow(['kind','name','seed_count','instances','dependency_relations'])
        for kind in ('structures','relations'):
            for name,entry in summary[kind].items():
                writer.writerow([kind,name,entry['seed_count'],entry['instances'],entry['dependency_relations']])
    baseline=json.loads((PROJECT/'evidence/original_hashes.json').read_text())
    current={str(p.relative_to(EDGM)):digest(p.read_bytes()) for tree in ('scripts','src/edgm','config','tests','data/seeds') for p in (EDGM/tree).rglob('*') if p.is_file()}
    check=dict(checked_files=len(baseline),changed_or_deleted=[p for p,h in baseline.items() if current.get(p)!=h],added=sorted(set(current)-set(baseline)))
    save(directory/'readonly_check.json',check)
    if check['changed_or_deleted'] or check['added']:
        raise RuntimeError('protected EDGM files changed')
    print(json.dumps({k:v for k,v in summary.items() if k not in ('structures','relations','histograms')},ensure_ascii=False))
    return 0 if summary['completed_seeds']==50 else 1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker',action='store_true'); parser.add_argument('--seed',type=Path);parser.add_argument('--runtime')
    parser.add_argument('--out-dir',type=Path);parser.add_argument('--timeout',type=float,default=90)
    args=parser.parse_args()
    if args.worker:
        from .semantic_profile import profile_file
        result=profile_file(args.seed,args.runtime,progress=lambda event:print(json.dumps(event),flush=True))
        print(json.dumps(dict(event='result',result=result)),flush=True)
        return 0
    if not args.out_dir or args.timeout<=0:
        parser.error('--out-dir and positive timeout required')
    return run(args.out_dir,args.timeout)


if __name__=='__main__':
    raise SystemExit(main())
