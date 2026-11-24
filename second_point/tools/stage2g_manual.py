"""Persist the three controlled examples separately from the 50-seed corpus."""
import json
import difflib
import argparse
from pathlib import Path
import tempfile
from edgm_second.environment import PROJECT,run_process
from edgm_second.active_enrichment import FAMILIES,enumerate_positions,plan_enrichment
from edgm_second.active_oracle import scan_prepared
from edgm_second.active_run import save,verify_pool
from edgm_second.seeds import validate_seed
from edgm_second.source_edit import digest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out-dir',type=Path,default=PROJECT/'evidence/stage2g')
    root=parser.parse_args().out_dir.resolve()
    if not root.is_relative_to(PROJECT):parser.error('output must be within second_point')
    out=root/'manual';out.mkdir()
    env=json.loads((root/'environment.json').read_text());profile=json.loads((root/'integer_profile.json').read_text())
    save(out/'environment.json',env);save(out/'integer_profile.json',profile)
    source=out/'S.c';source.write_text('''#include <limits.h>
int compute(int input){return input ^ 3;}
int main(void){int a[]={INT_MIN,INT_MAX,-1,0,1};for(int i=0;i<5;i++)if(compute(a[i])!=(a[i]^3))return 1;return 0;}
''')
    ctx,positions,reasons=enumerate_positions(source,env['runtime']['path']);actions=[];dynamic=[]
    for family in FAMILIES:
        folder=out/family;folder.mkdir();record,data=plan_enrichment(ctx,positions[0],family)
        p0=folder/'P0.c';p0.write_bytes(data);save(folder/'enrichment.json',record)
        (folder/'S_to_P0.diff').write_text(''.join(difflib.unified_diff(source.read_text().splitlines(True),data.decode().splitlines(True),fromfile=str(source),tofile=str(p0))))
        admission=validate_seed(p0,env);save(folder/'P0_validation.json',admission)
        assert admission['status']=='valid'
        scan=scan_prepared(p0,record,env['runtime']['path'],profile);save(folder/'formal_scan.json',scan);assert scan['status']=='APPLICABLE'
        actions.append(dict(candidate_id=digest((family+record['p0_sha256']).encode()),seed_id='manual_control',seed_index=1,
            enrichment_family=family,position_id=positions[0]['position_id'],mr_id=record['mr_id'],p0_file=str(p0),directory=str(folder)))
        with tempfile.TemporaryDirectory(prefix='stage2g-range-') as tmp:
            for name,c in env['compilers'].items():
                binary=Path(tmp)/name
                build=run_process([c['path'],'-std=c11','-w',str(p0),'-o',str(binary)],20)
                execution=run_process([str(binary)],5) if build['status']=='ok' else None
                dynamic.append(dict(family=family,compiler=name,build=build,execution=execution))
                assert execution and execution['returncode']==0
    save(out/'candidate_pool.json',dict(valid_candidates=actions,origin='controlled artificial fixtures, excluded from real corpus counts'))
    save(out/'dynamic_controls.json',dynamic);verify_pool(out)
    print('Three manual families validated; six controlled dynamic builds/runs passed; no real seed executed.')


if __name__=='__main__':main()
