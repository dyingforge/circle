"""Real HTTP clients, SQLite contention, restart, review and acceptance gates."""
import concurrent.futures
import hashlib
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from unittest.mock import patch

from support import CircleTestCase, SCRIPTS, import_payload
from service import Coordinator, Conflict, Forbidden, handler_for
from service_workbench import WORKBENCH_HTML


class CredentialAuthTest(CircleTestCase):
    def test_workbench_uses_cookie_session_and_admin_reason_contract(self):
        self.assertIn("HttpOnly",WORKBENCH_HTML)
        self.assertIn("a.push('accept','reject')",WORKBENCH_HTML)
        self.assertIn("b.reason=f.get('note')",WORKBENCH_HTML)
    def request_status(self, user):
        coordinator=Coordinator(self.root/"auth.sqlite3")
        server=ThreadingHTTPServer(("127.0.0.1",0),handler_for(coordinator,{"users":[user]}))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            request=urllib.request.Request(f"http://127.0.0.1:{server.server_port}/v1/tasks",headers={"Authorization":"Bearer secret"})
            try:response=urllib.request.urlopen(request,timeout=5)
            except urllib.error.HTTPError as exc:response=exc
            with response:return response.status
        finally:server.shutdown();server.server_close();thread.join(5)

    def test_disabled_and_revoked_users_cannot_authenticate(self):
        token={"id":"one","token_hash":hashlib.sha256(b"secret").hexdigest(),"revoked_at":None}
        self.assertEqual(401,self.request_status({"identity":"u","roles":["worker"],"enabled":False,"tokens":[token]}))
        token["revoked_at"]="2026-09-25T00:00:00Z"
        self.assertEqual(401,self.request_status({"identity":"u","roles":["worker"],"enabled":True,"tokens":[token]}))


