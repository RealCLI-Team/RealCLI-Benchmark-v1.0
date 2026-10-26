#!/usr/bin/env bash
set -euo pipefail

fail() { echo "FAIL: $1" >&2; exit 1; }

[ -f /app/result.json ] || fail "missing /app/result.json"
[ -f /app/report.csv ] || fail "missing /app/report.csv"
[ -f /app/model.stan ] || fail "missing /app/model.stan"
[ -f /app/solution.py ] || fail "missing /app/solution.py"

# model.stan required substrings
grep -q "binomial(trials, theta)" /app/model.stan || fail "model.stan missing binomial likelihood"
grep -q "beta(alpha, beta)" /app/model.stan || fail "model.stan missing group prior"
grep -qF "target += -2.5 * log(alpha + beta);" /app/model.stan || fail "model.stan missing hyperprior line"

python3 - <<'PY'
import glob, json, math, os, re, sys

INPUT = '/app/inputs'

def split_line(line):
    return line.split(';') if ';' in line else line.split(',')

def parse_int(field):
    s = field.replace(',', '').strip()
    m = re.match(r'-?\d+', s)
    return int(m.group(0)) if m else None

def canon(x):
    return x.strip().upper()

# --- reference recomputation ---
seen = set()
valid_last = {}
for path in sorted(glob.glob(os.path.join(INPUT, 'sites_*.csv'))):
    lines = open(path).read().splitlines()
    header = split_line(lines[0])
    idx = {}
    for i, tok in enumerate(header):
        n = tok.strip().lower()
        if n in ('site_id', 'successes', 'trials'):
            idx[n] = i
    need = max(idx.values())
    for line in lines[1:]:
        if line.strip() == '':
            continue
        parts = split_line(line)
        if len(parts) <= need:
            continue
        cid = canon(parts[idx['site_id']])
        seen.add(cid)
        s = parse_int(parts[idx['successes']])
        t = parse_int(parts[idx['trials']])
        if s is None or t is None or s < 0 or t < 0 or s > t:
            continue
        valid_last[cid] = (s, t)

reg = {}
rlines = open(os.path.join(INPUT, 'registry.csv')).read().splitlines()
rh = [x.strip().lower() for x in rlines[0].split(',')]
si, sc = rh.index('site_id'), rh.index('in_scope')
for line in rlines[1:]:
    if line.strip() == '':
        continue
    p = line.split(',')
    reg[canon(p[si])] = p[sc].strip().lower() == 'true'

kept = {c: v for c, v in valid_last.items() if reg.get(c) is True}
rejected = sorted(seen - set(kept))
used = [(s, t) for (s, t) in kept.values() if t > 0]

grid = [0.5 * i for i in range(1, 21)]
sw = sa = sb = 0.0
vals = []
for a in grid:
    for b in grid:
        lp = -2.5 * math.log(a + b)
        for (k, n) in used:
            lp += (math.lgamma(a + k) + math.lgamma(b + n - k)
                   - math.lgamma(a + b + n)
                   - math.lgamma(a) - math.lgamma(b) + math.lgamma(a + b))
        vals.append((a, b, lp))
mx = max(v[2] for v in vals)
for a, b, lp in vals:
    w = math.exp(lp - mx)
    sw += w; sa += a * w; sb += b * w
exp_alpha = sa / sw
exp_beta = sb / sw

exp_n = len(kept)
exp_used = len(used)
exp_ts = sum(s for s, t in kept.values())
exp_tt = sum(t for s, t in kept.values())
exp_sites = [{'site_id': c, 'successes': kept[c][0], 'trials': kept[c][1]} for c in sorted(kept)]

# --- independent hardcoded expectations (manual) ---
assert exp_n == 4, exp_n
assert exp_used == 3, exp_used
assert exp_ts == 1035, exp_ts
assert exp_tt == 2037, exp_tt
assert rejected == ['S03', 'S04', 'S06', 'X'], rejected
assert sorted(kept) == ['S01', 'S02', 'S05', 'S07'], sorted(kept)
assert kept['S01'] == (7, 25), kept['S01']
assert kept['S02'] == (4, 12), kept['S02']
assert kept['S05'] == (1024, 2000), kept['S05']
assert kept['S07'] == (0, 0), kept['S07']

# --- compare against produced result.json ---
res = json.load(open('/app/result.json'))
def check(cond, msg):
    if not cond:
        print('FAIL: ' + msg); sys.exit(1)

check(res.get('n_sites') == exp_n, 'n_sites %r != %r' % (res.get('n_sites'), exp_n))
check(res.get('used_groups') == exp_used, 'used_groups mismatch')
check(res.get('total_successes') == exp_ts, 'total_successes mismatch')
check(res.get('total_trials') == exp_tt, 'total_trials mismatch')
check(res.get('rejected') == rejected, 'rejected %r != %r' % (res.get('rejected'), rejected))
check(res.get('sites') == exp_sites, 'sites %r != %r' % (res.get('sites'), exp_sites))
check(abs(res.get('alpha') - round(exp_alpha, 6)) < 1e-6, 'alpha %r != %r' % (res.get('alpha'), round(exp_alpha, 6)))
check(abs(res.get('beta') - round(exp_beta, 6)) < 1e-6, 'beta %r != %r' % (res.get('beta'), round(exp_beta, 6)))

# --- report.csv ---
rc = open('/app/report.csv').read().splitlines()
check(rc[0] == 'parameter,mean,used_groups', 'report header wrong: %r' % rc[0])
ra = rc[1].split(',')
rb = rc[2].split(',')
check(ra[0] == 'alpha' and rb[0] == 'beta', 'report row order wrong')
check(int(ra[2]) == exp_used and int(rb[2]) == exp_used, 'report used_groups wrong')
check(abs(float(ra[1]) - exp_alpha) < 1e-6, 'report alpha mean wrong')
check(abs(float(rb[1]) - exp_beta) < 1e-6, 'report beta mean wrong')
# exactly 6 decimals formatting
check(re.match(r'^-?\d+\.\d{6}$', ra[1]) is not None, 'report alpha not 6dp: %r' % ra[1])
check(re.match(r'^-?\d+\.\d{6}$', rb[1]) is not None, 'report beta not 6dp: %r' % rb[1])

print('ALL TESTS PASSED')
print('alpha=%.6f beta=%.6f' % (exp_alpha, exp_beta))
PY
