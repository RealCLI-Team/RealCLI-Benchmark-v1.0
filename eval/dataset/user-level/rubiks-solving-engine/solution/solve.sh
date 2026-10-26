#!/usr/bin/env bash
set -euo pipefail
cd /app

cat > /app/solution.py <<'PY'
import argparse, json, os, glob
from collections import deque, Counter

FACES = ["U","D","L","R","F","B"]
COLORS = set("WYROGB")

def rot_cw(face):
    return [[face[2-c][r] for c in range(3)] for r in range(3)]

def clone(s):
    return {f:[row[:] for row in s[f]] for f in FACES}

def move_U(s):
    s["U"]=rot_cw(s["U"])
    tmp=s["F"][0][:]; s["F"][0]=s["R"][0][:]; s["R"][0]=s["B"][0][:]
    s["B"][0]=s["L"][0][:]; s["L"][0]=tmp
def move_D(s):
    s["D"]=rot_cw(s["D"])
    tmp=s["F"][2][:]; s["F"][2]=s["L"][2][:]; s["L"][2]=s["B"][2][:]
    s["B"][2]=s["R"][2][:]; s["R"][2]=tmp
def move_F(s):
    s["F"]=rot_cw(s["F"])
    tmp=s["U"][2][:]
    s["U"][2]=[s["L"][2][2],s["L"][1][2],s["L"][0][2]]
    for i in range(3): s["L"][i][2]=s["D"][0][i]
    s["D"][0]=[s["R"][2][0],s["R"][1][0],s["R"][0][0]]
    for i in range(3): s["R"][i][0]=tmp[i]
def move_B(s):
    s["B"]=rot_cw(s["B"])
    tmp=s["U"][0][:]
    s["U"][0]=[s["R"][0][2],s["R"][1][2],s["R"][2][2]]
    for i in range(3): s["R"][i][2]=s["D"][2][2-i]
    s["D"][2]=[s["L"][0][0],s["L"][1][0],s["L"][2][0]]
    for i in range(3): s["L"][i][0]=tmp[2-i]
def move_L(s):
    s["L"]=rot_cw(s["L"])
    tmp=[s["U"][i][0] for i in range(3)]
    for i in range(3): s["U"][i][0]=s["B"][2-i][2]
    for i in range(3): s["B"][2-i][2]=s["D"][i][0]
    for i in range(3): s["D"][i][0]=s["F"][i][0]
    for i in range(3): s["F"][i][0]=tmp[i]
def move_R(s):
    s["R"]=rot_cw(s["R"])
    tmp=[s["U"][i][2] for i in range(3)]
    for i in range(3): s["U"][i][2]=s["F"][i][2]
    for i in range(3): s["F"][i][2]=s["D"][i][2]
    for i in range(3): s["D"][i][2]=s["B"][2-i][0]
    for i in range(3): s["B"][2-i][0]=tmp[i]

BASE={"U":move_U,"D":move_D,"L":move_L,"R":move_R,"F":move_F,"B":move_B}
ALL_TOKENS=[f+m for f in FACES for m in ["","'","2"]]
INV={"":"'","'":"","2":"2"}

def apply_move(s,tok):
    fn=BASE[tok[0]]
    mod=tok[1:]
    if mod=="": fn(s)
    elif mod=="'": fn(s);fn(s);fn(s)
    elif mod=="2": fn(s);fn(s)
    else: raise ValueError("bad token "+tok)

def to_str(s):
    return "".join("".join("".join(r) for r in s[f]) for f in FACES)

def validate(faces):
    if not isinstance(faces,dict): return "faces is not an object"
    for f in FACES:
        if f not in faces: return f"missing face {f}"
        g=faces[f]
        if not isinstance(g,list) or len(g)!=3: return f"face {f} is not a 3x3 grid"
        for row in g:
            if not isinstance(row,list) or len(row)!=3: return f"face {f} is not a 3x3 grid"
            for x in row:
                if x not in COLORS: return f"illegal color {x} on face {f}"
    cnt=Counter()
    for f in FACES:
        for row in faces[f]:
            for x in row: cnt[x]+=1
    for col,n in cnt.items():
        if n!=9: return f"color {col} appears {n} times, expected 9"
    return None

def is_uniform(s):
    return all(all(all(x==s[f][1][1] for x in row) for row in s[f]) for f in FACES)

def goal_str(s):
    g={f:[[s[f][1][1]]*3 for _ in range(3)] for f in FACES}
    return to_str(g)

def str_to_state(ss):
    s={};idx=0
    for f in FACES:
        grid=[]
        for r in range(3):
            grid.append([ss[idx+r*3+c] for c in range(3)])
        idx+=9
        s[f]=grid
    return s

def solve(start):
    goal=goal_str(start)
    start_s=to_str(start)
    if start_s==goal: return []
    fwd={start_s:[]}; bwd={goal:[]}
    fq=deque([start_s]); bq=deque([goal])
    for _ in range(4):
        nq=deque()
        while fq:
            cur=fq.popleft(); cs=str_to_state(cur)
            for t in ALL_TOKENS:
                ns=clone(cs); apply_move(ns,t); nss=to_str(ns)
                if nss not in fwd:
                    fwd[nss]=fwd[cur]+[t]
                    if nss in bwd: return fwd[nss]+bwd[nss]
                    nq.append(nss)
        fq=nq
        nq=deque()
        while bq:
            cur=bq.popleft(); cs=str_to_state(cur)
            for t in ALL_TOKENS:
                ns=clone(cs); apply_move(ns,t); nss=to_str(ns)
                if nss not in bwd:
                    bwd[nss]=[t[0]+INV[t[1:]]]+bwd[cur]
                    if nss in fwd: return fwd[nss]+bwd[nss]
                    nq.append(nss)
        bq=nq
    return None

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--input",required=True)
    ap.add_argument("--output",required=True)
    a=ap.parse_args()
    files=sorted(glob.glob(os.path.join(a.input,"puzzles","*.json")))
    records=[]
    for path in files:
        with open(path) as fh: obj=json.load(fh)
        pid=obj.get("id")
        faces=obj.get("faces")
        err=validate(faces)
        if err is not None:
            records.append({"id":pid,"status":"invalid","moves":[],"move_count":0,"reason":err})
            continue
        state={f:[row[:] for row in faces[f]] for f in FACES}
        if is_uniform(state):
            records.append({"id":pid,"status":"already_solved","moves":[],"move_count":0})
            continue
        moves=solve(clone(state))
        if moves is None:
            records.append({"id":pid,"status":"invalid","moves":[],"move_count":0,"reason":"no solution within bound"})
        else:
            records.append({"id":pid,"status":"solved","moves":moves,"move_count":len(moves)})
    records.sort(key=lambda r:r["id"])
    with open(a.output,"w") as fh:
        json.dump({"puzzles":records},fh,indent=2,sort_keys=True)
        fh.write("\n")

if __name__=="__main__":
    main()
PY

python /app/solution.py --input /app/inputs --output /app/result.json
echo "solve.sh finished"