class ServiceTest(CircleTestCase):
    def setUp(self):
        super().setUp()
        payload = import_payload()
        for issue in payload["issues"]:
            issue.update(state="ready", assignee=None)
        self.ids = self.preview_commit(payload)
        self.coordinator = Coordinator(self.root / "runtime" / "service.sqlite3")
        self.coordinator.publish(self.root)
        self.users = [("alice", "worker"), ("bob", "worker"), ("reviewer", "reviewer"), ("owner", "admin")]
        # Test-only tokens, never usable against a deployed service.
        credentials = [{"identity": name, "role": role,
                        "token_hash": hashlib.sha256(("test-" + name).encode()).hexdigest()}
                       for name, role in self.users]
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(self.coordinator, credentials))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        super().tearDown()

    def http(self, path, actor="alice", data=None, expect=200):
        body = None if data is None else json.dumps(data).encode()
        request = urllib.request.Request(f"http://127.0.0.1:{self.server.server_port}" + path,
                                         data=body, headers={"Authorization": "Bearer test-" + actor})
        try:
            response = urllib.request.urlopen(request, timeout=10)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            content = json.loads(response.read())
            self.assertEqual(expect, response.status, content)
            return content

    def act(self, action, actor="alice", data=None, expect=200, key="model"):
        return self.http(f"/v1/tasks/{self.ids[key]}/{action}", actor, data or {}, expect)

    def submit(self, claim, commit="a" * 40):
        evidence = {"criterion": 1, "kind": "test-log", "locator": "tests/result.txt",
                    "sha256": "b" * 64, "command": "python -m unittest", "exit_code": 0,
                    "observed_at": "2026-09-24T00:00:00+00:00", "commit": commit}
        return self.act("submit", data={"attempt": claim["attempt"], "commit": commit,
                                        "evidence": [evidence]})

    def assign(self, task):
        return self.act("assign", "owner", {"expected_version": task["version"], "reviewer": "reviewer"})

    def test_real_git_http_review_acceptance_and_dependency_release(self):
        # Keep the central DB outside the Git working tree in normal deployment.
        (self.root / ".gitignore").write_text("runtime/\n", encoding="utf-8")
        self.init_git()
        task = self.act("claim")
        self.act("claim", "bob", expect=409, key="dag")
        started=self.circle("issue-branch", "--id", self.ids["model"]).stdout
        import re
        worktree=Path(re.search(r"worktree at (.+) from [0-9a-f]+",started).group(1))
        (worktree / "implementation.txt").write_text("implemented fixture\n", encoding="utf-8")
        subprocess.run(["git","add","-A"],cwd=worktree,check=True)
        subprocess.run(["git","commit","-qm","implement fixture"],cwd=worktree,check=True)
        commit=subprocess.run(["git","rev-parse","HEAD"],cwd=worktree,text=True,capture_output=True,check=True).stdout.strip()
        task = self.submit(task, commit)
        self.act("accept", "owner", {"expected_version": task["version"], "note": "early"}, expect=409)
        task = self.assign(task)
        task = self.act("review", "reviewer", {"expected_version": task["version"],
                                               "decision": "approve", "note": "reviewed fixture and evidence",
                                               "verdicts": [{"criterion":1,"status":"pass","note":"verified"}]})
        task = self.act("accept", "owner", {"expected_version": task["version"], "note": "final acceptance",
                                             "verdicts": [{"criterion":1,"status":"pass","note":"accepted"}]})
        self.assertEqual("done", task["state"])
        with self.assertRaisesRegex(Conflict, "not merged"):
            self.coordinator.sync(self.root)
        self.circle("issue-finish", "--id", self.ids["model"])
        self.assertEqual(1, self.coordinator.sync(self.root)["synced"])
        self.assertEqual(0, self.coordinator.sync(self.root)["synced"])
        self.circle("validate")
        self.assertEqual(0, self.coordinator.publish(self.root)["published"])
        released = self.act("claim", "bob", key="dag")
        self.assertEqual("running", released["state"])
        restarted = Coordinator(self.coordinator.database)
        self.assertEqual("done", next(t for t in restarted.tasks() if t["id"] == task["id"])["state"])
        if os.environ.get("CIRCLE_TEST_EVIDENCE"):
            Path(os.environ["CIRCLE_TEST_EVIDENCE"]).write_text(json.dumps({
                "kind": "local HTTP + real Git integration, not physical multi-machine",
                "commit": commit, "completed": task, "released": released["id"],
                "events": restarted.events()}, ensure_ascii=False, indent=2), encoding="utf-8")

    def test_two_http_workers_have_only_one_claim_winner(self):
        def claim(actor):
            request = urllib.request.Request(
                f"http://127.0.0.1:{self.server.server_port}/v1/tasks/{self.ids['model']}/claim",
                data=b"{}", headers={"Authorization": "Bearer test-" + actor})
            try:
                response = urllib.request.urlopen(request, timeout=10)
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                return response.status, json.loads(response.read())
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(claim, ["alice", "bob"]))
        self.assertEqual([200, 409], sorted(code for code, _ in results))
        self.assertEqual(1, next(body for code, body in results if code == 200)["attempt"])

    def test_two_workers_parallel_issues_review_and_synchronize(self):
        """Alice and Bob work different issues at once, then both merge and sync."""
        import re

        (self.root / ".gitignore").write_text("runtime/\n", encoding="utf-8")
        self.init_git()
        parallel = json.loads(self.circle("issue-add", data={
            "title": "Parallel feature", "state": "ready",
            "goal": "Add the parallel feature.", "expected_behavior": "Feature exists.",
            "boundaries": "No unrelated changes.", "acceptance": ["Feature file exists"],
        }).stdout)["id"]
        self.git("add", "-A")
        self.git("commit", "-qm", "add parallel issue")
        self.coordinator.publish(self.root)
        self.git("add", "-A")
        self.git("commit", "-qm", "publish service state")

        def claim(pair):
            issue_id, actor = pair
            return self.http(f"/v1/tasks/{issue_id}/claim", actor, {"ttl": 300})

        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            alice_task, bob_task = list(pool.map(
                claim, [(self.ids["model"], "alice"), (parallel, "bob")]))
        self.assertEqual(("alice", "running"), (alice_task["owner"], alice_task["state"]))
        self.assertEqual(("bob", "running"), (bob_task["owner"], bob_task["state"]))
        self.assertNotEqual(alice_task["id"], bob_task["id"])

        submitted = {}
        for issue_id, actor, filename, text in (
            (self.ids["model"], "alice", "alice-feature.txt", "alice work\n"),
            (parallel, "bob", "bob-feature.txt", "bob work\n"),
        ):
            started = self.circle("issue-branch", "--id", issue_id).stdout
            worktree = Path(re.search(r"worktree at (.+) from [0-9a-f]+", started).group(1))
            (worktree / filename).write_text(text, encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=worktree, check=True)
            subprocess.run(["git", "commit", "-qm", f"{actor} implementation"],
                           cwd=worktree, check=True)
            commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=worktree,
                                    text=True, capture_output=True, check=True).stdout.strip()
            submitted[actor] = {
                "task": alice_task if actor == "alice" else bob_task,
                "commit": commit, "filename": filename,
            }
        # Both worktrees exist at the same time and hold different work.
        self.assertTrue(all(self.git(f"rev-parse", f"circle/{i}") for i in
                            (self.ids["model"], parallel)))

        for actor, item in submitted.items():
            task = self.http(f"/v1/tasks/{item['task']['id']}/submit", actor, {
                "attempt": item["task"]["attempt"], "commit": item["commit"],
                "evidence": [{"criterion": 1, "kind": "test-log", "locator": item["filename"],
                              "sha256": hashlib.sha256("evidence\n".encode()).hexdigest(),
                              "command": "python -m unittest", "exit_code": 0,
                              "observed_at": "2026-09-25T00:00:00+00:00",
                              "commit": item["commit"]}]})
            self.assertEqual("review", task["state"])
            task = self.http(f"/v1/tasks/{item['task']['id']}/assign", "owner", {
                "expected_version": task["version"], "reviewer": "reviewer"})
            task = self.http(f"/v1/tasks/{item['task']['id']}/review", "reviewer", {
                "expected_version": task["version"], "decision": "approve",
                "note": f"reviewed {actor}",
                "verdicts": [{"criterion": 1, "status": "pass", "note": "ok"}]})
            task = self.http(f"/v1/tasks/{item['task']['id']}/accept", "owner", {
                "expected_version": task["version"], "note": f"accepted {actor}",
                "verdicts": [{"criterion": 1, "status": "pass", "note": "accepted"}]})
            self.assertEqual("done", task["state"])

        for issue_id in (self.ids["model"], parallel):
            self.circle("issue-finish", "--id", issue_id)
        self.assertEqual(2, self.coordinator.sync(self.root)["synced"])
        self.circle("validate")
        self.assertEqual("alice work\n",
                         (self.root / "alice-feature.txt").read_text(encoding="utf-8"))
        self.assertEqual("bob work\n",
                         (self.root / "bob-feature.txt").read_text(encoding="utf-8"))
        for issue_id in (self.ids["model"], parallel):
            text = (self.store / "issues" / f"{issue_id}.md").read_text(encoding="utf-8")
            self.assertIn('state: "done"', text)
            self.assertIn("service_commit", text)
        if os.environ.get("CIRCLE_PARALLEL_EVIDENCE"):
            Path(os.environ["CIRCLE_PARALLEL_EVIDENCE"]).write_text(json.dumps({
                "kind": "two simulated users, concurrent HTTP claims, two worktrees, one merge/sync",
                "concurrent_claims": {
                    "alice": {"issue": alice_task["id"], "owner": alice_task["owner"],
                              "attempt": alice_task["attempt"]},
                    "bob": {"issue": bob_task["id"], "owner": bob_task["owner"],
                            "attempt": bob_task["attempt"]},
                },
                "commits": {actor: item["commit"] for actor, item in submitted.items()},
                "worktrees_existed_simultaneously": True,
                "merged_and_synced": {
                    "synced": 2,
                    "files": ["alice-feature.txt", "bob-feature.txt"],
                    "issues_done": [self.ids["model"], parallel],
                },
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def test_expired_lease_can_be_reclaimed_and_old_attempt_is_fenced(self):
        with patch("service.time.time", return_value=1):
            first = self.coordinator.act(self.ids["model"], "claim", "alice", "worker", {})
        second = self.act("claim", "bob")
        self.assertEqual(first["attempt"] + 1, second["attempt"])
        self.act("heartbeat", "alice", {"attempt": first["attempt"]}, expect=403)
        self.act("submit", "bob", {"attempt": first["attempt"]}, expect=409)
        self.act("heartbeat", "bob", {"attempt": second["attempt"], "ttl": 60})

    def test_review_rejection_requires_new_attempt_and_evidence(self):
        first = self.act("claim")
        submitted = self.assign(self.submit(first))
        rejected = self.act("review", "reviewer", {"expected_version": submitted["version"],
                                                   "decision": "reject", "note": "missing behavior",
                                                   "verdicts": [{"criterion":1,"status":"fail","note":"missing"}]})
        self.assertEqual("ready", rejected["state"])
        second = self.act("claim")
        self.assertIsNone(second["submission"])
        self.act("submit", data={"attempt": first["attempt"]}, expect=409)
        self.submit(second)

    def test_admin_can_finally_reject_approved_work(self):
        task=self.assign(self.submit(self.act("claim")))
        task=self.act("review","reviewer",{"expected_version":task["version"],"decision":"approve","note":"reviewed","verdicts":[{"criterion":1,"status":"pass","note":"ok"}]})
        task=self.act("reject","owner",{"expected_version":task["version"],"reason":"final acceptance failed"})
        self.assertEqual("ready",task["state"]);self.assertIsNone(task["submission"])
        self.assertEqual("final_reject",task["history"][-1]["action"])

    def test_auth_roles_designated_reviewer_and_stale_versions(self):
        self.http("/v1/tasks", "unknown", expect=401)
        task = self.act("claim")
        self.act("assign", "alice", {"expected_version": task["version"], "reviewer": "alice"}, expect=403)
        task = self.submit(task)
        self.act("review", "reviewer", {"expected_version": task["version"], "decision": "approve", "note": "x"}, expect=403)
        assigned = self.assign(task)
        self.act("review", "reviewer", {"expected_version": task["version"], "decision": "approve", "note": "x"}, expect=409)
        self.act("assign", "owner", {"expected_version": assigned["version"], "reviewer": "alice"}, expect=400)

    def test_publish_is_idempotent_and_rejects_changed_source(self):
        self.assertEqual(0, self.coordinator.publish(self.root)["published"])
        task = self.act("claim")
        self.assertIn("execution-locked", self.rejected("issue-edit", "--id", self.ids["model"], "--expected-revision", "1", data={"goal": "changed"}))
        current = next(t for t in self.coordinator.tasks() if t["id"] == task["id"])
        self.assertEqual(task["attempt"], current["attempt"])

    def test_invalid_submission_never_moves_to_review(self):
        claim = self.act("claim")
        for change in [{"commit": "main"}, {"evidence": []}, {"evidence": ["test.log"]},
                       {"evidence": [{"criterion":1,"kind":"test-log"}]},
                       {"evidence": [{"criterion":1,"kind":"test-log","locator":"x","sha256":"b"*64,"exit_code":0,"observed_at":"yesterday","commit":"a"*40}]}]:
            data = {"attempt": claim["attempt"], "commit": "a" * 40, "evidence": [{"criterion":1,"kind":"test-log","locator":"test.log","sha256":"b"*64,"exit_code":0,"observed_at":"2026-09-24T00:00:00Z","commit":"a"*40}]}
            data.update(change)
            self.act("submit", data=data, expect=400)
        self.assertEqual("running", next(t for t in self.coordinator.tasks() if t["id"] == self.ids["model"])["state"])

    def test_submit_after_expiration_is_rejected(self):
        with patch("service.time.time", return_value=1):
            claim = self.coordinator.act(self.ids["model"], "claim", "alice", "worker", {})
        self.act("submit", data={"attempt": claim["attempt"]}, expect=409)

    def test_assigned_worker_is_enforced(self):
        with self.coordinator.transaction() as db:
            tasks = self.coordinator._tasks(db)
            task = tasks[self.ids["model"]]
            task["source"]["assignee"] = "alice"
            self.coordinator._save(db, task, "fixture", "assign")
        self.act("claim", "bob", expect=403)
        self.act("claim", "alice")

    def test_release_cancel_retry_and_notification_cursor(self):
        claim=self.act("claim")
        released=self.act("release",data={"attempt":claim["attempt"],"reason":"handoff"})
        self.assertEqual("ready",released["state"])
        cancelled=self.act("cancel","owner",{"expected_version":released["version"],"reason":"scope"})
        self.assertEqual("cancelled",cancelled["state"])
        retried=self.act("retry","owner",{"expected_version":cancelled["version"],"reason":"restored"})
        self.assertEqual("ready",retried["state"])
        events=self.http("/v1/notifications?after=0","owner")
        self.assertIn("requeued",{e["notification"] for e in events})
        self.assertTrue(all(e["seq"]>events[0]["seq"] for e in self.http(f"/v1/notifications?after={events[0]['seq']}","owner")))

    def test_lease_expiring_is_a_derived_notification(self):
        with patch("service.time.time",return_value=100):claim=self.coordinator.act(self.ids["model"],"claim","alice","worker",{"ttl":30})
        with patch("service.time.time",return_value=105):notes=self.coordinator.notifications(0,"alice",["worker"])
        projected=[n for n in notes if n["notification"]=="lease_expiring"]
        self.assertEqual(self.ids["model"],projected[0]["issue"]);self.assertTrue(projected[0]["projection"])

    def test_reviewer_identity_is_enforced_in_domain_logic(self):
        task=self.submit(self.act("claim"))
        with self.assertRaisesRegex(Exception,"enabled independent reviewer"):
            self.coordinator.act(task["id"],"assign","owner","admin",{"expected_version":task["version"],"reviewer":"missing"})


class ServiceCliTest(CircleTestCase):
    def run_script(self, script, *args, expect=0):
        result = subprocess.run([sys.executable, str(SCRIPTS / script), *map(str, args)],
                                capture_output=True, text=True, encoding="utf-8", timeout=20)
        self.assertEqual(expect, result.returncode, result.stderr)
        return result

    def test_credentials_server_and_client_cli_end_to_end(self):
        payload = import_payload()
        for issue in payload["issues"]:
            issue.update(state="ready", assignee=None)
        ids = self.preview_commit(payload)
        database = self.root / "service.sqlite3"
        private = self.root / "private"
        self.run_script("service_credentials.py", "--directory", private, "--worker", "alice",
                        "--reviewer", "reviewer", "--admin", "owner")
        self.run_script("service.py", "--database", database, "publish", "--project-root", self.root)
        self.run_script("service.py", "--database", database, "serve", "--credentials", private / "server.json",
                        "--host", "0.0.0.0", expect=2)
        server = subprocess.Popen([sys.executable, str(SCRIPTS / "service.py"), "--database", str(database),
                                   "serve", "--credentials", str(private / "server.json"), "--port", "0"],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        try:
            with concurrent.futures.ThreadPoolExecutor(1) as pool:
                startup = pool.submit(server.stdout.readline).result(10)
            port = int(startup.strip().rsplit(":", 1)[1])
            base = ["--url", f"http://127.0.0.1:{port}", "--token-file", private / "user-1.token"]
            listing = self.run_script("service_client.py", *base)
            self.assertEqual(3, len(json.loads(listing.stdout)))
            body = self.root / "claim.json"
            body.write_text("{}", encoding="utf-8")
            claim = self.run_script("service_client.py", *base, "--path",
                                    f"/v1/tasks/{ids['model']}/claim", "--body", body)
            self.assertEqual("alice", json.loads(claim.stdout)["owner"])
        finally:
            server.terminate()
            server.communicate(timeout=10)
        status = self.run_script("service.py", "--database", database, "status")
        self.assertEqual("running", next(t for t in json.loads(status.stdout) if t["id"] == ids["model"])["state"])
