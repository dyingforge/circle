"""Create and maintain durable Circle users and revocable tokens."""
import argparse, datetime as dt, hashlib, json, secrets
from pathlib import Path
def now():return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
def token_for(user,directory,index):
    raw=secrets.token_urlsafe(32);token_id=secrets.token_hex(8);path=directory/(f"user-{index}.token" if index else f"{user['identity']}-{token_id}.token");path.write_text(raw,encoding="utf-8");path.chmod(0o600);user.setdefault("tokens",[]).append({"id":token_id,"token_hash":hashlib.sha256(raw.encode()).hexdigest(),"created_at":now(),"revoked_at":None});return path
def save(path,data):path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");path.chmod(0o600)
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--directory",type=Path,required=True);p.add_argument("--rotate");p.add_argument("--revoke");p.add_argument("--disable");p.add_argument("--enable")
    for role in ("worker","reviewer","admin"):p.add_argument("--"+role,action="append",default=[])
    a=p.parse_args();server=a.directory/"server.json"
    if server.exists():data=json.loads(server.read_text(encoding="utf-8"))
    else:
        people={}
        for role in ("worker","reviewer","admin"):
            for name in getattr(a,role):people.setdefault(name,set()).add(role)
        if not people or any(not n.strip() for n in people):p.error("provide users for a new credential store")
        a.directory.mkdir(mode=0o700,parents=True,exist_ok=False);data={"version":1,"users":[]}
        for name,roles in people.items():
            user={"identity":name,"roles":sorted(roles),"enabled":True,"tokens":[]};token_for(user,a.directory,len(data["users"])+1);data["users"].append(user)
    users={u["identity"]:u for u in data["users"]}
    if a.rotate:
        if a.rotate not in users:p.error("unknown user")
        path=token_for(users[a.rotate],a.directory,0);print(f"New token written to {path}")
    if a.revoke:
        found=False
        for u in data["users"]:
            for t in u["tokens"]:
                if t["id"]==a.revoke and t.get("revoked_at") is None:t["revoked_at"]=now();found=True
        if not found:p.error("unknown or already revoked token id")
    for identity,enabled in ((a.disable,False),(a.enable,True)):
        if identity:
            if identity not in users:p.error("unknown user")
            users[identity]["enabled"]=enabled
    save(server,data);save(a.directory/"identities.json",{"users":[{"identity":u["identity"],"roles":u["roles"],"enabled":u["enabled"],"tokens":[{"id":t["id"],"revoked_at":t.get("revoked_at")} for t in u["tokens"]]} for u in data["users"]]});print(f"Credentials updated in {a.directory}; plaintext tokens exist only in individual .token files.")
if __name__=="__main__":main()
