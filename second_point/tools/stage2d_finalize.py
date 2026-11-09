"""Finalize stage 2D evidence without regenerating the real corpus or any P1."""
import difflib
import json
from pathlib import Path
from edgm_second.environment import PROJECT, EDGM, load_config, inspect_environment
from edgm_second.source_edit import digest
from edgm_second.seed_enrichment import enrich_seed, plan_enrichment
from edgm_second.seeds import validate_seed
from edgm_second.enrichment_run import dynamic_fixture, save, isolated

out=PROJECT/'evidence/stage2d/final_checks';out.mkdir(exist_ok=False)
environment=inspect_environment(load_config());save(out/'environment.json',environment)
source=PROJECT/'examples/stage2d/original.c';enhanced=out/'minimal_P0.c'
r=enrich_seed(source,enhanced,load_config()['runtime'])
r['original_validation']=validate_seed(source,environment)
r['enhanced_validation']=validate_seed(enhanced,environment)
r['dynamic']=dynamic_fixture(source,enhanced,environment,out)
r['diff']=''.join(difflib.unified_diff(source.read_text().splitlines(True),enhanced.read_text().splitlines(True),fromfile=str(source),tofile=str(enhanced)))
save(out/'minimal.json',r);(out/'minimal.diff').write_text(r['diff'])
assert r['status']=='ENRICHED_UNVALIDATED',r
assert all(r[k]['status']=='valid' for k in ('original_validation','enhanced_validation'))
assert all(v['equal'] for v in r['dynamic'].values())
# One repeat per interface, not another 50-seed scan or extra real-seed enrichment.
first=json.loads((PROJECT/'evidence/stage2d/experiment/native_records.json').read_text())[0]
repeat=isolated('native',first['source'],load_config()['runtime'])
def project(value):return {k:v for k,v in value.items() if k not in ('worker','elapsed_seconds')}
assert project(first['native'])==project(repeat)
plan_a=plan_enrichment(first['source'],load_config()['runtime']);plan_b=plan_enrichment(first['source'],load_config()['runtime'])
assert plan_a==plan_b
save(out/'reproducibility.json',dict(native_repeat_equal=True,plan_repeat_equal=True,seed=first['source'],native_repeat=repeat,plan=plan_a))
originals=json.loads((PROJECT/'evidence/original_hashes.json').read_text())
changed=[name for name,sha in originals.items() if not (EDGM/name).exists() or digest((EDGM/name).read_bytes())!=sha]
save(out/'readonly_check.json',dict(original_files_checked=len(originals),changed_or_deleted=changed))
assert not changed
modules=['src/edgm_second/ast_extract.py','src/edgm_second/seed_enrichment.py','src/edgm_second/native_bitfield_candidates.py',
         'src/edgm_second/enrichment_run.py','tests/test_stage2d_enrichment.py','tools/stage2d_finalize.py']
save(out/'implementation_manifest.json',{name:digest((PROJECT/name).read_bytes()) for name in modules})
print(json.dumps(dict(minimal_status=r['status'],dynamic_equal={k:v['equal'] for k,v in r['dynamic'].items()},original_files_unchanged=len(originals),reproducible=True)))
