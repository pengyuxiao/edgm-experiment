"""Probe the existing environment without changing it."""
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

PROJECT = Path(__file__).resolve().parents[2]
EDGM = PROJECT.parent


def load_config(path=None):
    return json.loads(Path(path or PROJECT / 'config/environment.json').read_text())


def _text(value):
    return value.decode('utf-8', 'replace') if isinstance(value, bytes) else value or ''


def run_process(command, timeout):
    """Keep raw streams, real return codes and partial timeout output."""
    start = time.perf_counter()
    result = dict(command=list(map(str, command)), returncode=None, stdout='', stderr='',
                  status='execution_failed', exception=None)
    try:
        process = subprocess.run(result['command'], capture_output=True, timeout=timeout,
                                 env={**os.environ, 'LC_ALL': 'C'})
        result.update(returncode=process.returncode, stdout=_text(process.stdout),
                      stderr=_text(process.stderr),
                      status='ok' if process.returncode == 0 else
                      ('crashed' if process.returncode < 0 else 'nonzero'))
    except subprocess.TimeoutExpired as exc:
        result.update(status='timeout', stdout=_text(exc.stdout), stderr=_text(exc.stderr),
                      exception=str(exc))
    except OSError as exc:
        result['exception'] = f'{type(exc).__name__}: {exc}'
    result['elapsed_seconds'] = time.perf_counter() - start
    return result


def probe_libclang():
    result = dict(status='unavailable')
    try:
        from clang import cindex
        result['binding_path'] = cindex.__file__
        index = cindex.Index.create()
        tu = index.parse('environment_probe.c', args=['-std=c11'],
                         unsaved_files=[('environment_probe.c', 'int main(void){return 0;}')])
        result['probe_diagnostics'] = [str(d) for d in tu.diagnostics]
        library = cindex.conf.lib
        result['library_name'] = library._name
        # ctypes must use the binding's CXString return conversion.
        version = library.clang_getClangVersion
        version.restype = cindex._CXString
        version.errcheck = cindex._CXString.from_result
        result['version'] = version()
        result['status'] = 'ok' if not any(d.severity >= 3 for d in tu.diagnostics) else 'parse_error'
        if Path('/proc/self/maps').exists():
            result['loaded_library_paths'] = sorted({line.split()[-1] for line in
                Path('/proc/self/maps').read_text().splitlines()
                if 'libclang' in line and line.split()[-1].startswith('/')})
    except Exception as exc:
        result['exception'] = f'{type(exc).__name__}: {exc}'
    return result


def inspect_environment(config):
    compilers = {}
    for name in ('gcc', 'clang'):
        found = shutil.which(config[name])
        entry = dict(requested=config[name], path=str(Path(found).resolve()) if found else None,
                     expected_version=config['expected_versions'][name], status='missing')
        if found:
            entry['version_check'] = run_process([entry['path'], '--version'], 10)
            version = entry['version_check']['stdout'].splitlines()
            entry['version'] = version[0] if version else ''
            entry['version_matches'] = bool(re.search(
                r'(?<![\d.])' + re.escape(entry['expected_version']) + r'(?![\d.])', entry['version']))
            entry['status'] = ('ok' if entry['version_check']['status'] == 'ok'
                               and entry['version_matches'] else 'version_mismatch_or_failure')
        compilers[name] = entry
    runtime = Path(config['runtime']).resolve()
    result = dict(created_utc=datetime.now(timezone.utc).isoformat(), python=sys.executable,
                  platform=platform.platform(), compilers=compilers,
                  runtime=dict(path=str(runtime), header=str(runtime / 'csmith.h'),
                               available=(runtime / 'csmith.h').is_file()), libclang=probe_libclang())
    result['ready'] = (all(c['status'] == 'ok' for c in compilers.values())
                       and result['runtime']['available'] and result['libclang']['status'] == 'ok')
    return result
