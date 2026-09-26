"""Verify a claimed task's base and dependency commits against a local clone."""
import argparse,json,sys
from pathlib import Path
from errors import CircleError
import workbranch
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--project-root",type=Path,required=True);p.add_argument("--task",type=Path,required=True);a=p.parse_args();task=json.loads(a.task.read_text(encoding="utf-8"));print(json.dumps(workbranch.preflight(a.project_root.resolve(),task.get("base_commit"),task.get("dependency_commits",{})),indent=2))
if __name__=="__main__":
    try:main()
    except CircleError as e:print(f"Circle error: {e}",file=sys.stderr);raise SystemExit(2)
