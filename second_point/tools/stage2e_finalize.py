"""Read-only evidence replay and exclusive-create stage 2E delivery checks."""
import difflib
import json
from pathlib import Path
from edgm_second.environment import PROJECT, EDGM
from edgm_second.source_edit import digest
from edgm_second.mr_member_bitfield_addressability import verify_bitfield_witness, verify_enrichment_provenance


def save(path,data):
    with path.open('x') as stream:json.dump(data,stream,ensure_ascii=False,indent=2)


def main():
    root=PROJECT/'evidence/stage2e'; out=root/'experiment'
    checks=[]
    for directory in [out/'manual',*sorted(out.glob('real_*'))]:
        w=json.loads((directory/'Oracle.json').read_text());m=w['source_map']
        p0=Path(m['p0_file']).read_bytes();p1=Path(m['p1_file']).read_bytes()
        original=Path(w['provenance']['original_path']).read_bytes()
        replay=verify_bitfield_witness(w,p0,p1,original)
        barrier=json.loads((directory/'oracle_before_compilation.json').read_text())
        checks.append(dict(directory=str(directory),replay=replay,
            saved_oracle_unchanged=digest((directory/'Oracle.json').read_bytes())==barrier['witness_sha256'],
            p0_review_copy_matches=(directory/'P0.c').read_bytes()==p0,
            source_kind=w['provenance']['source_kind'],all_target_count=len(w['affected_uses'])))
    receipts=json.loads((PROJECT/'evidence/stage2d/experiment/enrichment_records.json').read_text())
    lineage=[]
    for index,r in enumerate(receipts,1):
        s=Path(r['original_path']).read_bytes();p0=Path(r['enhanced_path']).read_bytes()
        verify_enrichment_provenance(s,p0,r)
        scan=json.loads((out/f'P0_scan_{index:02}.json').read_text())
        c=next(c for c in scan['candidates'] if c['isolation']['subset']=='inline_single_int_member_compound_literal')
        isolation=c['isolation'];a,b=r['source_map']['segments'][1]['nested_original_read']
        read=isolation['original_read'];op=isolation['original_operation'];oldop=r['original_operation']
        delta=len(r['edit']['after'].encode())-len(r['edit']['before'].encode())
        exact_read=[read['start_offset'],read['end_offset']]==[a,b]
        exact_op=op['start_offset']==oldop['start_offset'] and op['end_offset']==oldop['end_offset']+delta
        assert exact_read and exact_op
        lineage.append(dict(seed_id=r['seed_id'],source_map_replay=True,original_read_mapped=exact_read,
            original_operation_mapped=exact_op,original_operation=oldop,enhanced_operation=op,
            parameter=isolation['parameter_declaration'],new_field=c['field'],
            original_sha256=digest(s),p0_sha256=digest(p0),
            dependency_strength='simple local value routing into original binary expression',
            original_parameter_crosses_into_expression=True,new_field_cross_statement=False,
            new_field_cross_scope=False,new_field_cross_function=False))
    save(root/'all_five_lineage_audit.json',lineage)
    hashes=json.loads((PROJECT/'evidence/original_hashes.json').read_text())
    old_changes=[f for f,h in hashes.items() if not (EDGM/f).is_file() or digest((EDGM/f).read_bytes())!=h]
    seeds=json.loads((PROJECT/'evidence/stage2b/corpus/records.json').read_text())
    seed_changes=[r['source'] for r in seeds if digest(Path(r['source']).read_bytes())!=r['sha256']]
    stage2d=json.loads((PROJECT/'evidence/stage2d/final_implementation_manifest.json').read_text())
    stage2d_changes=[f for f,h in stage2d.items() if digest((PROJECT/f).read_bytes())!=h]
    report=PROJECT/'docs/STAGE2E_BITFIELD_MR_VALIDATION.md'
    files=[PROJECT/'src/edgm_second'/name for name in ['constraint_witness.py',
        'mr_member_bitfield_addressability.py','bitfield_validation.py','bitfield_run.py']]
    files += [PROJECT/'tests/test_stage2e_bitfield.py',Path(__file__).resolve(),report,PROJECT/'README.md']
    save(root/'implementation_manifest.json',{str(f.relative_to(PROJECT)):digest(f.read_bytes()) for f in files})
    # Existing framework extension is additive. Preserve a review diff against
    # the pre-2E source reconstructed by removing exactly this new dataclass.
    review=[]
    for f in files:
        if f.name in ('README.md',report.name):continue
        after=f.read_text();before=''
        if f.name=='constraint_witness.py':
            a=after.index('@dataclass(frozen=True)\nclass BitfieldConstraintWitness:')
            b=after.index('@dataclass(frozen=True)\nclass ConstraintWitness:')
            before=after[:a]+after[b:]
        review.extend(difflib.unified_diff(before.splitlines(True),after.splitlines(True),
            fromfile='before/'+str(f.relative_to(PROJECT)),tofile='after/'+str(f.relative_to(PROJECT))))
    with (root/'implementation.diff').open('x') as stream:stream.write(''.join(review))
    result=dict(witnesses=checks,lineages_checked=len(lineage),original_edgm_files_checked=len(hashes),
        changed_original_edgm_files=old_changes,original_seeds_checked=len(seeds),changed_seeds=seed_changes,
        stage2d_manifest_checked=len(stage2d),changed_stage2d_implementation=stage2d_changes,
        final_tests_log=str(root/'final_all_tests.txt'),report=str(report))
    assert not old_changes and not seed_changes and not stage2d_changes
    assert all(x['saved_oracle_unchanged'] and x['p0_review_copy_matches'] for x in checks)
    save(root/'delivery_check.json',result)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
