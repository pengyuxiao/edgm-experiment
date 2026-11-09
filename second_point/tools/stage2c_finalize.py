#!/usr/bin/env python3
"""Derive report tables from saved profiles; never parse or mutate C sources."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
from edgm_second.environment import PROJECT,EDGM
from edgm_second.semantic_profile_run import aggregate,semantic_projection
from edgm_second.source_edit import digest

FAMILIES = {
 'typedef_declaration_use':(['typedef_declarations','typedef_type_uses'],['typedef_to_type_use']),
 'typedef_chain_propagation':(['typedef_declarations'],['typedef_to_operation']),
 'function_declaration_call':(['functions','function_definitions','parameters','main_function_calls'],['function_redeclaration','formal_call_argument']),
 'record_member_operation':(['structs','unions','fields','member_accesses'],['member_to_access','member_to_operation','record_contains_record','member_to_address','record_type_to_assignment']),
 'pointer_dereference_write':(['pointer_declarations','dereferences','indirect_writes'],['pointer_to_dereference','pointer_to_indirect_write','address_binding']),
 'qualifier_use':(['qualified_declarations','qualified_uses'],['qualified_decl_to_use']),
 'array_bound_subscript':(['arrays','array_dimensions','array_subscripts'],['bound_declaration_dependency','array_decl_to_subscript']),
 'integer_enum_constant':(['integer_literals','enum_constants','enum_references','ice_subset_expressions'],['enum_to_reference','constant_to_ice_context']),
 'switch_control_labels':(['switches','case_labels','default_labels','nested_switches'],['switch_to_label']),
 'goto_label_scope':(['gotos','labels','local_scopes'],['goto_to_label']),
 'bitfield_width':(['bitfields'],['bitfield_width_to_field']),
 'integer_operation_conversion':(['integer_operations','explicit_casts','implicit_type_changes'],['integer_operand_to_operation'])}


def write(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--corpus-dir',type=Path,required=True)
    args=parser.parse_args();directory=args.corpus_dir.resolve()
    if PROJECT.resolve() not in directory.parents:parser.error('corpus-dir outside second_point')
    records=json.loads((directory/'records.json').read_text())
    old=json.loads((directory/'summary.json').read_text());summary={**old,**aggregate(records)}
    write(directory/'summary.json',summary)
    families={}
    for name,(metrics,relations) in FAMILIES.items():
        families[name]=dict(seed_count=sum(any(r['profile']['structures'][m]['instances'] for m in metrics)
            for r in records if r['profile']['status']=='PROFILED'),
            components={m:{k:v for k,v in summary['structures'][m].items() if k!='examples'} for m in metrics},
            dependency_instances=sum(summary['relations'][m]['instances'] for m in relations), dependency_kinds=relations,
            note='component counts are roles, not disjoint AST partitions; relation counts are occurrence/path instances')
    families['integer_operation_conversion']['dependency_instances']+=summary['structures']['implicit_type_changes']['instances']
    families['integer_operation_conversion']['note']+='; includes implicit AST type-change wrapper edges'
    write(directory/'families.json',families)
    with (directory/'summary.csv').open('w',newline='',encoding='utf-8') as stream:
        writer=csv.writer(stream);writer.writerow(['kind','name','seed_count','instances','dependency_relations'])
        for kind in ('structures','relations'):
            for name,e in summary[kind].items():writer.writerow([kind,name,e['seed_count'],e['instances'],e['dependency_relations']])
    with (directory/'per_seed.csv').open('w',newline='',encoding='utf-8') as stream:
        writer=csv.writer(stream);writer.writerow(['seed_id','sha256','status','main_file_nodes','typedef_operation_edges',
            'formal_actual_edges','member_address_edges','nested_pointer_argument_edges','record_assignment_edges','bound_declaration_edges'])
        for r in records:
            p=r['profile'];writer.writerow([r['seed_id'],r['sha256'],p['status'],p.get('main_file_nodes')]+[
                p.get('relations',{}).get(key,{}).get('instances') for key in ['typedef_to_operation','formal_call_argument',
                    'member_to_address','nested_pointer_formal_actual','record_type_to_assignment','bound_declaration_dependency']])
    switch_types,case_values,array_sizes,field_address_sample_types=Counter(),Counter(),Counter(),Counter()
    nested_with_assignments=[]
    for r in records:
        p=r['profile']
        if p['status']!='PROFILED':continue
        for s in p['details']['switches']:
            switch_types[s['promoted_control_type']['spelling']]+=1
            for label in s['labels']:
                if label['kind']=='CASE_STMT':case_values[str(label['converted_value'])]+=1
        for a in p['details']['arrays']:
            for bound in a['type']['array_bounds']:array_sizes[str(bound)]+=1
        if p['relations']['record_contains_record']['instances'] and p['relations']['record_type_to_assignment']['instances']:
            nested_with_assignments.append(r['seed_id'])
        for e in p['relations']['member_to_address']['examples']:field_address_sample_types[e['field_type']['canonical_kind']]+=1
    write(directory/'supplementary.json',dict(switch_promoted_types=dict(switch_types),case_converted_values=dict(case_values),
        array_bound_sizes=dict(array_sizes),member_address_sample_types_only=dict(field_address_sample_types),
        note='member address type histogram is bounded evidence sampling, NOT an all-edge histogram',
        seeds_with_both_record_containment_and_assignment=nested_with_assignments,
        nested_record_to_assignment_join_confirmed=False, typedef_expression_span=None))
    proposals=[]
    for rank,name,relation in [(1,'MR_MEMBER_BITFIELD_ADDRESSABILITY','member_to_address'),
            (2,'MR_MEMBER_CONST_AGGREGATE_WRITE','record_type_to_assignment'),
            (3,'MR_DEEP_QUALIFIER_CALL_INVARIANCE','nested_pointer_formal_actual')]:
        e=summary['relations'][relation]
        proposals.append(dict(rank=rank,mr_name=name,implementation_status='DESIGN_ONLY',supporting_relation=relation,
            related_seed_count=e['seed_count'],relation_instances=e['instances'],
            confirmed_full_precondition_candidate_count=None,mr_applicability_rate=None,
            recommended_for_implementation=name != 'MR_DEEP_QUALIFIER_CALL_INVARIANCE',
            severity_screen='C_EXTWARN_SOURCE_PATH_NOT_ELIGIBLE' if name == 'MR_DEEP_QUALIFIER_CALL_INVARIANCE' else 'REQUIRES_FUTURE_MINIMAL_VALIDATION',
            reason='complete impact closure, exact editable token ranges and independent proof not yet implemented'))
    write(directory/'mr_design_summary.json',dict(proposals=proposals,
        investigated_but_not_selected=dict(array_constant_dependency=summary['relations']['constant_to_array_bound']['instances'],
            typedef_operation_dependency=summary['relations']['typedef_to_operation']['instances'],nested_switches=summary['structures']['nested_switches']['instances'])))
    baseline=json.loads((PROJECT/'evidence/original_hashes.json').read_text())
    current={str(p.relative_to(EDGM)):digest(p.read_bytes()) for tree in ['scripts','src/edgm','config','tests','data/seeds']
        for p in (EDGM/tree).rglob('*') if p.is_file()}
    readonly=dict(checked_original_files=len(baseline),changed_or_deleted=[p for p,h in baseline.items() if current.get(p)!=h],
        added=sorted(set(current)-set(baseline)),all_fifty_hashes_unchanged=all(digest(Path(r['source']).read_bytes())==r['sha256'] for r in records))
    assert not readonly['changed_or_deleted'] and not readonly['added'] and readonly['all_fifty_hashes_unchanged']
    write(directory/'readonly_check.json',readonly)
    manifest={str(p.relative_to(EDGM)):digest(p.read_bytes()) for tree in ('src','tests','tools','config')
        for p in (PROJECT/tree).rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    write(directory/'implementation_manifest.json',manifest)
    print(json.dumps(dict(completed_seeds=summary['completed_seeds'],main_file_nodes=summary['main_file_nodes'],
        wall_seconds=summary['wall_seconds'],proposal_full_candidates='UNKNOWN',readonly_check=readonly),ensure_ascii=False))


if __name__=='__main__':main()
