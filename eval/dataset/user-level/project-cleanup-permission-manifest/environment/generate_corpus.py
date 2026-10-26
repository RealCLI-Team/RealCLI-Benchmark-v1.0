#!/usr/bin/env python3
from __future__ import annotations
import os, shutil
from pathlib import Path
ROOT=Path('/app/project')
FILES={'src/app.py':('print("ok")\n',0o777),'scripts/deploy.sh':('#!/bin/sh\necho deploy\n',0o644),'config/app.conf':('port=8080\n',0o600),'secrets/token.env':('TOKEN=x\n',0o644),'.env.example':('TOKEN=sample\n',0o777),'cache/a.tmp':('cache\n',0o644),'build/output.bin':('bin\n',0o755),'logs/keep.log':('keep\n',0o644),'logs/old.log':('old\n',0o644),'backup/archive/cache.tmp':('do not delete\n',0o777),'space dir/run tool.sh':('#!/bin/sh\necho space\n',0o644)}
def main():
    shutil.rmtree(ROOT, ignore_errors=True); Path('/app/cleanup_manifest.json').unlink(missing_ok=True)
    for rel,(content,mode) in FILES.items():
        p=ROOT/rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(content, encoding='utf-8'); os.chmod(p,mode)
    for d in [ROOT, ROOT/'src', ROOT/'scripts', ROOT/'config', ROOT/'secrets', ROOT/'logs', ROOT/'backup', ROOT/'backup/archive', ROOT/'space dir']:
        os.chmod(d,0o700)
    print('Generated project under /app/project')
if __name__=='__main__': main()
