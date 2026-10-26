#!/usr/bin/env bash
set -euo pipefail
python3 - <<'PY'
from __future__ import annotations
import json, os, shutil, stat
from pathlib import Path
root=Path('/app/project')
def mode(p): return format(stat.S_IMODE(p.stat().st_mode),'03o')
deleted=[]
for rel in ['cache','build','logs/old.log']:
    p=root/rel
    if p.exists():
        if p.is_dir():
            for f in sorted(x for x in p.rglob('*') if x.is_file()): deleted.append(f.relative_to(root).as_posix())
            shutil.rmtree(p)
        else: deleted.append(rel); p.unlink()
fixes=[]; ignored=[]
for p in sorted(root.rglob('*')):
    rel=p.relative_to(root).as_posix()
    if rel.startswith('backup/') or rel=='backup': ignored.append(rel); continue
    if p.is_dir(): want='755'
    elif p.is_file(): want='755' if p.name.endswith('.sh') else '600' if ('secrets' in p.relative_to(root).parts or p.name.endswith('.env')) else '644'
    else: continue
    old=mode(p)
    if old!=want: os.chmod(p,int(want,8)); fixes.append({'path':rel,'old_mode':old,'new_mode':want})
manifest={'deleted':sorted(deleted),'permission_fixes':fixes,'ignored':sorted(ignored),'summary':{'deleted_count':len(deleted),'permission_fix_count':len(fixes),'ignored_count':len(ignored)}}
Path('/app/cleanup_manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True)+'\n', encoding='utf-8')
PY
