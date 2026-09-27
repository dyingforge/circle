"""Circle central coordination service. It never executes submitted commands."""
from __future__ import annotations
import argparse, contextlib, datetime as dt, hashlib, hmac, json, re, secrets, sqlite3, ssl, sys, time, urllib.parse
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from errors import CircleError
from model import DOCUMENTS, load_requirements, load_store, write_publication_metadata
import workbranch
from service_workbench import WORKBENCH_HTML

SERVICE_TO_LOCAL={"ready":"ready","running":"in_progress","review":"review","approved":"review","done":"done","cancelled":"cancelled"}
EVIDENCE_KINDS={"test-log","ci-run","artifact","screenshot","review-note"}; ROLES={"worker","reviewer","admin"}
class Conflict(CircleError): pass
class Forbidden(CircleError): pass
def digest(v): return hashlib.sha256(json.dumps(v,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
def iso(ts=None): return dt.datetime.fromtimestamp(ts or time.time(),dt.timezone.utc).replace(microsecond=0).isoformat()

def normalize_users(value):
    if isinstance(value,list):
        value={"users":[{"identity":x.get("identity"),"roles":[x.get("role")],"enabled":True,
                         "tokens":[{"id":"legacy","token_hash":x.get("token_hash"),"revoked_at":None}]} for x in value]}
    if not isinstance(value,dict) or not isinstance(value.get("users"),list) or not value["users"]: raise CircleError("credentials require users")
    out=[]; names=set(); hashes=set()
    for u in value["users"]:
        name=u.get("identity"); roles=u.get("roles"); tokens=u.get("tokens")
        if not isinstance(name,str) or not name.strip() or name in names: raise CircleError("invalid or duplicate identity")
        if not isinstance(roles,list) or not roles or any(r not in ROLES for r in roles): raise CircleError("invalid roles")
        if type(u.get("enabled",True)) is not bool or not isinstance(tokens,list) or not tokens: raise CircleError("invalid user status or tokens")
        clean=[]
        for t in tokens:
            th=t.get("token_hash") if isinstance(t,dict) else None
            if not isinstance(th,str) or not re.fullmatch(r"[0-9a-f]{64}",th) or th in hashes: raise CircleError("invalid or duplicate token hash")
            if t.get("revoked_at") is not None and not isinstance(t["revoked_at"],str): raise CircleError("invalid revoked_at")
            hashes.add(th); clean.append(dict(t,token_hash=th,revoked_at=t.get("revoked_at")))
        names.add(name); out.append({"identity":name,"roles":sorted(set(roles)),"enabled":u.get("enabled",True),"tokens":clean})
    return out

class Coordinator:
    def __init__(self,database):
        self.database=Path(database); self.database.parent.mkdir(parents=True,exist_ok=True); self.users=[]
        with self.connect() as db:
            db.executescript("""CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY,at REAL NOT NULL,actor TEXT NOT NULL,issue TEXT NOT NULL,action TEXT NOT NULL,version INTEGER NOT NULL,detail TEXT NOT NULL DEFAULT '{}');""")
            if "detail" not in {r[1] for r in db.execute("PRAGMA table_info(events)")}: db.execute("ALTER TABLE events ADD COLUMN detail TEXT NOT NULL DEFAULT '{}'")
    def set_users(self,v): self.users=normalize_users(v)
    @contextlib.contextmanager
    def connect(self):
        db=sqlite3.connect(self.database,timeout=20)
        try:
            with db: yield db
        finally: db.close()
    @contextlib.contextmanager
    def transaction(self):
        with self.connect() as db: db.execute("BEGIN IMMEDIATE"); yield db
    def _tasks(self,db): return {k:json.loads(v) for k,v in db.execute("SELECT id,body FROM tasks")}
    def _save(self,db,t,actor,action,detail=None):
        t["version"]+=1; record={"at":iso(),"actor":actor,"action":action,"version":t["version"],"detail":detail or {}}
        t.setdefault("history",[]).append(record); db.execute("INSERT OR REPLACE INTO tasks VALUES (?,?)",(t["id"],json.dumps(t)))
        db.execute("INSERT INTO events(at,actor,issue,action,version,detail) VALUES(?,?,?,?,?,?)",(time.time(),actor,t["id"],action,t["version"],json.dumps(detail or {})))
    def publish(self,root):
        from model import store_lock
        root=Path(root).resolve()
        with store_lock(root) as store:
            project,issues=load_store(store); docs={f:(store/f).read_text(encoding="utf-8") for _,f in DOCUMENTS}; reqs=load_requirements(store)
            base=workbranch.git(root,"rev-parse","HEAD") if workbranch.git_ok(root,"rev-parse","HEAD") else None
            result=self.publish_snapshot(project,issues,docs,reqs,base,str(root)); tasks={t["id"]:t for t in self.tasks()}
            write_publication_metadata(store,{"execution_mode":"service","published_at":iso(),"base_commit":base,"issues":{k:{"authoring_revision":t["source"]["revision"],"source_hash":t["source_hash"],"service_state":t["state"],"service_version":t["version"]} for k,t in tasks.items()}}); return result
    def publish_snapshot(self,project,issues,docs,requirements=None,base_commit=None,project_root=None):
        with self.transaction() as db:
            old=self._tasks(db); identity={"name":project["name"],"created_at":project["created_at"]}; meta=db.execute("SELECT body FROM metadata WHERE key='project'").fetchone()
            if meta and json.loads(meta[0])!=identity: raise Conflict("database belongs to a different project")
            if old.keys()-issues.keys(): raise Conflict("publication is append-only")
            for k,i in issues.items():
                if k in old and old[k]["source_hash"]!=digest(i):
                    done=self.completed_issue(old[k])
                    if done is None or digest(done)!=digest(i): raise Conflict(f"published issue changed: {k}; create a follow-up issue")
                if k not in old and i["state"] not in {"ready","done","cancelled"}: raise Conflict(f"only ready/done/cancelled issues can be published: {k}")
            for k,i in issues.items():
                if k in old: continue
                t={"id":k,"source":i,"source_hash":digest(i),"docs":docs,"requirements":{r:(requirements or {}).get(r) for r in i.get("requirement_ids",[])},"state":i["state"],"local_state":SERVICE_TO_LOCAL[i["state"]],"base_commit":base_commit,"dependency_commits":{},"project_root":project_root,"owner":None,"attempt":0,"lease_until":None,"version":0,"reviewer":None,"submission":None,"review":None,"acceptance":None,"history":[]}; self._save(db,t,"publisher","publish")
            db.execute("INSERT OR REPLACE INTO metadata VALUES('project',?)",(json.dumps(identity),)); return {"published":len(issues.keys()-old.keys()),"total":len(self._tasks(db)),"base_commit":base_commit}
    @staticmethod
    def completed_issue(t):
        if t["state"]!="done" or not t["acceptance"]: return None
        i=dict(t["source"]); a=t["acceptance"]; i.update(state="done",assignee=t["owner"],revision=i["revision"]+1,updated_at=a["at"],acceptance=[dict(x,done=True) for x in i["acceptance"]]); i["comments"]=(i.get("comments","")+"\n\n"+json.dumps({"service_commit":a["commit"],"worker":t["owner"],"review":t["review"],"final_acceptance":a,"evidence":t["submission"]["evidence"]},ensure_ascii=False)).strip(); return i
    def sync(self,root):
        from model import store_lock,validate_graph,write_issue
        root=Path(root).resolve(); workbranch.require_repo_root(root)
        with store_lock(root) as store,self.transaction() as db:
            project,issues=load_store(store); meta=db.execute("SELECT body FROM metadata WHERE key='project'").fetchone(); identity={"name":project["name"],"created_at":project["created_at"]}
            if not meta or json.loads(meta[0])!=identity: raise Conflict("database belongs to a different project")
            tasks=self._tasks(db); pending={}
            for k,t in tasks.items():
                done=self.completed_issue(t)
                if done is None or (k in issues and digest(issues[k])==digest(done)): continue
                if k not in issues or digest(issues[k])!=t["source_hash"]: raise Conflict(f"source changed since publication: {k}")
                if not workbranch.git_ok(root,"merge-base","--is-ancestor",t["acceptance"]["commit"],"HEAD"): raise Conflict(f"accepted commit is not merged into current HEAD: {k}")
                pending[k]=done
            validate_graph(dict(issues,**pending)); count=len(pending)
            while pending:
                writable=[k for k,i in pending.items() if all(issues[d]["state"]=="done" for d in i["blocked_by"])]
                if not writable: raise Conflict("cannot sync before accepted dependencies")
                for k in writable: i=pending.pop(k); write_issue(root,i); issues[k]=i; self._save(db,tasks[k],"publisher","sync")
            return {"synced":count,"instruction":"validate, render, then commit updated Circle facts"}
    def tasks(self,*_):
        with self.connect() as db:return list(self._tasks(db).values())
    def events(self,after=0,actor=None,roles=None):
        with self.connect() as db: rows=list(db.execute("SELECT seq,at,actor,issue,action,version,detail FROM events WHERE seq>? ORDER BY seq LIMIT 500",(after,)))
        out=[dict(zip(("seq","at","actor","issue","action","version","detail"),r)) for r in rows]
        for e in out:e["detail"]=json.loads(e["detail"]);e["notification"]={"publish":"task_available","submit":"awaiting_review","review_approve":"awaiting_final_acceptance","review_reject":"review_rejected","final_reject":"final_rejected","accept":"completed","cancel":"cancelled","retry":"requeued","recover":"requeued","release":"requeued"}.get(e["action"],e["action"])
        if roles and "admin" not in roles:
            relevant={t["id"] for t in self.tasks() if actor in {t.get("owner"),t.get("reviewer"),t["source"].get("assignee")} or ("worker" in roles and t["state"]=="ready") or ("reviewer" in roles and t["state"]=="review")};out=[e for e in out if e["issue"] in relevant]
        return out
    def notifications(self,after=0,actor=None,roles=None):
        out=self.events(after,actor,roles);latest=max([e["seq"] for e in out],default=after);now=time.time()
        for t in self.tasks():
            if t["state"]=="running" and t.get("lease_until") and now<t["lease_until"]<=now+60:
                if roles and "admin" not in roles and actor not in {t.get("owner"),t["source"].get("assignee")}:
                    continue
                out.append({"seq":latest,"at":now,"actor":"service","issue":t["id"],"action":"lease_expiring","version":t["version"],"detail":{"lease_until":t["lease_until"]},"notification":"lease_expiring","projection":True})
        return out
    def verdicts(self,t,value,label,all_pass=False):
        n=len(t["source"]["acceptance"])
        if not isinstance(value,list) or len(value)!=n: raise CircleError(f"{label} requires one verdict per criterion")
        for idx,v in enumerate(value,1):
            if not isinstance(v,dict) or v.get("criterion")!=idx or v.get("status") not in {"pass","fail"} or not isinstance(v.get("note"),str) or not v["note"].strip(): raise CircleError(f"invalid {label} verdict {idx}")
            if all_pass and v["status"]!="pass": raise Conflict(f"criterion {idx} has not passed")
        return value
    def evidence(self,t,value,commit):
        if not isinstance(value,list) or not value: raise CircleError("submission requires structured evidence")
        covered=set(); allowed={"criterion","kind","locator","sha256","command","exit_code","observed_at","commit"}
        for idx,e in enumerate(value,1):
            if not isinstance(e,dict) or set(e)-allowed: raise CircleError(f"invalid evidence object {idx}")
            c=e.get("criterion")
            if type(c) is not int or not 1<=c<=len(t["source"]["acceptance"]): raise CircleError(f"invalid evidence criterion {idx}")
            if e.get("kind") not in EVIDENCE_KINDS or not isinstance(e.get("locator"),str) or not e["locator"].strip() or not re.fullmatch(r"[0-9a-f]{64}",str(e.get("sha256",""))) or e.get("commit")!=commit or not isinstance(e.get("observed_at"),str): raise CircleError(f"invalid evidence fields {idx}")
            try:dt.datetime.fromisoformat(e["observed_at"].replace("Z","+00:00"))
            except ValueError:raise CircleError(f"invalid evidence observed_at {idx}")
            if "command" in e and (not isinstance(e["command"],str) or not e["command"].strip()):raise CircleError(f"invalid evidence command {idx}")
            if "exit_code" in e and type(e["exit_code"]) is not int: raise CircleError(f"invalid evidence exit_code {idx}")
            if e["kind"] in {"test-log","ci-run"} and e.get("exit_code")!=0: raise CircleError(f"failed test evidence {idx}")
            if t.get("project_root"):
                root=Path(t["project_root"]).resolve(); p=(root/e["locator"]).resolve()
                try:p.relative_to(root)
                except ValueError:raise CircleError(f"evidence escapes project root {idx}")
                if p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest()!=e["sha256"]:raise CircleError(f"evidence hash mismatch {idx}")
            covered.add(c)
        if covered!=set(range(1,len(t["source"]["acceptance"])+1)):raise CircleError("evidence must cover every acceptance criterion")
        return value
    def reviewer_exists(self,name):return any(u["enabled"] and u["identity"]==name and "reviewer" in u["roles"] for u in self.users)
    def act(self,k,a,actor,role,data):return self.act_roles(k,a,actor,{role},data)
    def act_roles(self,k,a,actor,roles,data):
        if not isinstance(data,dict):raise CircleError("request body must be an object")
        perms={"claim":"worker","heartbeat":"worker","submit":"worker","release":"worker","review":"reviewer","accept":"admin","reject":"admin","assign":"admin","cancel":"admin","retry":"admin","recover":"admin"}; need=perms.get(a)
        if not need:raise CircleError("unknown action")
        if need not in roles:raise Forbidden(f"{a} requires role {need}")
        with self.transaction() as db:
            tasks=self._tasks(db)
            if k not in tasks:raise CircleError("unknown issue")
            t=tasks[k]; now=time.time(); event=a; detail={}
            if a=="claim":
                expired=t["state"]=="running" and t["lease_until"]<=now
                if t["state"]!="ready" and not expired:raise Conflict("issue is not available")
                if any(tasks.get(d,{}).get("state")!="done" for d in t["source"]["blocked_by"]):raise Conflict("unfinished blockers")
                if t["source"].get("assignee") and t["source"]["assignee"]!=actor:raise Forbidden("issue is assigned to another worker")
                t["dependency_commits"]={d:tasks[d]["acceptance"]["commit"] for d in t["source"]["blocked_by"]};t.update(state="running",local_state="in_progress",owner=actor,attempt=t["attempt"]+1,lease_until=now+self.ttl(data),submission=None,review=None,acceptance=None);detail={"attempt":t["attempt"],"base_commit":t["base_commit"],"dependency_commits":t["dependency_commits"]}
            elif a in {"heartbeat","submit","release"}:
                if t["owner"]!=actor:raise Forbidden("only the lease owner may update execution")
                if t["state"]!="running" or t["lease_until"]<=now or type(data.get("attempt")) is not int or data["attempt"]!=t["attempt"]:raise Conflict("expired or stale execution lease")
                if a=="heartbeat":t["lease_until"]=now+self.ttl(data)
                elif a=="release":
                    if not isinstance(data.get("reason"),str) or not data["reason"].strip():raise CircleError("release requires a reason")
                    detail={"reason":data["reason"],"old_attempt":t["attempt"],"old_version":t["version"]};t.update(state="ready",local_state="ready",owner=None,lease_until=None,submission=None,review=None,acceptance=None)
                else:
                    commit=data.get("commit","")
                    if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}",str(commit)):raise CircleError("submission requires a full Git commit hash")
                    evidence=self.evidence(t,data.get("evidence"),commit);t.update(state="review",local_state="review",lease_until=None,submission={"commit":commit,"evidence":evidence,"worker":actor,"attempt":t["attempt"],"at":iso(now)});detail={"commit":commit,"evidence":evidence,"attempt":t["attempt"]}
            else:
                if type(data.get("expected_version")) is not int or data["expected_version"]!=t["version"]:raise Conflict("stale task version")
                if a=="assign":
                    r=data.get("reviewer")
                    if t["state"] in {"done","cancelled","approved"}:raise Conflict("cannot assign reviewer in this state")
                    if r==t["owner"] or not self.reviewer_exists(r):raise CircleError("assign an enabled independent reviewer")
                    t["reviewer"]=r;detail={"reviewer":r}
                elif a=="review":
                    if actor!=t["reviewer"] or actor==t["owner"]:raise Forbidden("only designated independent reviewer may review")
                    if t["state"]!="review":raise Conflict("issue is not awaiting review")
                    d=data.get("decision");note=data.get("note")
                    if d not in {"approve","reject"} or not isinstance(note,str) or not note.strip():raise CircleError("review requires approve/reject and note")
                    verdicts=self.verdicts(t,data.get("verdicts"),"review",d=="approve");t["review"]={"reviewer":actor,"decision":d,"note":note,"verdicts":verdicts,"commit":t["submission"]["commit"],"at":iso(now)};t["state"]="approved" if d=="approve" else "ready";t["local_state"]="review" if d=="approve" else "ready";event="review_"+d;detail={"decision":d,"note":note,"verdicts":verdicts,"commit":t["submission"]["commit"]}
                elif a=="accept":
                    if t["state"]!="approved" or actor in {t["owner"],t["reviewer"]}:raise Conflict("final acceptance requires an independent admin after review")
                    if not isinstance(data.get("note"),str) or not data["note"].strip():raise CircleError("final acceptance requires note")
                    verdicts=self.verdicts(t,data.get("verdicts"),"acceptance",True);t.update(state="done",local_state="done",acceptance={"actor":actor,"note":data["note"],"verdicts":verdicts,"commit":t["submission"]["commit"],"at":iso(now)});detail={"note":data["note"],"verdicts":verdicts,"commit":t["submission"]["commit"]}
                elif a=="reject":
                    if t["state"]!="approved":raise Conflict("final rejection requires approved work")
                    reason=data.get("reason")
                    if not isinstance(reason,str) or not reason.strip():raise CircleError("final rejection requires reason")
                    detail={"reason":reason,"old_attempt":t["attempt"],"old_version":t["version"],"commit":t["submission"]["commit"]};t.update(state="ready",local_state="ready",owner=None,lease_until=None,submission=None,review=None,acceptance=None);event="final_reject"
                elif a=="cancel":
                    if t["state"]=="done":raise Conflict("cannot cancel completed work")
                    if not isinstance(data.get("reason"),str) or not data["reason"].strip():raise CircleError("cancel requires reason")
                    detail={"reason":data["reason"],"old_attempt":t["attempt"],"old_version":t["version"]};t.update(state="cancelled",local_state="cancelled",lease_until=None)
                else:
                    if a=="retry" and t["state"] not in {"cancelled","review","approved"}:raise Conflict("retry requires cancelled or review-stage work")
                    if t["state"]=="done":raise Conflict("completed work cannot be recovered")
                    if not isinstance(data.get("reason"),str) or not data["reason"].strip():raise CircleError(f"{a} requires reason")
                    detail={"reason":data["reason"],"old_attempt":t["attempt"],"old_version":t["version"]};t.update(state="ready",local_state="ready",owner=None,lease_until=None,submission=None,review=None,acceptance=None)
            self._save(db,t,actor,event,detail);return t
    @staticmethod
    def ttl(data):
        ttl=data.get("ttl",300)
        if type(ttl) is not int or not 30<=ttl<=3600:raise CircleError("lease ttl must be integer 30..3600")
        return ttl

def handler_for(coordinator,credentials):
    users=normalize_users(credentials);coordinator.set_users({"users":users});sessions={}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def reply(self,code,body,ctype="application/json; charset=utf-8",headers=None):
            raw=body.encode() if isinstance(body,str) else json.dumps(body,ensure_ascii=False).encode();self.send_response(code);self.send_header("Content-Type",ctype);self.send_header("Content-Length",str(len(raw)));self.send_header("X-Content-Type-Options","nosniff");self.send_header("Content-Security-Policy","default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'")
            for k,v in (headers or {}).items():self.send_header(k,v)
            self.end_headers();self.wfile.write(raw)
        def body(self):
            n=int(self.headers.get("Content-Length","0"))
            if not 0<=n<=1048576:raise CircleError("body too large")
            return json.loads(self.rfile.read(n) or b"{}")
        def token_user(self,raw):
            hashed=hashlib.sha256(raw.encode()).hexdigest() if raw else ""
            return next((u for u in users if u["enabled"] and any(t["revoked_at"] is None and hmac.compare_digest(t["token_hash"],hashed) for t in u["tokens"])),None)
        def auth(self):
            a=self.headers.get("Authorization","");u=self.token_user(a.removeprefix("Bearer ")) if a.startswith("Bearer ") else None
            if u:return u
            jar=cookies.SimpleCookie(self.headers.get("Cookie",""));m=jar.get("circle_session");s=sessions.get(m.value) if m else None
            return next((u for u in users if s and s[1]>time.time() and u["identity"]==s[0] and u["enabled"]),None)
        def dispatch(self):
            p=urllib.parse.urlsplit(self.path)
            if self.command=="GET" and p.path=="/health":return self.reply(200,{"status":"ok"})
            if self.command=="GET" and p.path in {"/","/workbench"}:return self.reply(200,WORKBENCH_HTML,"text/html; charset=utf-8")
            if self.command=="POST" and p.path=="/v1/session":
                u=self.token_user(self.body().get("token",""))
                if not u:return self.reply(401,{"error":"invalid credentials"})
                sid=secrets.token_urlsafe(32);sessions[sid]=(u["identity"],time.time()+28800);return self.reply(200,{"identity":u["identity"]},headers={"Set-Cookie":f"circle_session={sid}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800"})
            u=self.auth()
            if not u:return self.reply(401,{"error":"invalid credentials"})
            if self.command=="DELETE" and p.path=="/v1/session":return self.reply(200,{"ok":True},headers={"Set-Cookie":"circle_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"})
            if self.command=="GET" and p.path=="/v1/me":return self.reply(200,{"identity":u["identity"],"roles":u["roles"]})
            if self.command=="GET" and p.path=="/v1/tasks":return self.reply(200,coordinator.tasks())
            if self.command=="GET" and p.path in {"/v1/events","/v1/notifications"}:
                after=int(urllib.parse.parse_qs(p.query).get("after",[0])[0]);method=coordinator.notifications if p.path.endswith("notifications") else coordinator.events;return self.reply(200,method(after,u["identity"],u["roles"]))
            m=re.fullmatch(r"/v1/tasks/(CIR-[A-Z0-9]{10})(?:/(claim|heartbeat|submit|release|assign|review|accept|reject|cancel|retry|recover))?",p.path)
            if m and self.command=="GET" and not m[2]:
                t=next((x for x in coordinator.tasks() if x["id"]==m[1]),None);return self.reply(200 if t else 404,t or {"error":"unknown issue"})
            if m and self.command=="POST" and m[2]:return self.reply(200,coordinator.act_roles(m[1],m[2],u["identity"],set(u["roles"]),self.body()))
            return self.reply(404,{"error":"unknown endpoint"})
        def handle_request(self):
            self.connection.settimeout(15)
            try:self.dispatch()
            except Forbidden as e:self.reply(403,{"error":str(e)})
            except Conflict as e:self.reply(409,{"error":str(e)})
            except (CircleError,ValueError,UnicodeError,json.JSONDecodeError) as e:self.reply(400,{"error":str(e)})
            except (TimeoutError,BrokenPipeError,ConnectionResetError):pass
        do_GET=do_POST=do_DELETE=handle_request
    return Handler

def main():
    p=argparse.ArgumentParser();p.add_argument("--database",type=Path,required=True);s=p.add_subparsers(dest="command",required=True);x=s.add_parser("publish");x.add_argument("--project-root",type=Path,required=True);x=s.add_parser("sync");x.add_argument("--project-root",type=Path,required=True);s.add_parser("status");x=s.add_parser("serve");x.add_argument("--credentials",type=Path,required=True);x.add_argument("--host",default="127.0.0.1");x.add_argument("--port",type=int,default=8769);x.add_argument("--certfile");x.add_argument("--keyfile");a=p.parse_args();c=Coordinator(a.database)
    if a.command=="publish":print(json.dumps(c.publish(a.project_root)))
    elif a.command=="sync":print(json.dumps(c.sync(a.project_root)))
    elif a.command=="status":print(json.dumps(c.tasks(),ensure_ascii=False,indent=2))
    else:
        if a.host not in {"127.0.0.1","localhost","::1"} and not(a.certfile and a.keyfile):raise CircleError("remote binding requires --certfile and --keyfile for HTTPS")
        server=ThreadingHTTPServer((a.host,a.port),handler_for(c,json.loads(a.credentials.read_text(encoding="utf-8"))))
        if a.certfile and a.keyfile:ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(a.certfile,a.keyfile);server.socket=ctx.wrap_socket(server.socket,server_side=True)
        print(f"Circle Service listening on {a.host}:{server.server_port}",flush=True)
        try:server.serve_forever()
        finally:server.server_close()
if __name__=="__main__":
    for stream in (sys.stdin,sys.stdout,sys.stderr):stream.reconfigure(encoding="utf-8")
    try:main()
    except CircleError as e:print(f"Circle error: {e}",file=sys.stderr);raise SystemExit(2)
