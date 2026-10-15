#!/usr/bin/env bash
set -euo pipefail

mkdir -p /app

cat > /app/solution.py <<'PY'
import argparse
import glob
import json
import math
import os
import re


def split_line(line):
    if ';' in line:
        return line.split(';')
    return line.split(',')


def parse_int(field):
    s = field.replace(',', '').strip()
    m = re.match(r'-?\d+', s)
    if not m:
        return None
    return int(m.group(0))


def canon(site_id):
    return site_id.strip().upper()


def read_registry(path):
    reg = {}
    if not os.path.exists(path):
        return reg
    with open(path) as fh:
        lines = fh.read().splitlines()
    if not lines:
        return reg
    header = [t.strip().lower() for t in lines[0].split(',')]
    try:
        i_site = header.index('site_id')
        i_scope = header.index('in_scope')
    except ValueError:
        return reg
    for line in lines[1:]:
        if line.strip() == '':
            continue
        parts = line.split(',')
        if len(parts) <= max(i_site, i_scope):
            continue
        cid = canon(parts[i_site])
        reg[cid] = parts[i_scope].strip().lower() == 'true'
    return reg


def read_sites(input_dir):
    site_files = sorted(glob.glob(os.path.join(input_dir, 'sites_*.csv')))
    seen = set()
    valid_last = {}  # canonical id -> (successes, trials) last valid row
    for path in site_files:
        with open(path) as fh:
            lines = fh.read().splitlines()
        if not lines:
            continue
        header = split_line(lines[0])
        idx = {}
        for i, tok in enumerate(header):
            name = tok.strip().lower()
            if name in ('site_id', 'successes', 'trials'):
                idx[name] = i
        if not all(k in idx for k in ('site_id', 'successes', 'trials')):
            continue
        need = max(idx.values())
        for line in lines[1:]:
            if line.strip() == '':
                continue
            parts = split_line(line)
            if len(parts) <= need:
                continue
            cid = canon(parts[idx['site_id']])
            seen.add(cid)
            succ = parse_int(parts[idx['successes']])
            tri = parse_int(parts[idx['trials']])
            if succ is None or tri is None:
                continue
            if succ < 0 or tri < 0 or succ > tri:
                continue
            valid_last[cid] = (succ, tri)
    return seen, valid_last


def grid_expectations(used_groups):
    grid = [0.5 * i for i in range(1, 21)]
    logposts = []
    for a in grid:
        for b in grid:
            lp = -2.5 * math.log(a + b)
            for (k, n) in used_groups:
                lp += (math.lgamma(a + k) + math.lgamma(b + n - k)
                       - math.lgamma(a + b + n)
                       - math.lgamma(a) - math.lgamma(b) + math.lgamma(a + b))
            logposts.append((a, b, lp))
    mx = max(lp for _, _, lp in logposts)
    sw = 0.0
    sa = 0.0
    sb = 0.0
    for a, b, lp in logposts:
        w = math.exp(lp - mx)
        sw += w
        sa += a * w
        sb += b * w
    return sa / sw, sb / sw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--input', required=True)
    ap.add_argument('--output', required=True)
    args = ap.parse_args()

    seen, valid_last = read_sites(args.input)
    reg = read_registry(os.path.join(args.input, 'registry.csv'))

    kept = {cid: st for cid, st in valid_last.items() if reg.get(cid) is True}
    rejected = sorted(seen - set(kept.keys()))

    sites = []
    total_successes = 0
    total_trials = 0
    used_groups = []
    for cid in sorted(kept.keys()):
        s, t = kept[cid]
        sites.append({'site_id': cid, 'successes': s, 'trials': t})
        total_successes += s
        total_trials += t
        if t > 0:
            used_groups.append((s, t))

    alpha, beta = grid_expectations(used_groups)
    alpha_r = round(alpha, 6)
    beta_r = round(beta, 6)

    result = {
        'n_sites': len(kept),
        'used_groups': len(used_groups),
        'total_successes': total_successes,
        'total_trials': total_trials,
        'alpha': alpha_r,
        'beta': beta_r,
        'sites': sites,
        'rejected': rejected,
    }

    out_dir = os.path.dirname(os.path.abspath(args.output)) or '.'
    os.makedirs(out_dir, exist_ok=True)
    with open(args.output, 'w') as fh:
        json.dump(result, fh, indent=2)
        fh.write('\n')

    with open(os.path.join(out_dir, 'report.csv'), 'w') as fh:
        fh.write('parameter,mean,used_groups\n')
        fh.write('alpha,%.6f,%d\n' % (alpha, len(used_groups)))
        fh.write('beta,%.6f,%d\n' % (beta, len(used_groups)))

    stan = (
        'data {\n'
        '  int<lower=0> N;\n'
        '  int<lower=0> trials[N];\n'
        '  int<lower=0> successes[N];\n'
        '}\n'
        'parameters {\n'
        '  real<lower=0> alpha;\n'
        '  real<lower=0> beta;\n'
        '  vector<lower=0, upper=1>[N] theta;\n'
        '}\n'
        'model {\n'
        '  target += -2.5 * log(alpha + beta);\n'
        '  theta ~ beta(alpha, beta);\n'
        '  successes ~ binomial(trials, theta);\n'
        '}\n'
    )
    with open(os.path.join(out_dir, 'model.stan'), 'w') as fh:
        fh.write(stan)


if __name__ == '__main__':
    main()
PY

python /app/solution.py --input /app/inputs --output /app/result.json
