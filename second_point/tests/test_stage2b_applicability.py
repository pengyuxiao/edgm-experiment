import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from edgm_second.environment import PROJECT, load_config, inspect_environment
from edgm_second.seeds import validate_seed
from edgm_second.source_edit import digest
from edgm_second.mr_typedef_indirect_struct_arg import scan_candidates, build_mutation, Analysis
from edgm_second.constraint_witness import verify_constraint_witness
from edgm_second.applicability_run import (choose_sources, isolated_scan, summarize, stable_results,
                                           run_corpus, verify_real_candidates)

FIXTURE = PROJECT / 'examples/stage2a/P0.c'


class Stage2BTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_config()
        cls.env = inspect_environment(cls.config)

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(dir=PROJECT, prefix='stage2b-test-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.p0 = self.root / 'P0.c'
        self.p0.write_bytes(FIXTURE.read_bytes())

    def scan(self):
        return scan_candidates(self.p0, self.config['runtime'])

    def receipt(self):
        receipt = validate_seed(self.p0, self.env)
        self.assertEqual(receipt['status'], 'valid', receipt)
        return receipt

    def test_all_call_sites_and_identities_not_names(self):
        self.p0.write_text(FIXTURE.read_text().replace('typedef ArgumentBase Argument;',
            'typedef ArgumentBase Argument;\ntypedef Stable OtherBase;\ntypedef OtherBase OtherArgument;').replace(
            '    return dispatch.invoke(value);',
            '    { OtherArgument other = {0}; (void)dispatch.invoke(other); }\n    return dispatch.invoke(value);'))
        scan = self.scan()
        self.assertEqual(scan['candidate_count'], 2, scan)
        self.assertEqual(scan['status'], 'APPLICABLE')
        self.assertEqual(scan['inventory']['struct_function_pointer_members'], 1)
        self.assertTrue(all(c['typedef_chain_length'] == 3 for c in scan['candidates']))
        self.assertNotEqual(scan['candidates'][0]['candidate_id'], scan['candidates'][1]['candidate_id'])
        for candidate in scan['candidates']:
            self.assertEqual(candidate['call']['file'], str(self.p0.resolve()))

    def test_long_typedef_chain(self):
        self.p0.write_text(FIXTURE.read_text().replace('typedef ArgumentBase Argument;',
                'typedef ArgumentBase More;\ntypedef More Argument;'))
        scan = self.scan()
        self.assertEqual(scan['status'], 'APPLICABLE')
        self.assertEqual(scan['candidates'][0]['typedef_chain_length'], 4)

    def test_repeated_compatible_typedef_rejected_after_audit_fix(self):
        self.p0.write_text(FIXTURE.read_text().replace('typedef Stable ArgumentBase;',
                          'typedef Stable ArgumentBase;\ntypedef Stable ArgumentBase;'))
        receipt = self.receipt()
        scan = self.scan()
        self.assertEqual(scan['status'], 'NOT_APPLICABLE', scan)
        self.assertIn('AFFECTED_TYPEDEF_REDECLARED', scan['reasons'][0])
        result = build_mutation(self.p0, self.root / 'P1.c', self.config['runtime'], receipt)
        self.assertEqual(result['status'], 'NOT_APPLICABLE')

    def test_same_spelled_variable_in_distinct_scope_is_not_same_identity(self):
        self.p0.write_text(FIXTURE.read_text().replace('    return dispatch.invoke(value);',
                    '    { int value = 3; (void)value; }\n    return dispatch.invoke(value);'))
        self.receipt()
        self.assertEqual(self.scan()['status'], 'APPLICABLE')

    def test_local_typedef_shadowing_not_confused_with_global(self):
        self.p0.write_text(FIXTURE.read_text().replace('    return dispatch.invoke(value);',
            '    { typedef int Argument; Argument unrelated = 0; (void)unrelated; }\n    return dispatch.invoke(value);'))
        self.receipt()
        self.assertEqual(self.scan()['status'], 'APPLICABLE')

    def test_shared_typedef_extra_use_is_not_usable(self):
        self.p0.write_text(FIXTURE.read_text().replace('    Argument value = { 0 };',
                        '    Argument value = { 0 }; Argument second = {0}; (void)second;'))
        self.assertEqual(self.scan()['status'], 'NOT_APPLICABLE')

    def test_readonly_and_reproducible_scan(self):
        before = self.p0.read_bytes()
        first, second = self.scan(), self.scan()
        for value in (first, second):
            value.pop('timing_seconds')
            value.pop('elapsed_seconds')
        self.assertEqual(first, second)
        self.assertEqual(self.p0.read_bytes(), before)

    def test_selection_fixed_seed_and_hash_deduplication(self):
        for i in range(8):
            (self.root / f'{i}.c').write_text(f'int main(void){{return {i};}}')
        (self.root / 'duplicate.c').write_bytes(self.p0.read_bytes())
        first, manifest = choose_sources([self.root], 5, 20261108)
        second, _ = choose_sources([self.root], 5, 20261108)
        self.assertEqual(first, second)
        self.assertEqual(len({i['sha256'] for i in first}), 5)
        self.assertEqual(manifest['unique_files'], 9)
        self.assertEqual(manifest['input_files'], 10)

    def test_parse_failure_not_nonapplicable_and_worker_exception_separate(self):
        self.p0.write_text('int main(void){return missing;}')
        result = isolated_scan(self.p0, self.config['runtime'], 10)
        self.assertEqual(result['status'], 'PARSE_FAILED', result)
        self.assertEqual(result['ast_status'], 'ERROR_DIAGNOSTICS')
        self.assertIsNone(result['candidate_count'])
        with patch('edgm_second.applicability.Analysis', side_effect=RuntimeError('intentional failure')):
            from edgm_second.applicability import analyze_candidates
            self.assertEqual(analyze_candidates(self.p0, self.config['runtime'])['status'], 'SCAN_FAILED')

    def test_timeout_preserves_ast_milestone_and_is_excluded(self):
        event = json.dumps(dict(event='ast_ready', ast_status='OK', main_file_nodes=44)) + '\n'
        with patch('edgm_second.applicability_run.subprocess.run', side_effect=
                   subprocess.TimeoutExpired(['worker'], 1, output=event.encode(), stderr=b'partial')):
            result = isolated_scan(self.p0, self.config['runtime'], 1)
        self.assertEqual(result['status'], 'SCAN_TIMEOUT')
        self.assertEqual(result['ast_status'], 'OK')
        self.assertEqual(result['timeout_phase'], 'candidate_scan')
        self.assertEqual(result['worker_process']['stderr'], 'partial')
        records = [dict(validation=dict(status='valid'), source_unchanged=True, scan=result)]
        stats = summarize(records)
        self.assertEqual(stats['effective_seeds'], 0)
        self.assertIsNone(stats['applicability_rate'])
        self.assertEqual(stats['status_counts']['SCAN_TIMEOUT'], 1)

    def test_real_worker_deadline_and_crash_not_nonapplicable(self):
        result = isolated_scan(self.p0, self.config['runtime'], 0.000001)
        self.assertEqual(result['status'], 'SCAN_TIMEOUT')
        fake = subprocess.CompletedProcess([], -11, b'', b'worker crash')
        with patch('edgm_second.applicability_run.subprocess.run', return_value=fake):
            result = isolated_scan(self.p0, self.config['runtime'], 1)
        self.assertEqual(result['status'], 'WORKER_FAILED')
        self.assertIsNone(result['candidate_count'])

    def test_header_candidates_do_not_enter_main_file_scan(self):
        header = self.root / 'header.h'
        header.write_bytes(self.p0.read_bytes().replace(b'int main(void)', b'int helper(void)'))
        self.p0.write_text('#include "header.h"\nint main(void){return 0;}\n')
        self.receipt()
        scan = self.scan()
        self.assertEqual(scan['status'], 'NOT_APPLICABLE')
        self.assertEqual(scan['inventory']['struct_function_pointer_members'], 0)
        self.assertEqual(scan['inventory']['struct_definitions'], 0)

    def test_zero_candidate_cli_exits_success_and_no_mutation(self):
        # Use the same command entry but controlled source selection, not a corpus experiment.
        from edgm_second import applicability_run as runner
        self.p0.write_text('int main(void){return 0;}\n')
        before = digest(self.p0.read_bytes())
        config = {**self.config, 'seed_dirs': [str(self.root)]}
        out = self.root / 'out'
        with (patch.object(sys, 'argv', ['applicability_run', '--out-dir', str(out)]),
              patch.object(runner, 'load_config', return_value=config)):
            self.assertEqual(runner.main(), 0)
        summary = json.loads((out / 'summary.json').read_text())
        self.assertEqual(summary['effective_seeds'], 1)
        self.assertEqual(summary['applicability_rate'], 0)
        self.assertEqual(summary['real_mutations_performed'], 0)
        self.assertEqual(summary['nonapplicable_primary_reason_counts'], {'NO_STRUCT_FUNCTION_POINTER_MEMBER': 1})
        self.assertEqual(digest(self.p0.read_bytes()), before)
        self.assertTrue((out / 'records.csv').is_file())

    def test_admission_failure_excluded_from_denominator(self):
        scan = self.scan()
        good = dict(validation=dict(status='valid'), source_unchanged=True, scan=scan)
        bad = dict(validation=dict(status='check_failed'), source_unchanged=True,
                   scan=dict(status='VALIDATION_FAILED', ast_status='NOT_RUN', candidate_count=None))
        stats = summarize([good, bad])
        self.assertEqual(stats['effective_seeds'], 1)
        self.assertEqual(stats['applicability_rate'], 1)

    def test_witness_metadata_edit_and_anchor_tamper_rejected(self):
        receipt = self.receipt()
        result = build_mutation(self.p0, self.root / 'P1.c', self.config['runtime'], receipt)
        original = result['witness'].to_dict()
        for kind in ('declaration', 'missing_preconditions', 'file', 'bounds', 'severity'):
            witness = copy.deepcopy(original)
            if kind == 'declaration':
                witness['changed_declaration']['new_dependency_usr'] = witness['expected_parameter_type']['declaration_usr']
            elif kind == 'missing_preconditions':
                witness['preconditions'] = []
            elif kind == 'file':
                witness['source_anchors']['argument']['p1']['file'] = '/other.c'
            elif kind == 'bounds':
                witness['source_anchors']['argument']['p1']['end_offset'] = len(result['p1']) + 1
            else:
                witness['expected_error']['required_severities'] = ['warning']
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                verify_constraint_witness(witness, self.p0.read_bytes(), result['p1'])

    def test_candidate_validation_branch_on_fixture_preserves_input_and_witness(self):
        # Test the bounded branch on the explicit small fixture, never call it a real seed result.
        receipt = self.receipt()
        record = dict(source=str(self.p0), sha256=digest(self.p0.read_bytes()), validation=receipt,
                      source_unchanged=True, scan=self.scan())
        with patch('edgm_second.applicability_run.validate_seed', wraps=validate_seed):
            results = verify_real_candidates(self.root, [record], self.env, self.config['runtime'], 20)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['status'], 'EXECUTED_REQUIRES_REVIEW', results)
        self.assertTrue(results[0]['source_unchanged'])
        self.assertTrue(results[0]['witness_unchanged'])
        self.assertTrue(all(o['target_error_observed'] and o['unclassified_error_count'] == 0
                            for o in results[0]['observations'].values()))


    def test_real_validation_limit_is_three_distinct_fixture_sources(self):
        records = []
        for index in range(4):
            path = self.root / f'fixture_{index}.c'
            path.write_bytes(FIXTURE.read_bytes() + f'/* variant {index} */\n'.encode())
            receipt = validate_seed(path, self.env)
            records.append(dict(source=str(path), sha256=digest(path.read_bytes()), validation=receipt,
                source_unchanged=True, scan=scan_candidates(path, self.config['runtime'])))
        results = verify_real_candidates(self.root, records, self.env, self.config['runtime'], 20)
        self.assertEqual(len(results), 3)
        self.assertEqual(len(list(self.root.glob('mutation_*'))), 3)
        self.assertTrue(all(r['status'] == 'EXECUTED_REQUIRES_REVIEW' for r in results))
