"""Frozen local diagnostic vocabulary; exact coordinates, no Oracle feedback."""
import re
from pathlib import Path
from .active_validation import observe_active
from .mr_member_const_aggregate_write import CATEGORY,MR_ID


def observe(process,w):
    if w['mr_id']!=MR_ID:
        result=observe_active(process,w)
        # Preserve the old observer's label while distinguishing category from
        # location/count in this batch's necessary common summary.
        old=result['status']
        if old=='TARGET_LOCATION_OR_COUNT_REVIEW':
            result['status']='TARGET_COVERAGE_REVIEW' if len(result['errors'])!=1 else 'TARGET_LOCATION_REVIEW'
        if old=='UNRELATED_ERROR_REVIEW' and result['errors'] and all(e.get('matched_anchors') or e.get('exact_target_anchor') for e in result['errors']):
            result['status']='TARGET_CATEGORY_REVIEW'
        if old=='TARGET_ERRORS_OBSERVED' and 'BITFIELD' in w['mr_id'] and len(result['errors'])!=len(w['affected_uses']):
            result['status']='TARGET_COVERAGE_REVIEW'
        if result['status']!=old:result['observer_original_status']=old
        return result
    errors=[]
    for line in process['stderr'].splitlines():
        m=re.match(r'^(.*):(\d+):(\d+): (fatal error|error): (.*)$',line)
        if not m:continue
        file,row,col,severity,message=m.groups();matches=[]
        known=bool(re.fullmatch(r"assignment of read-only variable '[^']+'",message) or
                   re.fullmatch(r"cannot assign to variable '[^']+' with (?:nested )?const-qualified data member '[^']+'",message))
        for i,use in enumerate(w['affected_uses']):
            # Predetermined token anchors (lhs and =); never nearest-column matching.
            for key in ('lhs','operator'):
                a=use[key]['p1']
                if (str(Path(file).resolve()),int(row),int(col))==(a['file'],a['line'],a['column']):matches.append(dict(violation=i,anchor=key))
        errors.append(dict(file=file,line=int(row),column=int(col),severity=severity,message=message,raw=line,
            category=CATEGORY if known else 'unknown',matched_anchors=matches))
    parsed={e['raw'] for e in errors}
    unparsed=[line for line in process['stderr'].splitlines() if re.match(r'^(?:[^:\n|]+(?::\d+){0,2}:\s*)?(?:fatal )?error:\s',line) and line not in parsed]
    if process['status'] in ('timeout','crashed','execution_failed'):status={'timeout':'COMPILER_TIMEOUT','crashed':'COMPILER_CRASH','execution_failed':'EXECUTION_FAILED'}[process['status']]
    elif 'internal compiler error:' in process['stderr'] or 'PLEASE submit a bug report' in process['stderr']:status='COMPILER_CRASH'
    elif unparsed:status='UNPARSED_ERROR_REVIEW'
    elif not errors or process['returncode']==0:status='MISSING_TARGET_ERROR_CANDIDATE'
    elif any(e['category']=='unknown' for e in errors):status='TARGET_CATEGORY_REVIEW' if all(e['matched_anchors'] for e in errors) else 'UNRELATED_ERROR_REVIEW'
    elif any(not e['matched_anchors'] for e in errors):status='TARGET_LOCATION_REVIEW'
    elif len(errors)!=len(w['affected_uses']) or {m['violation'] for e in errors for m in e['matched_anchors']}!=set(range(len(w['affected_uses']))):status='TARGET_COVERAGE_REVIEW'
    else:status='TARGET_ERRORS_OBSERVED'
    return dict(status=status,errors=errors,unparsed_error_headers=unparsed,confirmed_bug=False,exact_coordinate_comparison=True)
