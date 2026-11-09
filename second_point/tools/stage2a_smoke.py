#!/usr/bin/env python3
"""Bounded stage-2A evidence: one fixture, then one read-only existing seed scan.

This is an acceptance harness, not an Oracle/diagnostic comparison engine.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import re

from edgm_second.environment import PROJECT, EDGM, load_config, inspect_environment
from edgm_second.seeds import discover_seeds, validate_seed
from edgm_second.ast_extract import extract_ast, parse_translation_unit
from edgm_second.source_edit import digest
from edgm_second.constraint_witness import verify_constraint_witness
from edgm_second.mr_typedef_indirect_struct_arg import write_mutation, scan_candidates


def save(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write('\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir', type=Path, required=True)
    args = parser.parse_args()
    directory = args.out_dir.resolve()
    if PROJECT.resolve() not in directory.parents or directory.exists():
        parser.error('out-dir must be a new directory inside second_point')
    directory.mkdir(parents=True)
    config = load_config()
    env = inspect_environment(config)
    save(directory / 'environment.json', env)
    assert env['ready'], env
    p0, p1, oracle = directory / 'P0.c', directory / 'P1.c', directory / 'witness.json'
    p0.write_bytes((PROJECT / 'examples/stage2a/P0.c').read_bytes())
    admission = validate_seed(p0, env)
    save(directory / 'P0_validation.json', admission)
    assert admission['status'] == 'valid', admission
    result = write_mutation(p0, p1, oracle, config['runtime'], admission)
    assert result['status'] == 'APPLIED', result['reasons']
    assert verify_constraint_witness(json.loads(oracle.read_text()), p0.read_bytes(), p1.read_bytes())
    proof_hash = digest(oracle.read_bytes())
    events = ['P0_validated', 'P0_AST_analyzed', 'witness_serialized', 'P1_written', 'witness_replayed']
    # P1 diagnostics are only collected after the complete proof is on disk.
    p1_checks = validate_seed(p1, env)
    events.append('P1_compiled')
    save(directory / 'P1_validation.json', p1_checks)
    assert p1_checks['status'] == 'rejected', p1_checks
    observations = {}
    for name, check in p1_checks['checks'].items():
        (directory / f'P1.{name}.stderr.txt').write_text(check['stderr'])
        errors = [dict(file=m[0], line=int(m[1]), column=int(m[2]), severity=m[3], message=m[4])
                  for m in re.findall(r'^(.+?):(\d+):(\d+): (fatal error|error): (.+)$', check['stderr'], re.M)]
        assert check['returncode'] > 0 and len(errors) == 1, check
        observations[name] = errors
    assert digest(oracle.read_bytes()) == proof_hash
    save(directory / 'diagnostic_observations.json', observations)
    # Only after the minimum fixture has passed do we touch one existing seed.
    seeds, checked = discover_seeds(config)
    scan = dict(status='NO_EXISTING_SEED', checked_directories=checked)
    audit = dict(status='NO_STAGE1_REPORT')
    if seeds:
        seed = seeds[0]
        before = digest(seed.read_bytes())
        scan = dict(source=str(seed), validation=validate_seed(seed, env),
                    candidate_scan=scan_candidates(seed, config['runtime']))
        assert before == digest(seed.read_bytes())
        scan['source_unchanged'] = True
        previous = json.loads((PROJECT / 'evidence/stage1_run.json').read_text())
        assert Path(previous['validation']['source']) == seed
        snapshot = extract_ast(seed, config['runtime'])
        assert snapshot['status'] == 'ok'
        tu = parse_translation_unit(seed, config['runtime'])
        raw_counts = Counter()
        raw_main = []
        for node in tu.cursor.walk_preorder():
            if not node.location.file:
                raw_counts['without_file'] += 1
            elif Path(node.location.file.name).resolve() == seed.resolve():
                raw_counts['main_file'] += 1
                raw_main.append((node.kind.name, node.location.offset, node.extent.start.offset, node.extent.end.offset))
            else:
                raw_counts['header_file'] += 1
        keys = lambda nodes: [(n['kind'], n['offset'], n['start_offset'], n['end_offset']) for n in nodes]
        bad_files = sorted({n['file'] for n in snapshot['nodes'] if Path(n['file']).resolve() != seed.resolve()})
        audit = dict(status='ok', source=str(seed), source_sha256=before,
            stage1_source_hash_matches=before == previous['validation']['sha256'],
            stage1_count=len(previous['ast']['nodes']), reextracted_count=len(snapshot['nodes']),
            raw_tree_counts=dict(raw_counts), foreign_files_in_snapshot=bad_files,
            stage1_reextract_identical=keys(previous['ast']['nodes']) == keys(snapshot['nodes']),
            raw_main_equals_snapshot=raw_main == keys(snapshot['nodes']))
        assert not bad_files and audit['raw_main_equals_snapshot'] and audit['stage1_reextract_identical']
        events.append('one_existing_seed_readonly_scanned')
    save(directory / 'one_seed_scan.json', scan)
    save(directory / 'stage1_ast_audit.json', audit)
    baseline = json.loads((PROJECT / 'evidence/original_hashes.json').read_text())
    current = {str(p.relative_to(EDGM)): digest(p.read_bytes())
               for tree in ('scripts', 'src/edgm', 'config', 'tests', 'data/seeds')
               for p in (EDGM / tree).rglob('*') if p.is_file()}
    readonly = dict(checked_files=len(baseline), changed=[p for p in baseline if current.get(p) != baseline[p]],
                    added=sorted(set(current) - set(baseline)))
    assert not readonly['changed'] and not readonly['added'], readonly
    save(directory / 'readonly_check.json', readonly)
    save(directory / 'summary.json', dict(success=True, event_order=events, witness_sha256_before_p1_compile=proof_hash,
        witness_unchanged_after_p1_compile=True, diagnostic_observations=observations,
        one_seed_scan_status=scan.get('candidate_scan', {}).get('status'),
        compiler_versions={name: item['version'] for name, item in env['compilers'].items()},
        libclang=env['libclang']))
    print(json.dumps(dict(success=True, output=str(directory), ast_audit=audit,
                         seed_scan=scan.get('candidate_scan')), ensure_ascii=False))


if __name__ == '__main__':
    main()
