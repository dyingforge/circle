"""One isolated Git worktree per Issue (and per Service attempt)."""
from __future__ import annotations
import hashlib, json, subprocess
from pathlib import Path
from typing import Any
from errors import CircleError
from graph import is_unblocked, unfinished_blockers
from model import UNSTARTABLE_STATES
BRANCH_PREFIX="circle/"
def git(root:Path,*args:str)->str:
    r=subprocess.run(["git",*args],cwd=str(root),text=True,encoding="utf-8",capture_output=True)
    if r.returncode: raise CircleError(f"git {' '.join(args)} failed: {r.stderr.strip() or r.stdout.strip() or 'unknown git failure'}")
    return r.stdout.strip()
def git_ok(root:Path,*args:str)->bool:return subprocess.run(["git",*args],cwd=str(root),capture_output=True).returncode==0
def require_repo_root(root:Path)->Path:
    if not git_ok(root,"rev-parse","--is-inside-work-tree"):raise CircleError(f"project root is not a git work tree: {root}")
    top=Path(git(root,"rev-parse","--show-toplevel")).resolve()
    if top!=root.resolve():raise CircleError(f"git work tree root is {top}, not the project root {root}")
    return top
def current_branch(root):
    b=git(root,"rev-parse","--abbrev-ref","HEAD")
    if b=="HEAD":raise CircleError("repository is in detached HEAD state")
    return b
def require_clean_tree(root):
    if git(root,"status","--porcelain"):raise CircleError("working tree has uncommitted changes; commit or stash them first")
def branch_exists(root,b):return git_ok(root,"rev-parse","--verify","--quiet",f"refs/heads/{b}")
def work_branch(issue,attempt=None):return BRANCH_PREFIX+issue["id"]+(f"-a{attempt}" if attempt else "")
def metadata_dir(root):
    common=Path(git(root,"rev-parse","--git-common-dir"));common=common if common.is_absolute() else (root/common).resolve();p=common/"circle-worktrees";p.mkdir(exist_ok=True);return p
def metadata_path(root,issue_id,attempt=None):return metadata_dir(root)/(issue_id+(f"-a{attempt}" if attempt else "")+".json")
def safe_worktree_path(root,issue_id,attempt=None):
    tag=hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:10];name=root.name+"-"+tag;base=(root.parent/".circle-worktrees"/name).resolve();leaf=issue_id+(f"-a{attempt}" if attempt else "");target=(base/leaf).resolve()
    try:target.relative_to(base)
    except ValueError:raise CircleError("unsafe worktree path")
    return target
def preflight(root,base_commit,dependency_commits):
    require_repo_root(root);head="HEAD"
    for label,commit in [("base",base_commit),*[(f"dependency {k}",v) for k,v in (dependency_commits or {}).items()]]:
        if not commit:continue
        if not git_ok(root,"cat-file","-e",f"{commit}^{{commit}}"):raise CircleError(f"missing {label} commit: {commit}")
        if not git_ok(root,"merge-base","--is-ancestor",commit,head):raise CircleError(f"{label} commit is not an ancestor of local HEAD: {commit}")
    return {"head":git(root,"rev-parse",head),"base_commit":base_commit,"dependency_commits":dependency_commits or {}}
def start(root:Path,issue:dict[str,Any],issues:dict[str,dict[str,Any]],*,attempt=None,base_commit=None,dependency_commits=None)->str:
    if issue["state"] in UNSTARTABLE_STATES:raise CircleError(f"cannot start work on a {issue['state']} issue")
    if not is_unblocked(issue,issues):raise CircleError("unfinished blockers prevent starting work: "+", ".join(unfinished_blockers(issue,issues)))
    root=require_repo_root(root.resolve());require_clean_tree(root);base_branch=current_branch(root);base_commit=base_commit or git(root,"rev-parse",base_branch);preflight(root,base_commit,dependency_commits or {})
    branch=work_branch(issue,attempt);target=safe_worktree_path(root,issue["id"],attempt);meta=metadata_path(root,issue["id"],attempt)
    if meta.exists():
        saved=json.loads(meta.read_text(encoding="utf-8"))
        if Path(saved["path"]).is_dir():return f"Already prepared {branch} at {saved['path']}."
    if branch_exists(root,branch):raise CircleError(f"issue branch already exists: {branch}; finish or clean it first")
    target.parent.mkdir(parents=True,exist_ok=True);git(root,"worktree","add","-b",branch,str(target),base_commit)
    record={"issue":issue["id"],"attempt":attempt,"path":str(target),"branch":branch,"base_branch":base_branch,"base_commit":base_commit,"dependency_commits":dependency_commits or {}}
    meta.write_text(json.dumps(record,indent=2)+"\n",encoding="utf-8");return f"Created {branch} worktree at {target} from {base_commit}."
def _find_meta(root,issue_id,attempt=None):
    p=metadata_path(root,issue_id,attempt)
    if not p.exists() and attempt is None:
        found=list(metadata_dir(root).glob(issue_id+"-a*.json"))
        if len(found)==1:p=found[0]
    if not p.exists():raise CircleError(f"issue worktree does not exist: {issue_id}")
    return p,json.loads(p.read_text(encoding="utf-8"))
def finish(root:Path,issue:dict[str,Any],into:str|None,attempt=None)->str:
    root=require_repo_root(root.resolve());meta_path,record=_find_meta(root,issue["id"],attempt);worktree=Path(record["path"])
    if not worktree.is_dir():raise CircleError(f"worktree path is missing: {worktree}")
    require_clean_tree(worktree);branch=record["branch"];base=into or record["base_branch"]
    if not branch_exists(root,base):raise CircleError(f"base branch does not exist: {base}")
    require_clean_tree(root)
    if current_branch(root)!=base:git(root,"checkout",base)
    try:git(root,"merge","--no-edit",branch)
    except CircleError:
        if git_ok(root,"rev-parse","-q","--verify","MERGE_HEAD"):git(root,"merge","--abort")
        raise
    git(root,"worktree","remove",str(worktree));git(root,"branch","-d",branch);meta_path.unlink();return f"Merged {branch} into {base}, removed {worktree}, and deleted the branch."
