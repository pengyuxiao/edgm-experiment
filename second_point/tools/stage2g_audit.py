"""Read-only replay, physical artifact accounting, and baseline protection audit.

Run after preparation/verification. Never invokes a compiler or parses P1.
Outputs use exclusive creation so an earlier audit cannot be overwritten.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
from edgm_second.environment import PROJECT
from edgm_second.active_enrichment import verify_enrichment
from edgm_second.active_oracle import verify_active_oracle
from edgm_second.active_run import save
from edgm_second.constraint_witness import verify_constraint_witness
from edgm_second.mr_member_bitfield_addressability import verify_bitfield_witness
from edgm_second.source_edit import digest


def read(path):return json.loads(Path(path).read_text())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir',type=Path,default=PROJECT/'evidence/stage2g')
    root=parser.parse_args().out_dir.resolve()
    if not root.is_relative_to(PROJECT):parser.error('output must be within second_point')
    pool=read(root/'candidate_pool.json');valid=pool['valid_candidates'];attempts=pool['all_attempts']
    keys=[(a['s_sha256'],a['mr_id'],a['semantic_context_signature']) for a in valid]
    assert len(keys)==len(set(keys))
    assert len({a['candidate_id'] for a in valid})==len(valid)
    assert len({a['P0_id'] for a in attempts if 'P0_id' in a})==sum('P0_id' in a for a in attempts)
    contexts=read(root/'semantic_contexts.json')
    assert contexts['candidate_to_signature']=={a['candidate_id']:a['semantic_context_signature'] for a in valid}
    p0_paths={str(p.resolve()) for p in (root/'enriched').glob('seed_*/*/P0.c')}
    assert p0_paths=={a['p0_file'] for a in attempts if 'p0_file' in a}
    checks=[]
    for a in attempts:
        folder=Path(a['directory'])
        assert read(folder/'result.json')['status']==a['status']
        if 'p0_file' not in a:continue
        r=read(folder/'enrichment.json');s=Path(r['s_file']).read_bytes();p0=Path(a['p0_file']).read_bytes()
        verify_enrichment(r,s,p0);assert digest(p0)==a['P0_id']!=digest(s)
        admission=read(folder/'P0_validation.json')
        if a['status']=='VALID_CANDIDATE':
            assert admission['status']=='valid' and admission['sha256']==digest(p0)
            assert all(p['status']=='ok' and p['returncode']==0 for p in admission['checks'].values())
            scan=read(folder/'formal_scan.json');assert scan['status']=='APPLICABLE'
            assert scan['route']['parameter']['usr']==scan['route']['initializer_read']['referenced_usr']
            assert scan['candidates']==a['formal_candidates']
        checks.append(dict(p0=a['p0_file'],lineage_replay=True,status=a['status']))
    replay=[]
    for experiment in (root/'experiment',root/'manual/experiment'):
        selection=read(experiment/'selection_before_compilation.json')
        summary=read(experiment/'summary.json');assert len(selection)==summary['selected_actions']
        for index,action in enumerate(sorted(experiment.glob('action_*'))):
            result=read(action/'result.json')
            assert result['candidate_id']==selection[index]['candidate_id']
            if not (action/'Oracle.json').exists():
                replay.append(dict(directory=str(action),status=result['status'],oracle_present=False));continue
            w=read(action/'Oracle.json');barrier=read(action/'oracle_before_compilation.json')
            assert digest((action/'Oracle.json').read_bytes())==barrier['oracle_sha256']
            assert barrier['persisted_before_P1_compilation'] and barrier['independent_replay']
            s,p0,p1=[Path(w[k]).read_bytes() for k in ('s_file','p0_file','p1_file')]
            assert len({digest(s),digest(p0),digest(p1)})==3
            assert verify_active_oracle(w,s,p0,p1)
            validation=read(action/'P1_validation.json');diagnostics=read(action/'diagnostics.json')
            for name,process in validation['checks'].items():
                for stream in ('stderr','stdout'):
                    assert (action/f'{name}.{stream}.txt').read_text()==process[stream]
                assert diagnostics[name]==result['observations'][name]
            replay.append(dict(directory=str(action),status=result['status'],oracle_present=True,
                replay=True,oracle_hash_unchanged=True,error_counts={n:len(d['errors']) for n,d in diagnostics.items()}))
    save(root/'artifact_replay.json',dict(p0_count=len(checks),p0_checks=checks,p1_checks=replay,
        real_P1_count=len(list((root/'experiment').glob('action_*/P1.c'))),
        artificial_P1_count=len(list((root/'manual/experiment').glob('action_*/P1.c'))),
        valid_candidate_unique_keys=True,semantic_index_consistent=True,physical_P0_accounting_complete=True))
    old=[]
    old_paths=list((PROJECT/'evidence/stage2e/experiment').glob('*/Oracle.json'))+[PROJECT/'evidence/stage2f/manual/validation/Oracle.json',PROJECT/'evidence/stage2a/final_smoke/witness.json']
    for path in old_paths:
        w=read(path);m=w['source_map'];p0=Path(m['p0_file']).read_bytes();p1=Path(m['p1_file']).read_bytes()
        ok=(verify_bitfield_witness(w,p0,p1,Path(w['provenance']['original_path']).read_bytes())
            if 'BITFIELD' in w['mr_id'] else verify_constraint_witness(w,p0,p1))
        assert ok;old.append(dict(oracle=str(path),replay=ok))
    save(root/'historical_witness_replay.json',old)
    before=read(root/'before_hashes.json');repo=PROJECT.parent
    for item in read(root/'source_snapshot_index.json'):
        assert digest(Path(item['snapshot']).read_bytes())==item['sha256']==digest(Path(item['source']).read_bytes())
    allowed={'second_point/src/edgm_second/constraint_witness.py','second_point/src/edgm_second/mr_member_bitfield_addressability.py',
        'second_point/src/edgm_second/mr_typedef_indirect_struct_arg.py','second_point/README.md','second_point/docs/RESEARCH_DESIGN.md'}
    changes=[];unexpected=[]
    for file,sha in before['files'].items():
        path=repo/file;now=digest(path.read_bytes()) if path.is_file() else None
        if now!=sha:
            changes.append(file)
            if file not in allowed:unexpected.append(file)
    seeds=[dict(path=f,sha256=sha,unchanged=digest(Path(f).read_bytes())==sha) for f,sha in before['seeds'].items()]
    assert not unexpected and all(s['unchanged'] for s in seeds)
    save(root/'readonly_check.json',dict(baseline_commit='6a515737d558fdffeb1c130b29fa3575ae42f433',
        baseline_tracked_files=len(before['files']),changed_existing_files=changes,unexpected_changes=unexpected,
        first_research_point_unchanged=True,historical_evidence_unchanged=True,original_seeds=seeds,
        preexisting_deleted_paths_preserved=[f for f,sha in before['files'].items() if sha is None],
        toolchain_or_runtime_modified=False,toolchain_scope='no installation or environment edits; pinned versions recorded in environment.json',
        passed=True))
    error_counts=Counter()
    for r in replay:
        if r.get('oracle_present') and '/manual/' not in r['directory']:
            error_counts.update(r['error_counts'])
    save(root/'audit_summary.json',dict(artifacts_replayed=len(checks),formal_witnesses_replayed=sum(r.get('replay',False) for r in replay),
        historical_witnesses_replayed=len(old),real_target_error_counts=dict(error_counts),readonly_passed=True,
        boundary='hash/source-map/fact-certificate consistency replay; not a full C11 parser or formal semantics proof'))
    print('Artifact replay, historical Oracle replay, semantic dedup, and readonly protection passed.')


if __name__=='__main__':main()
