from __future__ import annotations
import json, stat
from pathlib import Path
ROOT=Path('/app/project'); OUT=Path('/app/cleanup_manifest.json')
def mode(p): return format(stat.S_IMODE(p.stat().st_mode),'03o')
def main():
    assert not (ROOT/'cache').exists(); assert not (ROOT/'build').exists(); assert not (ROOT/'logs/old.log').exists()
    assert (ROOT/'backup/archive/cache.tmp').exists(); assert mode(ROOT/'backup/archive/cache.tmp')=='777'
    expected={'src':'755','src/app.py':'644','scripts':'755','scripts/deploy.sh':'755','config':'755','config/app.conf':'644','secrets':'755','secrets/token.env':'600','.env.example':'644','logs':'755','logs/keep.log':'644','space dir':'755','space dir/run tool.sh':'755'}
    for rel,want in expected.items(): assert mode(ROOT/rel)==want, f'{rel} mode mismatch'
    data=json.loads(OUT.read_text(encoding='utf-8')); assert data['deleted']==['build/output.bin','cache/a.tmp','logs/old.log']; assert data['summary']['deleted_count']==3; assert data['summary']['permission_fix_count']==12
    # The instruction allows ignored entries as bare paths ("path, reason" strings)
    # or structured objects; accept both shapes when checking membership.
    ignored_paths={e.get('path') if isinstance(e, dict) else e for e in data['ignored']}
    assert 'backup/archive/cache.tmp' in ignored_paths
if __name__=='__main__': main()
