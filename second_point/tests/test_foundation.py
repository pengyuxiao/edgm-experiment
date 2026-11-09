import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from edgm_second.environment import load_config, inspect_environment, run_process, PROJECT
from edgm_second.seeds import discover_seeds, validate_seed
from edgm_second.ast_extract import extract_ast


class FoundationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = load_config()
        cls.env = inspect_environment(cls.config)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='edgm-second-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / 'seed.c'
        self.source.write_bytes('/* 中文 */ int global;\nint main(void) { int local = 0; return local; }\n'.encode())

    def test_environment_record(self):
        self.assertTrue(self.env['ready'], json.dumps(self.env, indent=2))
        restored = json.loads(json.dumps(self.env))
        self.assertTrue(restored['runtime']['available'])
        self.assertIn('version', restored['libclang'])
        for name in ('gcc', 'clang'):
            record = restored['compilers'][name]
            self.assertTrue(Path(record['path']).is_absolute())
            self.assertTrue(record['version_matches'])
            self.assertEqual(record['version_check']['returncode'], 0)

    def test_dual_compiler_and_source_immutable(self):
        before = self.source.read_bytes()
        result = validate_seed(self.source, self.env)
        self.assertEqual(result['status'], 'valid', result)
        self.assertEqual(self.source.read_bytes(), before)
        self.assertTrue(result['source_unchanged'])
        for check in result['checks'].values():
            self.assertEqual(check['command'][1:4], ['-std=c11', '-fsyntax-only', '-w'])
            self.assertEqual(check['returncode'], 0)
            self.assertGreaterEqual(check['elapsed_seconds'], 0)
            self.assertIsInstance(check['stderr'], str)

    def test_ast_utf8_byte_positions_and_depth(self):
        result = extract_ast(self.source, self.config['runtime'])
        self.assertEqual(result['status'], 'ok', result)
        data = self.source.read_bytes()
        nodes = result['nodes']
        variable = next(n for n in nodes if n['kind'] == 'VAR_DECL' and n['spelling'] == 'global')
        self.assertEqual(variable['offset'], data.index(b'global'))
        self.assertEqual((variable['line'], variable['column'], variable['depth']),
                         (1, data.index(b'global') + 1, 1))
        local = next(n for n in nodes if n['kind'] == 'VAR_DECL' and n['spelling'] == 'local')
        self.assertEqual((local['line'], local['column']), (2, 22))
        self.assertEqual(data[local['start_offset']:local['end_offset']], b'int local = 0')
        self.assertGreater(local['depth'], 1)
        self.assertIsNotNone(local['parent_id'])
        for node in nodes:
            self.assertEqual(node['file'], str(self.source.resolve()))
            self.assertEqual(node['line'], data[:node['offset']].count(b'\n') + 1)
            self.assertLessEqual(node['end_offset'], len(data))

    def test_header_same_basename_filtered_and_tu_traversed(self):
        header_dir = self.root / 'include'
        header_dir.mkdir()
        (header_dir / 'seed.c').write_text('int header_only;\n')
        self.source.write_text('#include "include/seed.c"\nint main(void){return 0;}\n')
        result = extract_ast(self.source, self.config['runtime'])
        self.assertEqual(result['status'], 'ok', result)
        self.assertNotIn('header_only', [n['spelling'] for n in result['nodes']])
        self.assertIn('main', [n['spelling'] for n in result['nodes']])

    def test_rejection_and_ast_errors(self):
        self.source.write_text('int main(void) { return undeclared; }\n')
        result = validate_seed(self.source, self.env)
        self.assertEqual(result['status'], 'rejected')
        self.assertTrue(all(c['returncode'] != 0 and c['stderr'] for c in result['checks'].values()))
        ast = extract_ast(self.source, self.config['runtime'])
        self.assertEqual(ast['status'], 'parse_error')
        self.assertTrue(ast['diagnostics'])
        self.assertEqual(ast['nodes'], [])
        self.assertEqual(validate_seed(self.root / 'missing.c', self.env)['status'], 'source_unavailable')
        self.assertEqual(extract_ast(self.root / 'missing.c', self.config['runtime'])['status'], 'parse_failed')

    def test_execution_failure_and_timeout_preserve_partial_output(self):
        self.assertEqual(run_process(['/nonexistent/edgm-second-compiler'], 1)['status'], 'execution_failed')
        with patch('edgm_second.environment.subprocess.run', side_effect=
                   subprocess.TimeoutExpired(['compiler'], 1, output=b'partial stdout', stderr=b'partial stderr')):
            result = run_process(['compiler'], 1)
        self.assertEqual(result['status'], 'timeout')
        self.assertIsNone(result['returncode'])
        self.assertEqual(result['stderr'], 'partial stderr')
        self.assertEqual(result['stdout'], 'partial stdout')

    def test_missing_environment_record(self):
        config = {**self.config, 'gcc': '/nonexistent/gcc', 'runtime': str(self.root / 'absent')}
        env = inspect_environment(config)
        self.assertFalse(env['ready'])
        self.assertEqual(env['compilers']['gcc']['status'], 'missing')
        self.assertFalse(env['runtime']['available'])
        self.assertEqual(validate_seed(self.source, env)['status'], 'environment_failed')

    def test_existing_edgm_seed_read_only(self):
        seeds, checked = discover_seeds(self.config)
        if not seeds:
            self.skipTest('No existing EDGM clean seeds; minimal fixture tested separately')
        before = hashlib.sha256(seeds[0].read_bytes()).hexdigest()
        result = validate_seed(seeds[0], self.env)
        self.assertEqual(result['status'], 'valid', result)
        self.assertEqual(hashlib.sha256(seeds[0].read_bytes()).hexdigest(), before)
        self.assertTrue(checked)

    def test_cli_rejected_p0_is_recorded_without_ast(self):
        self.source.write_text('int main(void){return undeclared;}\n')
        with tempfile.TemporaryDirectory(dir=PROJECT, prefix='test-failure-') as temporary:
            report = Path(temporary) / 'failure.json'
            process = subprocess.run([sys.executable, '-m', 'edgm_second.cli', '--seed',
                str(self.source), '--output', str(report)], capture_output=True, text=True)
            self.assertEqual(process.returncode, 1, process.stdout + process.stderr)
            record = json.loads(report.read_text())
            self.assertFalse(record['success'])
            self.assertEqual(record['validation']['status'], 'rejected')
            self.assertEqual(record['ast']['status'], 'not_run_invalid_p0')
            self.assertTrue(record['validation']['checks']['gcc']['stderr'])

    def test_cli_refuses_existing_and_external_output(self):
        before = self.source.read_bytes()
        for output in (self.source, PROJECT / 'README.md'):
            process = subprocess.run([sys.executable, '-m', 'edgm_second.cli', '--output',
                str(output)], capture_output=True, text=True)
            self.assertEqual(process.returncode, 2)
        self.assertEqual(self.source.read_bytes(), before)

    def test_missing_seed_pool_uses_explicit_fixture(self):
        config = {**self.config, 'seed_dirs': [str(self.root / 'empty')]}
        path = self.root / 'config.json'
        path.write_text(json.dumps(config))
        with tempfile.TemporaryDirectory(dir=PROJECT, prefix='test-report-') as temporary:
            report = Path(temporary) / 'fixture.json'
            result = subprocess.run([sys.executable, '-m', 'edgm_second.cli', '--config', str(path),
                                     '--output', str(report)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            record = json.loads(report.read_text())
            self.assertEqual(record['provenance'], 'temporary_minimal_fixture')
            self.assertIn('not a CsmithEdge result', record['notice'])
            self.assertEqual(record['validation']['status'], 'valid')


if __name__ == '__main__':
    unittest.main()
