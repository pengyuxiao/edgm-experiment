"""Bounded stage 2E: manual first, five scans, at most three real mutations."""
import argparse
import difflib
import json
from pathlib import Path
import subprocess
import sys
import time
from .environment import PROJECT, EDGM, inspect_environment, load_config, run_process
from .bitfield_validation import collect_integer_profile, observe_errors
from .mr_member_bitfield_addressability import scan_candidates, build_mutation, verify_bitfield_witness
from .native_bitfield_candidates import scan_native
from .seeds import validate_seed
from .source_edit import digest

MANUAL = '''struct Payload { int value; };
int compute(int input) {
    struct Payload object = {0};
    int *first = &object.value;
    {
        int *second = &object.value;
        return input + *first + *second;
    }
}
int main(void) { return compute(0); }
'''


def save(path, value):
    with Path(path).open('x') as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2)


def isolated_scan(path,runtime,profile_path,timeout=60):
    command=[sys.executable,'-m','edgm_second.bitfield_run','--worker',str(path),
             '--runtime',str(runtime),'--profile',str(profile_path)]
    result=run_process(command,timeout)
    if result['status']=='timeout':return dict(status='SCAN_TIMEOUT',candidate_count=None,worker=result)
    if result['status']!='ok':return dict(status='WORKER_FAILED',candidate_count=None,worker=result)
    try:
        scan=json.loads(result['stdout'])
    except ValueError:return dict(status='WORKER_FAILED',candidate_count=None,worker=result)
    result['stdout']='JSON persisted in scan object'
    return dict(scan,worker=result)


def validate_mutation(p0, output, environment, profile, provenance, admission=None):
    output.mkdir()
    # Keep the same physical P0 (and therefore the original USR), and preserve a
    # byte-identical review copy. Oracle paths refer to the actual parsed file.
    data=p0.read_bytes(); (output/'P0.c').write_bytes(data)
    admission=admission or validate_seed(p0,environment)
    save(output/'P0_validation.json',admission)
    scan,p1,w=build_mutation(p0,output/'P1.c',environment['runtime']['path'],profile,admission,provenance)
    save(output/'scan.json',scan)
    if w is None:return dict(status='REJECTED',scan_status=scan['status'])
    (output/'P1.c').write_bytes(p1)
    (output/'change.diff').write_text(''.join(difflib.unified_diff(data.decode().splitlines(True),
        p1.decode().splitlines(True),fromfile=str(p0),tofile=str(output/'P1.c'))))
    witness_path=output/'Oracle.json'
    with witness_path.open('x') as stream:stream.write(w.to_json())
    witness_hash=digest(witness_path.read_bytes())
    before=time.time_ns()
    save(output/'oracle_before_compilation.json',dict(witness_sha256=witness_hash,
        oracle_saved_before_p1_compile=True,barrier_time_ns=before,
        p1_sha256=digest(p1),replay=verify_bitfield_witness(w,data,p1,Path(provenance['original_path']).read_bytes())))
    result=validate_seed(output/'P1.c',environment)
    observations={}
    for name,process in result['checks'].items():
        (output/(name+'.stderr.txt')).write_text(process['stderr'])
        observations[name]=observe_errors(process,w)
    save(output/'P1_validation.json',result);save(output/'diagnostic_observations.json',observations)
    unchanged=digest(witness_path.read_bytes())==witness_hash
    record=dict(status='VALIDATED' if unchanged and all(x['status']=='TARGET_ERRORS_OBSERVED' for x in observations.values())
                else 'REVIEW_CANDIDATE',p0=str(p0),p1=str(output/'P1.c'),
        p0_sha256=digest(data),p1_sha256=digest(p1),original_sha256=provenance['original_sha256'],
        candidate_fields=scan['candidate_count'],selected_field=w.changed_declaration['field_usr'],
        all_expected_address_points=len(w.affected_uses),observations=observations,
        oracle_unchanged_after_compilation=unchanged,p0_unchanged=p0.read_bytes()==data,
        complexity=w.isolation.get('complexity'),confirmed_bug=False)
    save(output/'result.json',record)
    return record


