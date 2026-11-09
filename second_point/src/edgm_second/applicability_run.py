"""Bounded corpus applicability experiment. Each libclang scan is isolated."""
import argparse
from collections import Counter
import csv
import difflib
import json
from pathlib import Path
import random
import re
import subprocess
import sys
import time

from .environment import EDGM, PROJECT, inspect_environment, load_config
from .seeds import validate_seed
from .source_edit import digest


def save(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def choose_sources(directories, limit, random_seed):
    by_hash, checked = {}, []
    for directory in directories:
        directory = Path(directory).resolve()
        files = sorted(p.resolve() for p in directory.glob('*.c') if p.is_file())
        checked.append(dict(directory=str(directory), files=len(files)))
        for path in files:
            sha = digest(path.read_bytes())
            by_hash.setdefault(sha, []).append(str(path))
    pool = [dict(source=paths[0], sha256=sha, aliases=paths) for sha, paths in sorted(by_hash.items())]
    random.Random(random_seed).shuffle(pool)
    return pool[:limit], dict(directories=checked, unique_files=len(pool),
        input_files=sum(c['files'] for c in checked), requested=limit, random_seed=random_seed,
        selection='sorted SHA256 pool; local Random(seed).shuffle; first limit; exact-byte deduplication',
        pool=[dict(sha256=sha, files=paths) for sha, paths in sorted(by_hash.items())])


def decode(value):
    return value.decode('utf-8', 'replace') if isinstance(value, bytes) else value or ''


def messages(stdout):
    values = []
    for line in decode(stdout).splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                values.append(value)
        except json.JSONDecodeError:
            pass
    return values


def isolated_scan(path, runtime, timeout):
    command = [sys.executable, '-m', 'edgm_second.applicability_run', '--worker',
               '--seed', str(Path(path).resolve()), '--runtime', str(runtime)]
    started = time.perf_counter()
    process_record = dict(command=command, returncode=None, stdout='', stderr='')
    try:
        process = subprocess.run(command, capture_output=True, timeout=timeout)
        process_record.update(returncode=process.returncode, stdout=decode(process.stdout), stderr=decode(process.stderr))
        events = messages(process.stdout)
        final = [e['result'] for e in events if e.get('event') == 'result']
        if process.returncode == 0 and len(final) == 1:
            result = final[0]
        else:
            ready = next((e for e in events if e.get('event') == 'ast_ready'), {})
            result = dict(status='WORKER_FAILED', ast_status=ready.get('ast_status', 'UNKNOWN'),
                          main_file_nodes=ready.get('main_file_nodes'), candidate_count=None,
                          reasons=['WORKER_NONZERO_OR_INVALID_RESPONSE'])
    except subprocess.TimeoutExpired as exc:
        process_record.update(stdout=decode(exc.stdout), stderr=decode(exc.stderr))
        events = messages(exc.stdout)
        ready = next((e for e in events if e.get('event') == 'ast_ready'), {})
        result = dict(status='SCAN_TIMEOUT', ast_status=ready.get('ast_status', 'UNKNOWN'),
                      main_file_nodes=ready.get('main_file_nodes'), candidate_count=None,
                      timeout_phase='candidate_scan' if ready else 'parse_or_ast_index',
                      reasons=['SCAN_DEADLINE_EXCEEDED'])
    except OSError as exc:
        result = dict(status='WORKER_FAILED', ast_status='UNKNOWN', candidate_count=None,
                      reasons=[f'{type(exc).__name__}: {exc}'])
    result.update(worker_seconds=time.perf_counter() - started, worker_process=process_record)
    return result


def effective(record):
    return (record['validation']['status'] == 'valid' and record.get('source_unchanged') is True
            and record['scan']['status'] in ('APPLICABLE', 'NOT_APPLICABLE')
            and record['scan']['ast_status'] == 'OK')


def summarize(records):
    valid = [r for r in records if effective(r)]
    applicable = [r for r in valid if r['scan']['candidate_count'] > 0]
    na = [r for r in valid if r['scan']['status'] == 'NOT_APPLICABLE']
    reasons = Counter(code for r in na for code in set(r['scan'].get('reason_categories', [])))
    priority = ['NO_STRUCT_FUNCTION_POINTER_MEMBER', 'NO_MEMBER_INDIRECT_CALL',
                'NO_RELEVANT_STRUCT_TYPEDEF_CHAIN', 'NO_DISTINCT_COMPLETE_STRUCT_PAIR',
                'TYPE_IDENTITY_OR_CHAIN_UNSUPPORTED', 'NO_SAFE_REPLACEMENT_TYPE',
                'MULTIPLE_AFFECTED_USES', 'AFFECTED_TYPEDEF_REDECLARED', 'TAG_SHADOWING',
                'CURRENT_LAYOUT_RESTRICTION', 'CURRENT_PREPROCESSING_RESTRICTION', 'OTHER_CURRENT_PRECONDITIONS']
    primary = Counter(next((code for code in priority if code in r['scan'].get('reason_categories', [])),
                           'UNCLASSIFIED') for r in na)
    structure = Counter()
    for r in valid:
        inv = r['scan']['inventory']
        for key in ('struct_definitions', 'union_definitions', 'typedef_count', 'struct_function_pointer_members',
                    'member_indirect_call_count', 'record_typedef_chain_count'):
            structure[key] += inv[key]
        structure['seeds_with_structs'] += inv['struct_definitions'] > 0
        structure['seeds_with_struct_function_pointer_members'] += inv['struct_function_pointer_members'] > 0
        structure['seeds_with_relevant_struct_typedef_chain'] += inv['record_typedef_chain_count'] > 0
    return dict(total_seeds=len(records), legal_seeds=sum(r['validation']['status'] == 'valid' for r in records),
        ast_success_seeds=sum(r['scan'].get('ast_status') == 'OK' for r in records), effective_seeds=len(valid),
        applicable_seeds=len(applicable), candidate_positions=sum(r['scan']['candidate_count'] for r in valid),
        applicability_rate=len(applicable) / len(valid) if valid else None,
        applicability_denominator='legal, unchanged, AST OK and completed APPLICABLE/NOT_APPLICABLE scans',
        status_counts=dict(Counter(r['scan']['status'] for r in records)),
        nonapplicable_reason_seed_counts=dict(reasons),
        nonapplicable_primary_reason_counts=dict(primary), primary_rule='first missing prerequisite in primary_reason_priority',
        primary_reason_priority=priority,
        structural_totals=dict(structure),
        validation_seconds=sum(r.get('validation_seconds', 0) for r in records),
        worker_seconds=sum(r['scan'].get('worker_seconds', 0) for r in records),
        phase_seconds={key: sum(r['scan'].get('timing_seconds', {}).get(key, 0) for r in records)
                       for key in ('parse_seconds', 'traversal_seconds', 'index_seconds', 'inventory_seconds', 'candidate_seconds')})


def stable_results(records):
    """Semantic reproducibility projection; timings and process transcripts are excluded."""
    def strip(value):
        if isinstance(value, dict):
            return {k: strip(v) for k, v in value.items() if k not in
                    ('elapsed_seconds', 'worker_seconds', 'timing_seconds', 'validation_seconds', 'worker_process')}
        if isinstance(value, list):
            return [strip(v) for v in value]
        return value
    return strip(records)


def verify_real_candidates(directory, records, environment, runtime, compiler_timeout):
    """At most three seeds; preserve every outcome, including unexpected errors."""
    from .mr_typedef_indirect_struct_arg import write_mutation
    from .constraint_witness import verify_constraint_witness
    results = []
    for record in [r for r in records if effective(r) and r['scan']['candidate_count']][:3]:
        case = directory / ('mutation_' + record['sha256'][:16])
        case.mkdir()
        p0, p1, proof = Path(record['source']), case / 'P1.c', case / 'witness.json'
        try:
            mutation = write_mutation(p0, p1, proof, runtime, record['validation'])
            if mutation['status'] != 'APPLIED':
                raise RuntimeError('candidate could not be reproduced')
            witness = json.loads(proof.read_text())
            verify_constraint_witness(witness, p0.read_bytes(), p1.read_bytes())
            proof_sha = digest(proof.read_bytes())
            expected_site = record['scan']['candidates'][0]['call']['start_offset']
            if witness['source_anchors']['call']['p0']['start_offset'] != expected_site:
                raise RuntimeError('mutation selected a different call site')
            # The complete witness already exists before this first P1 compilation.
            checks = validate_seed(p1, environment, compiler_timeout)
            save(case / 'P1_validation.json', checks)
            (case / 'minimal.diff').write_text(''.join(difflib.unified_diff(
                p0.read_text().splitlines(True), p1.read_text().splitlines(True), fromfile=str(p0), tofile=str(p1))))
            observations = {}
            root_names = [witness['expected_parameter_type']['tag'], witness['actual_argument_type']['p1']['tag']]
            anchor = witness['source_anchors']['argument']['p1']
            for name, check in checks['checks'].items():
                (case / f'{name}.stderr.txt').write_text(check['stderr'])
                errors = []
                for match in re.finditer(r'^(.+?):(\d+):(\d+): (fatal error|error): (.+)$', check['stderr'], re.M):
                    message = match[5]
                    target = ((name == 'gcc' and message.startswith('incompatible type for argument 1 of ')) or
                              (name == 'clang' and message.startswith('passing ') and 'to parameter of incompatible type' in message))
                    observed_tags = set(re.findall(r'\bstruct ([A-Za-z_]\w*)\b', check['stderr']))
                    target = target and set(root_names).issubset(observed_tags)
                    errors.append(dict(file=match[1], line=int(match[2]), column=int(match[3]), severity=match[4],
                        message=message, category='incompatible_struct_argument' if target else 'UNCLASSIFIED_ERROR',
                        exact_argument_anchor=(match[1], int(match[2]), int(match[3])) ==
                                              (anchor['file'], anchor['line'], anchor['column'])))
                observations[name] = dict(errors=errors, target_error_observed=any(
                    e['category'] == 'incompatible_struct_argument' for e in errors),
                    unclassified_error_count=sum(e['category'] == 'UNCLASSIFIED_ERROR' for e in errors),
                    returncode=check['returncode'], execution_status=check['status'])
            result = dict(source=str(p0), source_sha256=record['sha256'],
                witness_sha256_before_compilation=proof_sha, witness_unchanged=digest(proof.read_bytes()) == proof_sha,
                source_unchanged=digest(p0.read_bytes()) == record['sha256'], observations=observations,
                status='EXECUTED_REQUIRES_REVIEW', confirmed_bug=False)
            save(case / 'execution.json', result)
        except Exception as exc:
            result = dict(source=str(p0), status='MUTATION_VALIDATION_FAILED', exception=f'{type(exc).__name__}: {exc}')
            save(case / 'failure.json', result)
        results.append(result)
    return results


def run_corpus(directory, config, directories, limit=100, random_seed=20261108, scan_timeout=30, compiler_timeout=20):
    directory = Path(directory).resolve()
    if PROJECT.resolve() not in directory.parents or directory.exists():
        raise ValueError('output must be a new directory inside second_point')
    if not 1 <= limit <= 100 or min(scan_timeout, compiler_timeout) <= 0:
        raise ValueError('limit must be 1..100; timeouts must be positive')
    started = time.perf_counter()
    env = inspect_environment(config)
    if not env['ready']:
        raise RuntimeError(f'environment is not ready: {env}')
    selected, manifest = choose_sources(directories, limit, random_seed)
    directory.mkdir(parents=True)
    save(directory / 'environment.json', env)
    save(directory / 'selection.json', dict(**manifest, selected=selected,
        scan_timeout=scan_timeout, compiler_timeout=compiler_timeout))
    records = []
    for index, item in enumerate(selected):
        path = Path(item['source'])
        validation_start = time.perf_counter()
        validation = validate_seed(path, env, compiler_timeout)
        validation_seconds = time.perf_counter() - validation_start
        if validation['status'] == 'valid':
            scan = isolated_scan(path, config['runtime'], scan_timeout)
        else:
            scan = dict(status='P0_REJECTED' if validation['status'] == 'rejected' else 'VALIDATION_FAILED',
                        ast_status='NOT_RUN', candidate_count=None, reasons=[validation['status']])
        try:
            after = digest(path.read_bytes())
        except OSError:
            after = None
        unchanged = after == item['sha256'] == validation.get('sha256')
        if not unchanged:
            scan['status_before_source_check'] = scan['status']
            scan['status'] = 'SOURCE_CHANGED'
        record = dict(seed_id=path.stem, sha256=item['sha256'], source=str(path),
                      validation=validation, validation_seconds=validation_seconds,
                      source_unchanged=unchanged, scan=scan)
        records.append(record)
        save(directory / f'seed_{index+1:03d}.json', record)
        print(json.dumps(dict(index=index + 1, total=len(selected), seed=path.name,
                              status=scan['status'], candidates=scan['candidate_count'])), flush=True)
    save(directory / 'records.json', records)
    semantic = stable_results(records)
    save(directory / 'reproducible_results.json', semantic)
    real_validations = verify_real_candidates(directory, records, env, config['runtime'], compiler_timeout)
    save(directory / 'real_candidate_validations.json', real_validations)
    summary = summarize(records)
    summary.update(wall_seconds=time.perf_counter() - started,
                   random_seed=random_seed, limit=limit,
                   reproducible_results_sha256=digest(json.dumps(semantic, sort_keys=True).encode()),
                   real_mutations_performed=len(real_validations))
    save(directory / 'summary.json', summary)
    fields = ['seed_id', 'sha256', 'source', 'validation_status', 'ast_status', 'main_file_nodes', 'scan_status',
              'candidate_count', 'typedef_chain_lengths', 'reason_categories', 'scan_seconds', 'validation_seconds']
    with (directory / 'records.csv').open('x', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for r in records:
            scan = r['scan']
            writer.writerow(dict(seed_id=r['seed_id'], sha256=r['sha256'], source=r['source'],
                validation_status=r['validation']['status'], ast_status=scan.get('ast_status'),
                main_file_nodes=scan.get('main_file_nodes'), scan_status=scan['status'], candidate_count=scan['candidate_count'],
                typedef_chain_lengths=json.dumps([c['typedef_chain_length'] for c in scan.get('candidates', [])]),
                reason_categories=json.dumps(scan.get('reason_categories', [])), scan_seconds=scan.get('elapsed_seconds', scan.get('worker_seconds')),
                validation_seconds=r['validation_seconds']))
    return summary, records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--seed', type=Path)
    parser.add_argument('--runtime')
    parser.add_argument('--out-dir', type=Path)
    parser.add_argument('--limit', type=int, default=100)
    parser.add_argument('--random-seed', type=int, default=20261108)
    parser.add_argument('--scan-timeout', type=float, default=30)
    parser.add_argument('--compiler-timeout', type=float, default=20)
    args = parser.parse_args()
    if args.worker:
        from .applicability import analyze_candidates
        progress = lambda value: print(json.dumps(value), flush=True)
        result = analyze_candidates(args.seed, args.runtime, progress=progress)
        print(json.dumps(dict(event='result', result=result)), flush=True)
        return 0
    if args.out_dir is None:
        parser.error('--out-dir is required')
    config = load_config()
    summary, _ = run_corpus(args.out_dir, config, [EDGM / d for d in config['seed_dirs']],
        args.limit, args.random_seed, args.scan_timeout, args.compiler_timeout)
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    # Zero candidates is a successful applicability experiment. Execution failures are not.
    return 1 if any(s not in ('APPLICABLE', 'NOT_APPLICABLE', 'P0_REJECTED') for s in summary['status_counts']) else 0


if __name__ == '__main__':
    raise SystemExit(main())
