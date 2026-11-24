"""No compiler/P1 parser: byte-exact replay and protected-input accounting."""
import argparse
from collections import Counter
from pathlib import Path
from edgm_second.environment import PROJECT
from edgm_second.aggregate_run import read,save,replay
from edgm_second.active_enrichment import verify_enrichment
from edgm_second.active_oracle import verify_active_oracle
from edgm_second.source_edit import digest


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out-dir',required=True,type=Path)
    root=parser.parse_args().out_dir.resolve();assert root.is_relative_to(PROJECT)
    new=read(root/'candidate_pool.json');combined=read(root/'combined_candidate_pool.json')['valid_candidates']
    frozen=read(root/'selection_before_compilation.json');selection=frozen['actions']
    assert selection==combined
    old=read(PROJECT/'evidence/stage2g/candidate_pool.json')['valid_candidates']
    assert combined[:len(old)]==[dict(a,origin_stage='2G') for a in old]
    assert combined[len(old):]==new['valid_candidates']
    keys=[(a['s_sha256'],a['mr_id'],a['semantic_context_signature']) for a in combined]
    assert len(keys)==len(set(keys)) and len({a['candidate_id'] for a in combined})==len(combined)
    contexts=read(root/'semantic_contexts.json')
    assert contexts['candidate_to_signature']=={a['candidate_id']:a['semantic_context_signature'] for a in combined}
    p0_records=[]
    actual_p0={str(p.resolve()) for p in (root/'enriched').glob('seed_*/*/P0.c')}
    assert actual_p0=={a['p0_file'] for a in new['all_attempts'] if 'p0_file' in a}
    for a in new['all_attempts']:
        if 'p0_file' not in a:continue
        r=read(Path(a['directory'])/'enrichment.json');s=Path(r['s_file']).read_bytes();p0=Path(a['p0_file']).read_bytes()
        assert verify_enrichment(r,s,p0) and digest(p0)==a['P0_id']!=digest(s)
        p0_records.append(dict(candidate_id=a.get('candidate_id'),p0=a['p0_file'],status=a['status'],lineage_replay=True))
    replay_records=[];errors={c:Counter() for c in ('gcc','clang')}
    for i,a in enumerate(selection,1):
        folder=root/'experiment'/f'action_{i:03}';result=read(folder/'result.json')
        assert result['candidate_id']==a['candidate_id']
        admission=read(folder/'P0_validation.json')
        assert admission['sha256']==a['P0_id'] and all(v['returncode']==0 and v['status']=='ok' for v in admission['checks'].values())
        if not (folder/'Oracle.json').exists():
            replay_records.append(dict(index=i,candidate_id=a['candidate_id'],status=result['status'],oracle_present=False));continue
        w=read(folder/'Oracle.json');barrier=read(folder/'oracle_before_compilation.json')
        assert barrier['time_ns']>=frozen['time_ns'] and barrier['persisted_before_P1_compilation'] and barrier['replay']
        assert digest((folder/'Oracle.json').read_bytes())==barrier['oracle_sha256'] and replay(w)
        assert len({w['s_sha256'],w['p0_sha256'],w['p1_sha256']})==3
        validation=read(folder/'P1_validation.json');diagnostics=read(folder/'diagnostics.json')
        for c,process in validation['checks'].items():
            for stream in ('stdout','stderr'):assert (folder/f'{c}.{stream}.txt').read_text()==process[stream]
            assert diagnostics[c]==result['observations'][c]
            errors[c].update(e['category'] for e in diagnostics[c]['errors'])
        replay_records.append(dict(index=i,candidate_id=a['candidate_id'],status=result['status'],oracle_present=True,
            oracle_replay=True,oracle_hash_unchanged=True,compiler_error_counts={c:len(v['errors']) for c,v in diagnostics.items()}))
    manual=[]
    for p in sorted((root/'manual').glob('*/execution/Oracle.json')):
        w=read(p);b=read(p.parent/'oracle_before_compilation.json')
        assert replay(w) and digest(p.read_bytes())==b['oracle_sha256'];manual.append(dict(oracle=str(p),replay=True))
    historical=[]
    for p in sorted((PROJECT/'evidence/stage2g').rglob('Oracle.json')):
        w=read(p);assert verify_active_oracle(w,*[Path(w[k]).read_bytes() for k in ('s_file','p0_file','p1_file')])
        historical.append(dict(oracle=str(p),replay=True))
    save(root/'artifact_replay.json',dict(new_P0=p0_records,real_P1=replay_records,artificial_oracles=manual,
        historical_stage2g_oracles=historical,selection_order_unchanged=True,semantic_dedup_passed=True,
        physical_artifact_accounting_passed=True,compiler_error_categories={c:dict(v) for c,v in errors.items()},
        boundary='fact-certificate/byte-map replay, not full C11 verification'))
    before=read(root/'before_hashes.json');changed=[];unexpected=[]
    allowed={'second_point/README.md','second_point/docs/RESEARCH_DESIGN.md'}
    for p,old_sha in before['files'].items():
        file=PROJECT.parent/p;now=digest(file.read_bytes()) if file.is_file() else None
        if now!=old_sha:
            changed.append(p)
            if p not in allowed:unexpected.append(p)
    seeds=[dict(path=p,sha256=sha,unchanged=digest(Path(p).read_bytes())==sha) for p,sha in before['seeds'].items()]
    assert not unexpected and all(s['unchanged'] for s in seeds)
    save(root/'readonly_check.json',dict(baseline_commit='45775d50667412e2ea71ff862b39fbfeb78ee3d6',
        checked_tracked_files=len(before['files']),changed_existing_files=changed,unexpected_changes=unexpected,
        first_research_point_unchanged=True,historical_evidence_unchanged=True,original_seeds=seeds,
        toolchain_runtime_modified=False,preexisting_deleted_paths_preserved=[p for p,v in before['files'].items() if v is None],passed=True))
    print('All frozen actions, new and historical Oracles, source maps, original seeds and protected files verified.')


if __name__=='__main__':main()
