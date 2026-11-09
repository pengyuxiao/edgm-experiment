"""Read-only seed discovery and dual-compiler acceptance checks."""
import hashlib
from pathlib import Path
from .environment import EDGM, run_process


def discover_seeds(config):
    checked = []
    for relative in config['seed_dirs']:
        directory = (EDGM / relative).resolve()
        files = sorted(p.resolve() for p in directory.glob('*.c') if p.is_file())
        checked.append(dict(path=str(directory), count=len(files)))
        if files:
            return files, checked
    return [], checked


def validate_seed(path, environment, timeout=20):
    path = Path(path).resolve()
    result = dict(source=str(path), status='source_unavailable', checks={})
    try:
        original = path.read_bytes()
    except OSError as exc:
        result['exception'] = f'{type(exc).__name__}: {exc}'
        return result
    result['sha256'] = hashlib.sha256(original).hexdigest()
    for name in ('gcc', 'clang'):
        identity = environment['compilers'][name]
        command = [identity['path'] or identity['requested'], '-std=c11', '-fsyntax-only', '-w',
                   '-I', environment['runtime']['path'], str(path)]
        result['checks'][name] = run_process(command, timeout)
    try:
        result['source_unchanged'] = path.read_bytes() == original
    except OSError as exc:
        result.update(status='source_unavailable', source_unchanged=False,
                      exception=f'{type(exc).__name__}: {exc}')
        return result
    if not result['source_unchanged']:
        result['status'] = 'source_changed'
    elif not all(environment['compilers'][c]['status'] == 'ok' for c in ('gcc', 'clang')):
        result['status'] = 'environment_failed'
    elif not environment['runtime']['available']:
        result['status'] = 'environment_failed'
    elif any(c['status'] in ('timeout', 'execution_failed', 'crashed') for c in result['checks'].values()):
        result['status'] = 'check_failed'
    else:
        result['status'] = 'valid' if all(c['status'] == 'ok' for c in result['checks'].values()) else 'rejected'
    return result