def run(out):
    require_new=out.resolve().is_relative_to(PROJECT)
    if not require_new:raise ValueError('output must be within second_point')
    out.mkdir()
    env=inspect_environment(load_config());save(out/'environment.json',env)
    if not env['ready']:raise RuntimeError('environment unavailable')
    profile=collect_integer_profile(env);save(out/'integer_profile.json',profile)
    if profile['status']!='VERIFIED':raise RuntimeError('unverified target integer widths')
    # Phase 1 is completed before real seed scans or P1 compilation.
    manual=out/'manual_P0.c';manual.write_text(MANUAL)
    manual_result=validate_mutation(manual,out/'manual',env,profile,
        dict(source_kind='Native',original_path=str(manual.resolve()),original_sha256=digest(manual.read_bytes()),
             original_to_p0=dict(kind='identity',range=[0,len(manual.read_bytes())])))
    if manual_result['status']!='VALIDATED':raise RuntimeError('manual MR failed; no real mutations allowed')
    receipts=json.loads((PROJECT/'evidence/stage2d/experiment/enrichment_records.json').read_text())
    if len(receipts)!=5:raise ValueError('expected exactly five existing stage2d receipts')
    records=[]; mutations=[]
    for index,receipt in enumerate(receipts):
        p0=Path(receipt['enhanced_path']); original=Path(receipt['original_path'])
        if digest(original.read_bytes())!=receipt['original_sha256'] or digest(p0.read_bytes())!=receipt['enhanced_sha256']:
            raise ValueError('stage2d sources changed')
        # Re-scan S with the formal 2E checker before admitting enrichment.
        native=isolated_scan(original,env['runtime']['path'],out/'integer_profile.json')
        save(out/f'native_{index+1:02}.json',native)
        if native['status'] not in ('APPLICABLE','NOT_APPLICABLE'):
            records.append(dict(seed_id=receipt['seed_id'],status='NATIVE_SCAN_FAILED',native=native['status']))
            continue
        chosen=original if native['candidate_count'] else p0
        origin='Native' if chosen==original else 'Enriched'
        scan=native if origin=='Native' else isolated_scan(p0,env['runtime']['path'],out/'integer_profile.json')
        save(out/f'P0_scan_{index+1:02}.json',scan)
        admission=validate_seed(chosen,env);save(out/f'P0_admission_{index+1:02}.json',admission)
        record=dict(seed_id=receipt['seed_id'],original=str(original),enhanced=str(p0),source_kind=origin,
            original_sha256=receipt['original_sha256'],p0_sha256=digest(chosen.read_bytes()),
            native_candidates=native['candidate_count'],scan_status=scan['status'],
            candidate_count=scan['candidate_count'],all_candidates=scan['candidates'] if scan.get('candidates') else [],
            admission_status=admission['status'],status='SCANNED_NOT_SELECTED')
        records.append(record)
        save(out/f'seed_{index+1:02}.json',record)
    # Phase 3 starts only after all five P0 scans/admissions are persisted.
    for index, (record,receipt) in enumerate(zip(records,receipts)):
        original=Path(receipt['original_path']);origin=record.get('source_kind')
        chosen=original if origin=='Native' else Path(receipt['enhanced_path'])
        if record.get('scan_status')=='APPLICABLE' and record.get('admission_status')=='valid' and len(mutations)<3:
            provenance=dict(source_kind=origin,original_path=str(original),original_sha256=receipt['original_sha256'])
            if origin=='Enriched':provenance['enrichment']=receipt
            else:provenance['original_to_p0']=dict(kind='identity',range=[0,len(original.read_bytes())])
            admission=json.loads((out/f'P0_admission_{index+1:02}.json').read_text())
            result=validate_mutation(chosen,out/f'real_{index+1:02}',env,profile,provenance,admission)
            record['status']=result['status'];mutations.append(result)
        elif record.get('scan_status')!='APPLICABLE':record['status']='REJECTED_OR_SCAN_FAILED'
    save(out/'records.json',records)
    summary=dict(manual=manual_result,scanned_original_seeds=len(records),
        native_safe_fields=sum(r.get('native_candidates') or 0 for r in records),
        enhanced_safe_fields=sum((r.get('candidate_count') or 0) for r in records if r.get('source_kind')=='Enriched'),
        enhanced_applicable_seeds=sum(r.get('source_kind')=='Enriched' and (r.get('candidate_count') or 0)>0 for r in records),
        real_mutations=len(mutations),validated_real_mutations=sum(r['status']=='VALIDATED' for r in mutations),
        real_results=mutations,selection='first three applicable distinct seeds in immutable stage2d order',
        stage2d_50_seed_native_zero='historical evidence only; this run re-scanned only the five source seeds')
    save(out/'summary.json',summary)
    hashes=json.loads((PROJECT/'evidence/original_hashes.json').read_text())
    changed=[f for f,h in hashes.items() if not (EDGM/f).is_file() or digest((EDGM/f).read_bytes())!=h]
    seeds=json.loads((PROJECT/'evidence/stage2b/corpus/records.json').read_text())
    changed_seeds=[r['source'] for r in seeds if digest(Path(r['source']).read_bytes())!=r['sha256']]
    save(out/'readonly_check.json',dict(original_files_checked=len(hashes),changed=changed,
        seeds_checked=len(seeds),changed_seeds=changed_seeds))
    print(json.dumps({k:v for k,v in summary.items() if k not in ('manual','real_results')},ensure_ascii=False,indent=2))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--out-dir',type=Path)
    parser.add_argument('--worker',type=Path);parser.add_argument('--runtime');parser.add_argument('--profile',type=Path)
    args=parser.parse_args()
    if args.worker:
        print(json.dumps(scan_candidates(args.worker,args.runtime,json.loads(args.profile.read_text()))))
    elif args.out_dir:run(args.out_dir.resolve())
    else:parser.error('--out-dir required')


if __name__=='__main__':main()
