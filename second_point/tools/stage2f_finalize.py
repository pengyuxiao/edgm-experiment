"""Aggregate actual stage 2F scans, replay witnesses, and check frozen inputs."""
from collections import Counter
import json
from pathlib import Path
from edgm_second.environment import PROJECT,EDGM
from edgm_second.source_edit import digest
from edgm_second.mr_member_bitfield_addressability import verify_bitfield_witness


def save(path,data):
    with path.open('x') as f:json.dump(data,f,ensure_ascii=False,indent=2)


def main():
    root=PROJECT/'evidence/stage2f'
    baseline=[json.loads(p.read_text()) for p in sorted((root/'baseline_scans').glob('seed_*.json'))]
    extended=[json.loads(p.read_text()) for p in sorted((root/'experiment/scans').glob('seed_*.json'))]
    assert len(baseline)==len(extended)==50
    assert [(r['sha256'],r['index']) for r in baseline]==[(r['sha256'],r['index']) for r in extended]
    contexts=[c for r in extended for c in r.get('contexts',[])]
    baseline_summary=json.loads((root/'baseline_summary.json').read_text())
    completed=[r for r in extended if r['formal']['status'] in ('APPLICABLE','NOT_APPLICABLE')]
    context_summary=dict(fields=len(contexts),addresses=sum(c['target_address_count'] for c in contexts),
        complexity_field_counts=dict(Counter(c['complexity'] for c in contexts)),
        cross_statement_fields=sum(c['cross_statement'] for c in contexts),cross_scope_fields=sum(c['cross_scope'] for c in contexts),
        shared_type_cross_function_fields=sum(c['cross_function'] for c in contexts),
        safe_cross_statement_fields=sum(c['safe_candidate'] and c['cross_statement'] for c in contexts),
        safe_cross_scope_fields=sum(c['safe_candidate'] and c['cross_scope'] for c in contexts),
        safe_cross_function_fields=sum(c['safe_candidate'] and c['cross_function'] for c in contexts),
        runtime_cross_function_flow='NOT_PROVEN',
        type_propagation_depth_fields=dict(Counter(str(c['maximum_observed_type_propagation_depth']) for c in contexts)),
        failed_scan_count=50-len(completed))
    if len(completed)==50:
        assert len(contexts)==baseline_summary['distinct_main_file_fields']
        assert context_summary['addresses']==baseline_summary['raw_address_relation_instances']
    # Full paths, declarations and layout evidence already live in each frozen
    # scan. Publish a hash-bound index instead of duplicating those 21 MB here.
    index=[]
    detail_keys={'related_declarations','record_type_references','relations','layout_analysis','cross_function_evidence'}
    for scan_path in sorted((root/'experiment/scans').glob('seed_*.json')):
        record=json.loads(scan_path.read_text());scan_hash=digest(scan_path.read_bytes())
        for position,c in enumerate(record.get('contexts',[])):
            index.append(dict({k:v for k,v in c.items() if k not in detail_keys},
                details_ref=dict(file=str(scan_path.relative_to(root)),json_pointer=f'/contexts/{position}',
                                 file_sha256=scan_hash)))
    assert len(index)==len(contexts)
    save(root/'semantic_contexts.json',dict(schema='stage2f-context-index-v1',summary=context_summary,fields=index,
        details='Each entry references complete frozen AST/type/use facts by file, JSON pointer and SHA256.'))
    def fields(records):
        return {(r['sha256'],e['field']['usr']):(r,e) for r in records
            if r['discovery']['status'] in ('APPLICABLE','NOT_APPLICABLE')
            for e in r['discovery']['candidates']+r['discovery']['rejected_fields']}
    old=fields(baseline);new=fields(extended)
    reasons=set(reason for _,e in old.values() for reason in e.get('reasons',[]))
    rows=[]
    for reason in sorted(reasons):
        matches=[(r,e) for r,e in old.values() if reason in e.get('reasons',[])]
        rows.append(dict(reason=reason,old_unique_fields=len(matches),old_unique_seeds=len({r['seed_id'] for r,e in matches}),
            new_unique_fields=sum(reason in e.get('reasons',[]) for _,e in new.values()),
            sole_discovery_blocker_fields=sum(set(e.get('reasons',[]))=={reason} for _,e in matches)))
    detailed=[]
    for key,(r,e) in old.items():
        newer=new.get(key,(None,None))[1]
        detailed.append(dict(seed_id=r['seed_id'],source_sha256=key[0],field_usr=key[1],canonical_type=e['canonical_type'],
            old_overlapping_reasons=e.get('reasons',[]),new_overlapping_reasons=newer.get('reasons',[]) if newer else None,
            member_use_count=e['member_use_count'],target_address_count=len(e['address_anchors']),
            address_only_rule_would_reject=e['member_use_count']!=len(e['address_anchors']),
            layout_independence_status=newer.get('layout_independence',{}).get('status') if newer else None))
    classification={reason:'conservative implementation scope; not proof that C11 prohibits this construct' for reason in reasons}
    classification['FIELD_NOT_SUPPORTED_ORDINARY_INT_UINT_BOOL']='mixed: legal bitfield base/range must be proved; omitted implementation-defined base types and qualifiers are model exclusions, not universal C11 illegality'
    classification['UNCLASSIFIED_FIELD_USE']='unclosed analysis; some sizeof/other uses could cause independent violations, so no language-level conclusion follows from this label alone'
    analysis=dict(field_denominator=len(old),seed_denominator=len(baseline),reason_rows=rows,per_field=detailed,
        classification=classification,counts_overlap=True,
        formal_first_reason_counts=baseline_summary['formal_first_reason_field_counts'],
        distinction='formal scanner short-circuits; discovery reports independently evaluated overlapping blockers, not additional formal failures',
        non_address_member_use_fields=sum(d['address_only_rule_would_reject'] for d in detailed),
        layout_proven_independent_fields=sum(d['layout_independence_status']=='INDEPENDENT' for d in detailed),
        layout_unproven_detail_field_counts=dict(Counter(reason for _,e in new.values() for reason in e.get('layout_independence',{}).get('reasons',[]))),
        single_limit_relaxation_note='sole discovery blocker is a necessary counterfactual filter, not a claim that a formal MR would become applicable')
    save(root/'rejection_analysis.json',analysis)
    replays=[]
    for oracle in [*sorted((PROJECT/'evidence/stage2e/experiment').glob('*/Oracle.json')),*sorted((root/'manual').glob('*/Oracle.json')),*sorted((root/'experiment').glob('real_*/Oracle.json'))]:
        w=json.loads(oracle.read_text());p0=Path(w['source_map']['p0_file']).read_bytes();p1=Path(w['source_map']['p1_file']).read_bytes()
        original=Path(w['provenance']['original_path']).read_bytes()
        replays.append(dict(oracle=str(oracle),replay=verify_bitfield_witness(w,p0,p1,original)))
    save(root/'witness_replay.json',replays)
    before=json.loads((root/'before_hashes.json').read_text())
    def current(path):return digest(path.read_bytes()) if path.is_file() else None
    first_changed=[p for p,h in before['first_research'].items() if current(EDGM/p)!=h]
    second_changed=[p for p,h in before['old_second_point'].items() if current(EDGM/p)!=h]
    allowed={'second_point/README.md','second_point/docs/RESEARCH_DESIGN.md','second_point/src/edgm_second/mr_member_bitfield_addressability.py'}
    unintended=[p for p in second_changed if p not in allowed]
    selection=json.loads((root/'selection.json').read_text())
    seed_changes=[r['source'] for r in selection if current(Path(r['source']))!=r['sha256']]
    evidence_changes=[p for p in second_changed if '/evidence/' in p]
    checks=dict(first_research_files_checked=len(before['first_research']),first_research_changed=first_changed,
        preexisting_missing_first_research=[p for p,h in before['first_research'].items() if h is None],
        old_second_point_files_checked=len(before['old_second_point']),intentional_old_changes=second_changed,
        unintended_old_changes=unintended,old_evidence_changed=evidence_changes,seeds_checked=50,seeds_changed=seed_changes,
        runtime_headers_checked=sum('/csmith/runtime/' in p for p in before['first_research']),all_saved_witnesses_replayed=replays)
    assert not first_changed and not unintended and not evidence_changes and not seed_changes
    save(root/'readonly_check.json',checks)
    files=[*sorted((PROJECT/'src/edgm_second').glob('*.py')),*sorted((PROJECT/'tests').glob('test_stage2f*.py')),
        Path(__file__).resolve(),PROJECT/'README.md',PROJECT/'docs/RESEARCH_DESIGN.md',PROJECT/'docs/STAGE2F_NATIVE_DEPENDENCY_VALIDATION.md']
    save(root/'implementation_manifest.json',{str(p.relative_to(PROJECT)):digest(p.read_bytes()) for p in files})
    print(json.dumps(dict(context_summary=context_summary,layout_proven_independent_fields=analysis['layout_proven_independent_fields'],
        baseline_safe_fields=baseline_summary['safe_fields'],extended_safe_fields=sum(r['formal']['candidate_count'] for r in completed),
        readonly_ok=True,replays=len(replays)),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
