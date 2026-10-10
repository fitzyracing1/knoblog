#!/usr/bin/env python3
"""Compare knoblog entries.json against the x-algorithm-changelog site: python scripts/parity_x_algorithm.py NEW_entries.json [OLD_entries.json]"""
import json, sys
old = json.load(open(sys.argv[2] if len(sys.argv) > 2 else '../x-algorithm-changelog/site/entries.json'))['entries']
new = json.load(open(sys.argv[1]))['entries']
print('entries old/new', len(old), len(new), 'same shas', [e['sha'] for e in old] == [e['sha'] for e in new])
nb = {e['sha']: e for e in new}
mism = 0
for o in old:
    n = nb.get(o['sha'])
    if not n: print('missing', o['short']); continue
    P = o['params']
    oc = {('changed', x['name'], x['old'], x['new']) for x in P['changed']}
    oc |= {('added', x['name'], None, x['default']) for x in P['added']}
    oc |= {('removed', x['name'], x['default'], None) for x in P['removed']}
    oc |= {('moved', x['name'], x.get('old'), x.get('new')) for x in P.get('moved', [])}
    nc = {(c['kind'], c['name'], c['old'], c['new']) for c in n['changes'] if c['extractor'] == 'rust_param'}
    ok_p = oc == nc
    K = o['constants']
    ok_ = {(x['name'], x['old'], x['new'], x['file']) for x in K['changed']}
    nk = {(c['name'], c['old'], c['new'], c['file']) for c in n['changes'] if c['extractor'] != 'rust_param' and c['kind'] == 'changed'}
    ok_k = ok_ == nk
    oco = {(k, x['kind'], x.get('path')) for k in ('added', 'removed', 'renamed', 'changed') for x in o['components'][k]}
    nco = {(k, x['kind'], x.get('path')) for k in ('added', 'removed', 'renamed', 'changed') for x in n['components'][k]}
    ok_c = oco == nco
    if not (ok_p and ok_k and ok_c):
        mism += 1
        print(o['short'], 'params' if not ok_p else '', 'consts' if not ok_k else '', 'comps' if not ok_c else '')
        if not ok_p: print('  old-only', sorted(oc - nc, key=str)[:5]); print('  new-only', sorted(nc - oc, key=str)[:5])
        if not ok_k: print('  old-only', sorted(ok_ - nk, key=str)[:5]); print('  new-only', sorted(nk - ok_, key=str)[:5])
        if not ok_c: print('  old-only', sorted(oco - nco)[:5]); print('  new-only', sorted(nco - oco)[:5])
print('mismatching entries:', mism)
