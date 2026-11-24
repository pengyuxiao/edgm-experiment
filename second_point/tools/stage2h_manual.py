"""Persist artificial controls separately; real S are never executed."""
import argparse
from pathlib import Path
import tempfile
from edgm_second.environment import PROJECT,run_process
from edgm_second.active_enrichment import enumerate_positions
from edgm_second.aggregate_enrichment import FAMILIES,MR_ID,plan
from edgm_second.aggregate_run import read,save,diff,execute_action
from edgm_second.source_edit import digest


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out-dir',type=Path,required=True)
    root=parser.parse_args().out_dir.resolve();assert root.is_relative_to(PROJECT)
    out=root/'manual';out.mkdir();s=out/'S.c'
    s.write_text('#include <limits.h>\nint compute(int input){return input ^ 3;}\nint main(void){int a[]={INT_MIN,INT_MAX,-1,0,1};for(int i=0;i<5;i++)if(compute(a[i])!=(a[i]^3))return 1;return 0;}\n')
    env=read(root/'environment.json');ctx,positions,_=enumerate_positions(s,env['runtime']['path']);results=[];dynamic=[]
    for family in FAMILIES:
        for count in (1,2):
            folder=out/f'{family}_{count}';folder.mkdir();r,data=plan(ctx,positions[0],family,count)
            p0=folder/'P0.c';p0.write_bytes(data);save(folder/'enrichment.json',r);diff(folder/'S_to_P0.diff',s.read_bytes(),data,s,p0)
            a=dict(candidate_id=digest((family+str(count)+digest(data)).encode()),seed_id='artificial_control',mr_id=MR_ID,
                enrichment_family=family,P0_id=digest(data),p0_file=str(p0),directory=str(folder),semantic_context_signature=r['semantic_context_signature'])
            result=execute_action(root,0,a,folder/'execution');results.append(result)
            assert result['status']=='TARGET_ERRORS_OBSERVED',result
            with tempfile.TemporaryDirectory(prefix='stage2h-control-') as tmp:
                for compiler,identity in env['compilers'].items():
                    binary=Path(tmp)/compiler;build=run_process([identity['path'],'-std=c11','-w',str(p0),'-o',str(binary)],20)
                    execution=run_process([str(binary)],5) if build['status']=='ok' else None
                    dynamic.append(dict(family=family,count=count,compiler=compiler,build=build,execution=execution))
                    assert execution and execution['returncode']==0
    save(out/'summary.json',dict(results=results,artificial_P1_count=4,real_seed_count=0));save(out/'dynamic_controls.json',dynamic)
    print('Four artificial P1, eight compiler observations and eight dynamic controls passed; excluded from real counts.')


if __name__=='__main__':main()
