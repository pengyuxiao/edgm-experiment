#!/usr/bin/env python3
"""Recompute reporting tables from saved evidence; no AST/compiler execution."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import re

from edgm_second.environment import EDGM, PROJECT
from edgm_second.applicability_run import summarize
from edgm_second.source_edit import digest


def write(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus-dir', type=Path, required=True)
    args = parser.parse_args()
    corpus = args.corpus_dir.resolve()
    if PROJECT.resolve() not in corpus.parents:
        parser.error('corpus-dir must be inside second_point')
    output = corpus.parent
    records = json.loads((corpus / 'records.json').read_text())
    previous = json.loads((corpus / 'summary.json').read_text())
    summary = {**previous, **summarize(records)}
    # Timings and execution outcomes remain those of the actual saved run.
    write(corpus / 'summary.json', summary)
    extra, roots, fields, lengths, gates = (Counter() for _ in range(5))
    headers = []
    for record in records:
        scan = record['scan']
        if scan['status'] not in ('APPLICABLE', 'NOT_APPLICABLE'):
            continue
        inv = scan['inventory']
        extra['main_file_nodes'] += scan['main_file_nodes']
        extra['supported_records'] += scan['supported_records']
        extra['simple_typedef_edges'] += scan['simple_typedef_edges']
        extra['struct_pair_seeds'] += len({s['usr'] for s in inv['structs']}) >= 2
        extra['supported_pair_seeds'] += scan['supported_records'] >= 2
        extra['rejected_calls'] += len(scan['rejected_calls'])
        gates.update(scan['rejected_reason_call_counts'])
        for alias in inv['typedefs']:
            roots[alias['canonical_kind']] += 1
            lengths[alias['chain_length']] += 1
        for struct in inv['structs']:
            for field in struct['fields']:
                fields['struct_field_total'] += 1
                fields['bitfields'] += field['bitfield']
                fields[field['kind']] += 1
        header = Path(record['source']).read_text().split('*/', 1)[0] + '*/'
        seed = re.search(r'\* Seed:\s*(\d+)', header)
        headers.append(dict(source=record['source'], csmith_seed=seed[1] if seed else None, header=header))
    write(output / 'structural_aggregate.json', dict(additional_counts=dict(extra), typedef_canonical_kinds=dict(roots),
        typedef_chain_lengths=dict(lengths), struct_field_kinds_and_bitfields=dict(fields), rejected_call_gate_counts=dict(gates),
        distinct_csmith_seed_ids=len({h['csmith_seed'] for h in headers if h['csmith_seed']}), seed_headers=headers))
    with (corpus / 'summary.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.writer(stream)
        writer.writerow(['section', 'metric', 'value'])
        for name, value in summary.items():
            if isinstance(value, (int, float)) or value is None:
                writer.writerow(['summary', name, value])
        for section in ('status_counts', 'nonapplicable_reason_seed_counts', 'structural_totals', 'phase_seconds'):
            for name, value in summary[section].items():
                writer.writerow([section, name, value])
    baseline = json.loads((PROJECT / 'evidence/original_hashes.json').read_text())
    current = {str(p.relative_to(EDGM)): digest(p.read_bytes())
               for tree in ('scripts', 'src/edgm', 'config', 'tests', 'data/seeds')
               for p in (EDGM / tree).rglob('*') if p.is_file()}
    protected = ['second_point/config/environment.json', 'second_point/src/edgm_second/environment.py',
                 'second_point/src/edgm_second/seeds.py', 'second_point/src/edgm_second/ast_extract.py',
                 'second_point/tests/test_foundation.py']
    stage2a = json.loads((PROJECT / 'evidence/stage2a/implementation_manifest.json').read_text())
    check = dict(checked_original_files=len(baseline), changed_or_deleted=[p for p in baseline if current.get(p) != baseline[p]],
        added=sorted(set(current) - set(baseline)),
        stage1_environment_and_interfaces_unchanged={p: digest((EDGM / p).read_bytes()) == stage2a[p] for p in protected},
        corpus_source_hashes_unchanged=all(digest(Path(r['source']).read_bytes()) == r['sha256'] for r in records))
    assert not check['changed_or_deleted'] and not check['added']
    assert all(check['stage1_environment_and_interfaces_unchanged'].values()) and check['corpus_source_hashes_unchanged']
    write(output / 'readonly_check.json', check)
    manifest = {str(p.relative_to(EDGM)): digest(p.read_bytes()) for tree in ('src', 'tests', 'tools', 'config')
                for p in (PROJECT / tree).rglob('*') if p.is_file() and '__pycache__' not in p.parts}
    write(output / 'implementation_manifest.json', manifest)
    print(json.dumps(dict(effective_seeds=summary['effective_seeds'], applicable_seeds=summary['applicable_seeds'],
                         applicability_rate=summary['applicability_rate'], readonly_check=check), ensure_ascii=False))


if __name__ == '__main__':
    main()
