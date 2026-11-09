"""Bounded stage-1 inspection, writing only under second_point/."""
import argparse
import json
from pathlib import Path
import tempfile
from .environment import PROJECT, load_config, inspect_environment
from .seeds import discover_seeds, validate_seed
from .ast_extract import extract_ast


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--seed', type=Path)
    parser.add_argument('--output', type=Path, default=PROJECT / 'evidence/run.json')
    args = parser.parse_args()
    output = args.output.resolve()
    if PROJECT.resolve() not in output.parents:
        parser.error('output must be under second_point/')
    if output.exists():
        parser.error('output already exists; choose a new report name')
    config = load_config(args.config)
    env = inspect_environment(config)
    report = dict(stage=1, config=config, environment=env, seed_discovery=[])
    with tempfile.TemporaryDirectory(prefix='edgm-second-') as temporary:
        if args.seed:
            seed = args.seed.resolve()
            report['provenance'] = 'explicit_input'
        else:
            seeds, report['seed_discovery'] = discover_seeds(config)
            if seeds:
                seed = seeds[0]
                report['provenance'] = 'existing_edgm_seed_revalidated'
            else:
                seed = Path(temporary) / 'minimal.c'
                seed.write_bytes(b'int main(void) { return 0; }\n')
                report.update(provenance='temporary_minimal_fixture',
                    notice='No existing clean seeds found. This is not a CsmithEdge result.',
                    fixture_source=seed.read_text())
                print(report['notice'])
        report['validation'] = validate_seed(seed, env, config['timeout_seconds'])
        if report['validation']['status'] == 'valid':
            report['ast'] = extract_ast(seed, env['runtime']['path'])
        else:
            report['ast'] = dict(status='not_run_invalid_p0', nodes=[])
        report['success'] = env['ready'] and report['validation']['status'] == 'valid' and report['ast']['status'] == 'ok'
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps(dict(report=str(output), success=report['success'],
        validation=report['validation']['status'], ast=report['ast']['status'],
        nodes=len(report['ast']['nodes'])), ensure_ascii=False))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
