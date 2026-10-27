import json, glob, math, collections, sys
root = sys.argv[1] if len(sys.argv) > 1 else '.'
c = collections.Counter()
for p in glob.glob(root + '/details/*.json'):
    d = json.load(open(p, encoding='utf-8'))
    k = d['key']['config']
    if k.get('kind') in ('baseline', 'llm-score') and 'per_event' in d['reading']:
        v = float.fromhex(d['reading']['per_event'])
        if not math.isfinite(v):
            c[(k['system'], repr(v))] += 1
print(dict(c))
