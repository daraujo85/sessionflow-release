import argparse
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

LOADER = importlib.machinery.SourceFileLoader("sf", str(Path(__file__).with_name("sf")))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
sf = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(sf)


class SfContractTests(unittest.TestCase):
    def test_context_keeps_only_relative_existing_paths(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, "artifact.txt").write_text("secret", encoding="utf-8")
            context = Path(root, "context.json")
            context.write_text(json.dumps({"paths": ["artifact.txt"], "facts": ["known"]}), encoding="utf-8")
            self.assertEqual(sf.load_context_file(str(context), root), {"paths": ["artifact.txt"], "facts": ["known"]})

    def test_context_rejects_parent_escape(self):
        with tempfile.TemporaryDirectory() as root:
            context = Path(root, "context.json")
            context.write_text(json.dumps({"paths": ["../secret"]}), encoding="utf-8")
            with self.assertRaises(ValueError):
                sf.load_context_file(str(context), root)

    def test_handoff_requires_exact_contract(self):
        handoff = {
            "schema_version": 1, "status": "done", "summary": "ok",
            "diff": [], "tests": ["python -m unittest"], "blockers": [], "questions": [],
        }
        self.assertEqual(sf.validate_handoff(handoff), handoff)
        handoff["extra"] = "no"
        with self.assertRaises(ValueError):
            sf.validate_handoff(handoff)

    def test_prompt_references_manifest_not_contents(self):
        prompt = sf.build_handoff_task("Do work", "/repo", "worker", "/repo/.sessionflow/handoff/worker.manifest.json")
        self.assertIn("worker.manifest.json", prompt)
        self.assertIn("handoff.json", prompt)
        self.assertIn("nunca puxe transcript", prompt)

    def _write_metric_pair(self, root, name="worker", objective="work", handoff=True):
        directory = Path(root, ".sessionflow", "handoff")
        directory.mkdir(parents=True, exist_ok=True)
        manifest = {"objective": objective, "context": {"facts": ["known"]}, "role": {"name": "qa"}}
        Path(directory, f"{name}.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        if handoff:
            Path(directory, f"{name}.handoff.json").write_text(json.dumps({
                "schema_version": 1, "status": "done", "summary": "ok",
                "diff": [], "tests": [], "blockers": [], "questions": []
            }), encoding="utf-8")

    def test_metrics_valid_and_missing_handoff(self):
        with tempfile.TemporaryDirectory() as root:
            self._write_metric_pair(root, "done")
            self._write_metric_pair(root, "pending", handoff=False)
            metrics = sf.read_metrics(root)
            self.assertEqual([m["status"] for m in metrics], ["done", "missing"])
            self.assertTrue(all(m["manifest_bytes"] > 0 for m in metrics))

    def test_metrics_marks_budget_and_invalid_json(self):
        with tempfile.TemporaryDirectory() as root:
            self._write_metric_pair(root, objective="x" * (sf.TASK_BUDGET_BYTES + 1))
            metrics = sf.read_metrics(root)
            self.assertIn(f"task_bytes>{sf.TASK_BUDGET_BYTES}", metrics[0]["violations"])
            Path(root, ".sessionflow", "handoff", "worker.handoff.json").write_text("{", encoding="utf-8")
            self.assertEqual(sf.read_metrics(root)[0]["status"], "invalid")

    def test_metrics_strict_fails_only_violation_or_invalid(self):
        from argparse import Namespace
        with tempfile.TemporaryDirectory() as root:
            self._write_metric_pair(root, handoff=False)
            sf.cmd_metrics(Namespace(dir=root, strict=True))
            Path(root, ".sessionflow", "handoff", "worker.manifest.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(SystemExit):
                sf.cmd_metrics(Namespace(dir=root, strict=True))
        with tempfile.TemporaryDirectory() as root:
            self._write_metric_pair(root, objective="x" * (sf.TASK_BUDGET_BYTES + 1))
            with self.assertRaises(SystemExit):
                sf.cmd_metrics(Namespace(dir=root, strict=True))


WORKERS = [
    {"host_id": "duck-id", "display_name": "Duck Server", "hostname": "DESKTOP-ASCBQRT", "emoji": "🦆", "online": True},
    {"host_id": "mac-id", "display_name": "Macbook Air", "hostname": "mac", "emoji": "🍎", "online": True},
    {"host_id": "old-id", "display_name": "LaptopAlvarenga", "hostname": "alv", "emoji": None, "online": False},
]


def _w(hid, cpu=10.0, mem=8.0, online=True, agents=None, metrics=True, cores=None, gpu=None):
    w = {
        "host_id": hid, "display_name": hid, "hostname": hid, "online": online,
        "metrics": {"cpu_pct": cpu, "mem_avail_gb": mem} if metrics else None,
        "agents": {"claude": True} if agents is None else agents,
    }
    if cores is not None or gpu is not None:
        w["hardware"] = {"cpu_cores": cores, "gpu": gpu}
    return w


OK = lambda w: None  # noqa: E731


class ResolveHostTests(unittest.TestCase):
    def test_exact_host_id_wins(self):
        self.assertEqual(sf.resolve_host(WORKERS, "duck-id")["host_id"], "duck-id")

    def test_exact_name_hostname_or_emoji_case_insensitive(self):
        self.assertEqual(sf.resolve_host(WORKERS, "duck server")["host_id"], "duck-id")
        self.assertEqual(sf.resolve_host(WORKERS, "desktop-ascbqrt")["host_id"], "duck-id")
        self.assertEqual(sf.resolve_host(WORKERS, "🦆")["host_id"], "duck-id")

    def test_unique_substring(self):
        self.assertEqual(sf.resolve_host(WORKERS, "duck")["host_id"], "duck-id")

    def test_ambiguous_substring_lists_candidates(self):
        with self.assertRaisesRegex(ValueError, "ambíguo"):
            sf.resolve_host(WORKERS, "a")

    def test_unknown_lists_known_hosts(self):
        with self.assertRaisesRegex(ValueError, "Duck Server"):
            sf.resolve_host(WORKERS, "nope")

    def test_offline_host_rejected(self):
        with self.assertRaisesRegex(ValueError, "offline"):
            sf.resolve_host(WORKERS, "alvarenga")

    def test_local_host_id_reads_file(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "hid")
            path.write_text("mac-id\n", encoding="utf-8")
            self.assertEqual(sf.local_host_id(path), "mac-id")
            self.assertIsNone(sf.local_host_id(Path(root, "missing")))


class SfApiTestCase(unittest.TestCase):
    """Base: API fake em memória; nada sai pra rede."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cwd = os.getcwd()
        os.chdir(tmp.name)
        self.addCleanup(os.chdir, cwd)
        self.calls = []
        self.workers = list(WORKERS)
        self.dirs = [{"path": "~/Documents/projects/sessionflow", "name": "sessionflow", "is_git": True}]
        self.shared = []
        self.session = {
            "id": "s1", "tmux_name": "w1", "status": "running",
            "work_dir": "~/Documents/projects/sessionflow-clones/w1",
        }

        def fake_request(method, url, token=None, body=None, timeout=30):
            self.calls.append((method, url, body))
            if url.endswith("/workers"):
                return 200, self.workers
            if "/directories?" in url:
                return 200, {"items": self.dirs}
            if url.endswith("/shared-files"):
                return 200, {"items": self.shared}
            if method == "POST" and url.endswith("/sessions"):
                return 202, {"command_id": "c1"}
            if url.endswith("/input"):
                return 202, {}
            raise AssertionError(f"rota inesperada: {method} {url}")

        patches = {
            "resolve_env": lambda args: {},
            "login": lambda env: "tok",
            "api_base": lambda env: "http://api",
            "detect_parent": lambda: "pai",
            "local_host_id": lambda path=None: "mac-id",
            "list_sessions": lambda env, token: [self.session],
            "_request": fake_request,
        }
        for attr, value in patches.items():
            patcher = mock.patch.object(sf, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = mock.patch.object(sf.time, "sleep", lambda s: None)
        patcher.start()
        self.addCleanup(patcher.stop)
        # Shell do dev pode exportar SF_DEFAULT_HOST=auto; teste não herda.
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("SF_DEFAULT_HOST", None)

    def run_sf(self, *argv):
        args = sf.build_parser().parse_args(list(argv))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            args.func(args)
        return out.getvalue()

    def run_sf_error(self, *argv):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
            self.run_sf(*argv)
        return err.getvalue()


class DirsCommandTests(SfApiTestCase):
    def test_dirs_lists_paths_with_git_marker(self):
        out = self.run_sf("dirs", "--host", "duck", "sessionflow")
        self.assertIn("~/Documents/projects/sessionflow  [git]", out)
        url = next(u for m, u, b in self.calls if "/directories?" in u)
        self.assertIn("host_id=duck-id", url)
        self.assertIn("q=sessionflow", url)
        self.assertIn("limit=20", url)

    def test_dirs_unknown_host_dies(self):
        self.assertIn("nope", self.run_sf_error("dirs", "--host", "nope"))


class RemoteDirTests(unittest.TestCase):
    HOME = "/Users/diegoaraujo"

    def test_is_literal_remote_path_recognizes_absolute_and_tilde(self):
        self.assertTrue(sf._is_literal_remote_path("/mnt/c/repo"))
        self.assertTrue(sf._is_literal_remote_path("~"))
        self.assertTrue(sf._is_literal_remote_path("~/x"))
        self.assertFalse(sf._is_literal_remote_path("~user/x"))
        self.assertFalse(sf._is_literal_remote_path("sessionflow"))

    def test_absolute_and_tilde_paths_pass_through(self):
        self.assertEqual(sf.resolve_remote_dir("/mnt/c/repo/x", [], self.HOME), {"path": "/mnt/c/repo/x", "is_git": None})
        self.assertEqual(sf.resolve_remote_dir("~/x", [], self.HOME), {"path": "~/x", "is_git": None})

    def test_locally_expanded_home_rejected(self):
        with self.assertRaisesRegex(ValueError, "aspas"):
            sf.resolve_remote_dir("/Users/diegoaraujo/Documents/projects/x", [], self.HOME)

    def test_name_prefers_exact_match(self):
        dirs = [
            {"path": "~/p/sessionflow-clones/a", "name": "a", "is_git": True},
            {"path": "~/p/sessionflow", "name": "sessionflow", "is_git": True},
        ]
        self.assertEqual(sf.resolve_remote_dir("sessionflow", dirs, self.HOME), {"path": "~/p/sessionflow", "is_git": True})

    def test_two_exact_matches_are_ambiguous(self):
        dirs = [
            {"path": "/mnt/c/repo/sessionflow", "name": "sessionflow", "is_git": True},
            {"path": "~/Documents/projects/sessionflow", "name": "sessionflow", "is_git": True},
        ]
        with self.assertRaisesRegex(ValueError, "/mnt/c/repo/sessionflow.*~/Documents/projects/sessionflow"):
            sf.resolve_remote_dir("sessionflow", dirs, self.HOME)

    def test_old_doc_without_is_git_is_none(self):
        dirs = [{"path": "~/p/x", "name": "x"}]
        self.assertEqual(sf.resolve_remote_dir("x", dirs, self.HOME), {"path": "~/p/x", "is_git": None})

    def test_unknown_name_suggests_sf_dirs(self):
        with self.assertRaisesRegex(ValueError, "sf dirs"):
            sf.resolve_remote_dir("nada", [], self.HOME)


class RemoteSlugifyTests(unittest.TestCase):
    def test_slugify_remote_lowercases_strips_accents_and_replaces_runs(self):
        self.assertEqual(sf.slugify_remote("Foo_Bar"), "foo-bar")
        self.assertEqual(sf.slugify_remote("Café_Test"), "cafe-test")
        self.assertEqual(sf.slugify_remote("TestMa___Zing"), "testma-zing")
        self.assertEqual(sf.slugify_remote("My-Project"), "my-project")
        self.assertEqual(sf.slugify_remote("Café da Manhã"), "cafe-da-manha")


class RemoteTaskTests(unittest.TestCase):
    MANIFEST = {"schema_version": 1, "objective": "Do work", "context": {}}

    def test_patch_mode_instructions(self):
        text = sf.build_remote_task("Do work", "~/r-clones/w1", "w1", self.MANIFEST, "pai", push=False)
        self.assertTrue(text.startswith("PASSO 0"))
        self.assertIn("git rev-parse HEAD", text)
        self.assertIn('"objective":"Do work"', text)
        self.assertIn("git format-patch <BASE>..HEAD --stdout > ~/r-clones/w1/.sessionflow/handoff/w1.patch", text)
        self.assertIn("sf share ~/r-clones/w1/.sessionflow/handoff/w1.handoff.json", text)
        self.assertIn("sf share ~/r-clones/w1/.sessionflow/handoff/w1.patch", text)
        self.assertIn('sf send pai "✅ HANDOFF w1 status=<status do JSON>"', text)
        self.assertNotIn("Depois de escrever ambos, PARE", text)

    def test_push_mode_without_parent(self):
        text = sf.build_remote_task("Do work", "~/r", "w1", self.MANIFEST, None, push=True)
        self.assertIn("git push -u origin HEAD", text)
        self.assertNotIn("format-patch", text)
        self.assertNotIn('"✅ HANDOFF', text)

    def test_local_task_keeps_original_finish(self):
        self.assertIn("Depois de escrever ambos, PARE", sf.build_handoff_task("t", "/r", "w"))

class LocalDelegateTests(SfApiTestCase):
    ARGS = ("delegate", "--provider", "claude", "--dir", ".", "--name", "w1", "--task", "Do work")

    def _task_text(self):
        return next(b for m, u, b in self.calls if u.endswith("/input"))["text"]

    def test_local_delegate_with_parent_tells_child_to_notify(self):
        self.run_sf(*self.ARGS)
        text = self._task_text()
        self.assertIn('sf send pai "✅ HANDOFF w1 status=<status do JSON>"', text)
        self.assertNotIn("Depois de escrever ambos, PARE", text)

    def test_local_delegate_without_parent_keeps_plain_finish(self):
        with mock.patch.object(sf, "detect_parent", lambda: None):
            self.run_sf(*self.ARGS)
        text = self._task_text()
        self.assertIn("Depois de escrever ambos, PARE", text)
        self.assertNotIn("✅ HANDOFF", text)

    def test_local_delegate_without_host_pins_local_worker(self):
        self.run_sf(*self.ARGS)
        post = next(b for m, u, b in self.calls if m == "POST" and u.endswith("/sessions"))
        self.assertEqual(post["host_id"], "mac-id")

    def test_local_delegate_without_host_id_file_lets_api_pick(self):
        with mock.patch.object(sf, "local_host_id", lambda path=None: None):
            self.run_sf(*self.ARGS)
        post = next(b for m, u, b in self.calls if m == "POST" and u.endswith("/sessions"))
        self.assertNotIn("host_id", post)

    def test_default_host_env_var_used_when_host_omitted(self):
        with mock.patch.dict(os.environ, {"SF_DEFAULT_HOST": "duck"}):
            self.run_sf(*self.ARGS)
        post = next(b for m, u, b in self.calls if m == "POST" and u.endswith("/sessions"))
        self.assertEqual(post["host_id"], "duck-id")


class RemoteDelegateTests(SfApiTestCase):
    ARGS = ("delegate", "--provider", "claude", "--host", "duck", "--dir", "sessionflow",
            "--name", "w1", "--task", "Do work")

    def _post_body(self):
        return next(b for m, u, b in self.calls if m == "POST" and u.endswith("/sessions"))

    def test_remote_worktree_payload_task_and_registry(self):
        self.run_sf(*self.ARGS, "--worktree")
        post = self._post_body()
        self.assertEqual(post["host_id"], "duck-id")
        self.assertEqual(post["work_dir"], "~/Documents/projects/sessionflow")
        self.assertEqual(post["worktree_branch"], "sf/w1")
        self.assertEqual(post["parent"], "pai")
        text = next(b for m, u, b in self.calls if u.endswith("/input"))["text"]
        self.assertIn("git rev-parse HEAD", text)
        self.assertIn('"objective":"Do work"', text)
        self.assertIn("sessionflow-clones/w1/.sessionflow/handoff/w1.handoff.json", text)
        self.assertIn("format-patch", text)
        reg = json.loads(Path(".sessionflow/handoff/w1.registry.json").read_text(encoding="utf-8"))
        self.assertTrue(reg["remote"])
        self.assertEqual(reg["host_id"], "duck-id")
        self.assertEqual(reg["work_dir"], self.session["work_dir"])
        self.assertEqual(reg["dir"], os.getcwd())
        self.assertFalse(Path(".sessionflow/handoff/w1.manifest.json").exists())

    def test_remote_name_slugified_like_worker(self):
        args = list(self.ARGS)
        args[args.index("w1")] = "Foo_Bar"
        self.session["tmux_name"] = "foo-bar"
        self.run_sf(*args, "--worktree")
        post = self._post_body()
        self.assertEqual(post["name"], "foo-bar")
        self.assertEqual(post["worktree_branch"], "sf/foo-bar")
        self.assertTrue(Path(".sessionflow/handoff/foo-bar.registry.json").exists())

    def test_push_without_worktree_dies_before_create(self):
        self.assertIn("--push exige --worktree", self.run_sf_error(*self.ARGS, "--push"))
        self.assertFalse(any(m == "POST" for m, u, b in self.calls))

    def test_push_flag_switches_to_push_instructions(self):
        self.run_sf(*self.ARGS, "--worktree", "--push")
        text = next(b for m, u, b in self.calls if u.endswith("/input"))["text"]
        self.assertIn("git push -u origin sf/w1", text)
        self.assertTrue(json.loads(Path(".sessionflow/handoff/w1.registry.json").read_text())["push"])

    def test_worktree_on_non_git_dir_dies_before_create(self):
        self.dirs = [{"path": "~/plain", "name": "sessionflow", "is_git": False}]
        self.assertIn("não é repositório git", self.run_sf_error(*self.ARGS, "--worktree"))
        self.assertFalse(any(m == "POST" for m, u, b in self.calls))

    def test_worktree_with_unknown_is_git_proceeds(self):
        self.dirs = [{"path": "~/old", "name": "sessionflow"}]
        self.run_sf(*self.ARGS, "--worktree")
        self.assertEqual(self._post_body()["work_dir"], "~/old")

    def test_remote_requires_dir(self):
        args = [a for a in self.ARGS if a not in ("--dir", "sessionflow")]
        self.assertIn("--dir", self.run_sf_error(*args))

    def test_timeout_message_names_host_and_dir(self):
        self.session["status"] = "starting"
        with mock.patch.object(sf, "CREATE_TIMEOUT_SECONDS", 0):
            err = self.run_sf_error(*self.ARGS, "--worktree")
        self.assertIn("Duck Server", err)
        self.assertIn("~/Documents/projects/sessionflow", err)
        self.assertIn("repo git", err)

    def test_local_host_alias_keeps_local_flow_with_host_id(self):
        self.session["work_dir"] = os.getcwd()
        self.run_sf("delegate", "--provider", "claude", "--host", "mac", "--name", "w1", "--task", "Do work")
        post = self._post_body()
        self.assertEqual(post["host_id"], "mac-id")
        self.assertEqual(post["work_dir"], os.path.abspath(os.getcwd()))
        self.assertNotIn("worktree_branch", post)
        self.assertTrue(Path(".sessionflow/handoff/w1.manifest.json").exists())

    def test_worktree_requires_remote_host(self):
        err = self.run_sf_error("delegate", "--provider", "claude", "--worktree", "--name", "w1", "--task", "t")
        self.assertIn("--host", err)


class RemoteCheckTests(SfApiTestCase):
    HANDOFF = {"schema_version": 1, "status": "done", "summary": "ok",
               "diff": ["w1.patch"], "tests": [], "blockers": [], "questions": []}

    def setUp(self):
        super().setUp()
        hdir = Path(".sessionflow/handoff")
        hdir.mkdir(parents=True)
        (hdir / "w1.registry.json").write_text(json.dumps(
            {"id": "s1", "name": "w1", "remote": True, "dir": os.getcwd(), "host_id": "duck-id"}
        ), encoding="utf-8")
        self.contents = {"f2": json.dumps(self.HANDOFF), "f1": "velho", "f3": "# md", "f4": "patch", "f5": "x"}
        self.shared = [  # API: mais recente primeiro
            {"id": "f2", "filename": "w1.handoff.json"},
            {"id": "f1", "filename": "w1.handoff.json"},
            {"id": "f3", "filename": "w1.md"},
            {"id": "f4", "filename": "w1.patch"},
            {"id": "f5", "filename": "other.md"},
        ]
        self.downloaded = []

        def fake_download(url, token, dest):
            fid = url.rstrip("/").split("/")[-2]
            self.downloaded.append(fid)
            Path(dest).write_text(self.contents[fid], encoding="utf-8")

        patcher = mock.patch.object(sf, "_download", fake_download)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_summary_uses_newest_files_only(self):
        out = self.run_sf("check", "w1", "--summary")
        self.assertIn("status=done", out)
        self.assertEqual(sorted(self.downloaded), ["f2", "f3", "f4"])
        self.assertFalse(Path(".sessionflow/handoff/other.md").exists())

    def test_full_check_prints_md_and_patch_hint(self):
        out = self.run_sf("check", "w1")
        self.assertIn("# md", out)
        self.assertIn("git am", out)

    def test_patch_hint_printed_before_json_validation_failure(self):
        # Patch file exists but JSON is invalid — hint should still print
        self.contents = {"f2": "{", "f3": "# md", "f4": "patch"}
        self.shared = [
            {"id": "f2", "filename": "w1.handoff.json"},
            {"id": "f3", "filename": "w1.md"},
            {"id": "f4", "filename": "w1.patch"},
        ]
        out = self.run_sf("check", "w1")
        self.assertIn("git am", out)
        self.assertIn("inválido", out)

    def test_empty_patch_skips_hint(self):
        # Patch file exists but is empty (0 bytes)
        self.contents = {"f3": "# md", "f4": ""}
        self.shared = [
            {"id": "f3", "filename": "w1.md"},
            {"id": "f4", "filename": "w1.patch"},
        ]
        out = self.run_sf("check", "w1")
        self.assertNotIn("git am", out)
        self.assertIn("patch vazio", out)

    def test_no_shared_files_yet_means_running(self):
        self.shared = []
        self.assertIn("ainda rodando", self.run_sf("check", "w1", "--summary"))

    def test_remote_dir_flag_falls_back_to_local_registry(self):
        # --dir aponta pro path REMOTO (não existe aqui); registro vive no cwd
        out = self.run_sf("check", "w1", "--dir", "/mnt/c/repo")
        self.assertIn("# md", out)
        self.assertIn("f3", self.downloaded)

    def test_no_registry_but_session_on_other_host_pulls_shared_files(self):
        Path(".sessionflow/handoff/w1.registry.json").unlink()
        self.session = {**self.session, "host_id": "duck-id"}
        out = self.run_sf("check", "w1")
        self.assertIn("# md", out)
        self.assertTrue(Path(".sessionflow/handoff/w1.md").exists())

    def test_no_registry_local_session_does_not_pull(self):
        Path(".sessionflow/handoff/w1.registry.json").unlink()
        self.session = {**self.session, "host_id": "mac-id"}
        out = self.run_sf("check", "w1")
        self.assertEqual(self.downloaded, [])
        self.assertIn("ainda rodando", out)

    def test_pick_remote_files_ignores_paths_and_other_names(self):
        items = [{"id": "a", "filename": "../w1.md"}, {"id": "b", "filename": "w1.md"}]
        self.assertEqual([i["id"] for i in sf.pick_remote_files(items, "w1")], ["b"])


class PickHostTests(unittest.TestCase):
    def test_highest_score_wins(self):
        chosen, ranked, _, _ = sf.pick_host([_w("mac", 50, 4), _w("duck", 1, 16)], "claude", "mac", OK)
        self.assertEqual(chosen["host_id"], "duck")
        self.assertEqual([w["host_id"] for w in ranked], ["duck", "mac"])

    def test_more_cores_beats_same_free_fraction(self):
        # Mesma carga e RAM livre: quem tem mais núcleos tem mais poder livre.
        chosen, _, _, _ = sf.pick_host(
            [_w("mac", 20, 8, cores=8), _w("deepin", 20, 8, cores=16)], "claude", "x", OK)
        self.assertEqual(chosen["host_id"], "deepin")

    def test_big_idle_box_beats_small_idle_box(self):
        # Pequeno quase ocioso perde pro grande moderadamente usado.
        chosen, _, _, _ = sf.pick_host(
            [_w("small", 5, 3, cores=2), _w("big", 40, 10, cores=16)], "claude", "x", OK)
        self.assertEqual(chosen["host_id"], "big")

    def test_gpu_breaks_otherwise_equal(self):
        chosen, _, _, _ = sf.pick_host(
            [_w("a", 10, 8, cores=8), _w("b", 10, 8, cores=8, gpu="RTX 3060")], "claude", "x", OK)
        self.assertEqual(chosen["host_id"], "b")

    def test_unknown_cores_does_not_crash(self):
        w = _w("x", 10, 8); w["hardware"] = {"cpu_cores": None}
        self.assertGreater(sf.host_score(w), 0)

    def test_tie_prefers_local(self):
        chosen, _, _, _ = sf.pick_host([_w("duck", 10, 8.2), _w("mac", 10, 8.0)], "claude", "mac", OK)
        self.assertEqual(chosen["host_id"], "mac")

    def test_rejects_offline_missing_agent_and_old_worker(self):
        ws = [_w("off", online=False), _w("noagent", agents={"claude": False}),
              _w("old", metrics=False), _w("ok")]
        chosen, _, rejected, _ = sf.pick_host(ws, "claude", "mac", OK)
        self.assertEqual(chosen["host_id"], "ok")
        reasons = {w["host_id"]: r for w, r in rejected}
        self.assertIn("offline", reasons["off"])
        self.assertIn("claude", reasons["noagent"])
        self.assertIn("métricas", reasons["old"])

    def test_none_metric_values_rejected_without_crash(self):
        w = _w("x"); w["metrics"] = {"cpu_pct": None, "mem_avail_gb": 4.0}
        chosen, _, rejected, _ = sf.pick_host([w], "claude", "mac", OK)
        self.assertIsNone(chosen)
        self.assertIn("métricas", rejected[0][1])

    def test_floor_rejects_busy_and_low_memory(self):
        chosen, _, rejected, _ = sf.pick_host([_w("busy", 90, 16), _w("tight", 5, 1.5)], "claude", "mac", OK)
        self.assertIsNone(chosen)
        reasons = {w["host_id"]: r for w, r in rejected}
        self.assertIn("cpu", reasons["busy"])
        self.assertIn("ram", reasons["tight"])

    def test_need_remote_excludes_local(self):
        chosen, _, _, _ = sf.pick_host([_w("mac", 1, 32), _w("duck", 50, 4)], "claude", "mac", OK, need_remote=True)
        self.assertEqual(chosen["host_id"], "duck")

    def test_dir_check_runs_last_and_reason_kept(self):
        seen = []
        def dir_ok(w):
            seen.append(w["host_id"])
            return "pasta ambígua" if w["host_id"] == "duck" else None
        chosen, _, rejected, _ = sf.pick_host(
            [_w("duck", 1, 16), _w("mac", 10, 8), _w("busy", 99, 8)], "claude", "mac", dir_ok)
        self.assertEqual(chosen["host_id"], "mac")
        self.assertNotIn("busy", seen)  # barrado no piso antes de gastar rede
        self.assertIn(("duck", "pasta ambígua"), [(w["host_id"], r) for w, r in rejected])

    def test_priority_host_wins_over_higher_score(self):
        """Colab Pro tem score menor que Duck, mas AUTO_PRIORITY_HOSTS põe
        ele na frente — escolhido desde que online e com o agent."""
        colab_pro = sf.AUTO_PRIORITY_HOSTS[0]
        duck = _w("duck", 1, 16, cores=16, gpu="RTX 3060")  # score enorme
        colab = _w(colab_pro, 50, 4)  # score menor, mas prioritário
        chosen, _, _, _ = sf.pick_host([duck, colab], "claude", "mac", OK)
        self.assertEqual(chosen["host_id"], colab_pro)

    def test_priority_host_falls_through_when_offline_or_busy(self):
        """Colab prioritário offline OU sem o agent → ranking padrão assume."""
        colab_pro = sf.AUTO_PRIORITY_HOSTS[0]
        colab_free = sf.AUTO_PRIORITY_HOSTS[1] if len(sf.AUTO_PRIORITY_HOSTS) > 1 else None
        offline_colab = _w(colab_pro, online=False)
        duck = _w("duck", 1, 16, cores=16, gpu="RTX 3060")
        if colab_free:
            noagent_colab = _w(colab_free, agents={"claude": False})
            chosen, _, _, _ = sf.pick_host([offline_colab, noagent_colab, duck], "claude", "mac", OK)
        else:
            chosen, _, _, _ = sf.pick_host([offline_colab, duck], "claude", "mac", OK)
        self.assertEqual(chosen["host_id"], "duck")

    def test_all_four_priority_hosts_tried_in_order(self):
        """Os 4 Colabs (2 Pro T4 + 2 CPU) são tentados em ordem; só cai no
        ranking padrão se todos falharem (offline/ocupado/sem agent/dir)."""
        self.assertGreaterEqual(len(sf.AUTO_PRIORITY_HOSTS), 4,
            msg="esperado 4+ hosts prioritários; lista tem só " + str(len(sf.AUTO_PRIORITY_HOSTS)))
        # Todos os 4 Colabs online+ok → primeiro da lista vence (mesmo com Duck
        # tendo score muito maior).
        colabs = [_w(sf.AUTO_PRIORITY_HOSTS[i], cpu=50, mem=4) for i in range(4)]
        duck = _w("duck", 1, 16, cores=16, gpu="RTX 3060")
        chosen, _, rejected, _ = sf.pick_host(colabs + [duck], "claude", "mac", OK)
        self.assertEqual(chosen["host_id"], sf.AUTO_PRIORITY_HOSTS[0])
        # Nenhum Colab foi rejeitado (todos passaram) → Duck não aparece em rejected
        rejected_ids = [w["host_id"] for w, _ in rejected]
        self.assertNotIn("duck", rejected_ids)
        # Os 3 Colabs seguintes não foram nem consultados (retorno antecipado)
        # → confirmam que a primeira passagem válida ganha.

    def test_priority_chain_walks_through_offline_to_fourth(self):
        """3 primeiros Colabs offline → o 4º é o escolhido (antes de cair no
        ranking padrão)."""
        offline3 = [_w(sf.AUTO_PRIORITY_HOSTS[i], online=False) for i in range(3)]
        fourth = _w(sf.AUTO_PRIORITY_HOSTS[3])
        duck = _w("duck", 1, 16, cores=16, gpu="RTX 3060")
        chosen, _, _, _ = sf.pick_host(offline3 + [fourth, duck], "claude", "mac", OK)
        self.assertEqual(chosen["host_id"], sf.AUTO_PRIORITY_HOSTS[3])


DUCK_ID = "67122d03-0b03-4b80-9001-ba982603d0fb"


class GpuPickHostTests(unittest.TestCase):
    """`--gpu`: GPU estável (Duck) > Colab T4 online (overflow); nunca Colab offline."""

    def setUp(self):
        self.colab_t4 = _w(sf.AUTO_PRIORITY_HOSTS[0], cpu=1, mem=12, gpu="Tesla T4")
        self.colab_t4_b = _w(sf.AUTO_PRIORITY_HOSTS[1], cpu=1, mem=12, gpu="Tesla T4")
        self.colab_cpu = _w(sf.AUTO_PRIORITY_HOSTS[2], cpu=1, mem=12)
        self.duck = _w(DUCK_ID, cpu=40, mem=6, cores=8, gpu="RTX 3060")
        self.mac = _w("mac", cpu=1, mem=30, cores=16)

    def _reasons(self, rejected):
        return {w["host_id"]: r for w, r in rejected}

    def test_is_colab_by_label_and_known_ids(self):
        self.assertTrue(sf.is_colab({"display_name": "⚡ Colab 2 Pro", "host_id": "x"}))
        self.assertTrue(sf.is_colab({"hostname": "19db473d4190", "host_id": sf.AUTO_PRIORITY_HOSTS[0]}))
        self.assertFalse(sf.is_colab({"display_name": "Duck Server", "hostname": "DESKTOP-ASCBQRT",
                                      "host_id": DUCK_ID}))
        self.assertTrue(sf._colab_info({"display_name": "Colab 1", "host_id": "y"})["is_colab"])

    def test_gpu_picks_duck_even_with_free_colab_t4(self):
        ws = [self.colab_t4, self.colab_t4_b, self.colab_cpu, self.duck, self.mac]
        chosen, ranked, rejected, _ = sf.pick_host(ws, "claude", "mac", OK, gpu=True)
        self.assertEqual(chosen["host_id"], DUCK_ID)
        self.assertEqual([w["host_id"] for w in ranked], [DUCK_ID])
        reasons = self._reasons(rejected)
        self.assertEqual(reasons["mac"], "sem GPU")
        self.assertEqual(reasons[sf.AUTO_PRIORITY_HOSTS[2]], "sem GPU")
        self.assertIn("colab = overflow", reasons[sf.AUTO_PRIORITY_HOSTS[0]])

    def test_gpu_duck_busy_falls_to_online_colab_t4(self):
        self.duck["metrics"]["cpu_pct"] = 99
        chosen, _, rejected, _ = sf.pick_host([self.duck, self.colab_t4_b, self.colab_t4],
                                              "claude", "mac", OK, gpu=True)
        self.assertEqual(chosen["host_id"], sf.AUTO_PRIORITY_HOSTS[0])  # ordem de prioridade
        self.assertIn("cpu", self._reasons(rejected)[DUCK_ID])

    def test_gpu_duck_offline_skips_offline_colab(self):
        self.duck["online"] = False
        self.colab_t4["online"] = False
        chosen, _, rejected, _ = sf.pick_host([self.duck, self.colab_t4, self.colab_t4_b],
                                              "claude", "mac", OK, gpu=True)
        self.assertEqual(chosen["host_id"], sf.AUTO_PRIORITY_HOSTS[1])
        self.assertEqual(self._reasons(rejected)[sf.AUTO_PRIORITY_HOSTS[0]],
                         "colab = overflow: offline")

    def test_gpu_no_gpu_host_returns_none_without_local_fallback(self):
        self.duck["online"] = False
        self.colab_t4["online"] = False
        chosen, ranked, rejected, local_serves = sf.pick_host(
            [self.duck, self.colab_t4, self.colab_cpu, self.mac], "claude", "mac", OK, gpu=True)
        self.assertIsNone(chosen)
        self.assertEqual(ranked, [])
        self.assertFalse(local_serves)
        self.assertEqual(self._reasons(rejected)["mac"], "sem GPU")

    def test_gpu_respects_dir_ok(self):
        dir_ok = lambda w: "pasta 'x' não indexada" if w["host_id"] == DUCK_ID else None  # noqa: E731
        chosen, _, rejected, _ = sf.pick_host([self.duck, self.colab_t4], "claude", "mac",
                                              dir_ok, gpu=True)
        self.assertEqual(chosen["host_id"], sf.AUTO_PRIORITY_HOSTS[0])
        self.assertIn("não indexada", self._reasons(rejected)[DUCK_ID])

    def test_gpu_low_vram_duck_falls_to_colab(self):
        self.duck["metrics"].update(vram_total_gb=12.0, vram_used_gb=10.8)
        self.colab_t4["metrics"].update(vram_total_gb=15.0, vram_used_gb=0.0)
        chosen, _, rejected, _ = sf.pick_host([self.duck, self.colab_t4], "claude", "mac",
                                              OK, gpu=True)
        self.assertEqual(chosen["host_id"], sf.AUTO_PRIORITY_HOSTS[0])
        self.assertIn("vram livre 1.2G", self._reasons(rejected)[DUCK_ID])

    def test_gpu_low_vram_colab_marked_overflow(self):
        self.duck["online"] = False
        self.colab_t4["metrics"].update(vram_total_gb=15.0, vram_used_gb=14.5)
        chosen, _, rejected, _ = sf.pick_host([self.duck, self.colab_t4], "claude", "mac",
                                              OK, gpu=True)
        self.assertIsNone(chosen)
        self.assertIn("colab = overflow: vram livre", self._reasons(rejected)[sf.AUTO_PRIORITY_HOSTS[0]])

    def test_gpu_vram_unknown_or_enough_passes(self):
        chosen, _, _, _ = sf.pick_host([self.duck], "claude", "mac", OK, gpu=True)
        self.assertEqual(chosen["host_id"], DUCK_ID)  # worker sem vram_* → não barra
        self.duck["metrics"].update(vram_total_gb=12.0, vram_used_gb=4.0)
        chosen, _, _, _ = sf.pick_host([self.duck], "claude", "mac", OK, gpu=True)
        self.assertEqual(chosen["host_id"], DUCK_ID)

    def test_gpu_vram_floor_configurable(self):
        self.duck["metrics"].update(vram_total_gb=12.0, vram_used_gb=4.0)
        with mock.patch.object(sf, "AUTO_GPU_MIN_VRAM_GB", 10.0):
            chosen, _, rejected, _ = sf.pick_host([self.duck], "claude", "mac", OK, gpu=True)
        self.assertIsNone(chosen)
        self.assertIn("< 10.0G", self._reasons(rejected)[DUCK_ID])

    def test_without_gpu_behavior_unchanged(self):
        ws = [self.colab_t4, self.duck, self.mac]
        chosen, _, _, _ = sf.pick_host(ws, "claude", "mac", OK)
        self.assertEqual(chosen["host_id"], sf.AUTO_PRIORITY_HOSTS[0])  # Colab prioritário segue
        chosen, _, _, _ = sf.pick_host([self.duck, self.mac], "claude", "mac", OK)
        self.assertEqual(chosen["host_id"], "mac")  # score, mesmo sem GPU


class AutoDelegateTests(SfApiTestCase):
    ARGS = ("delegate", "--provider", "claude", "--host", "auto", "--dir", "sessionflow",
            "--name", "w1", "--task", "Do work")

    def setUp(self):
        super().setUp()
        Path("sessionflow").mkdir()
        self.workers = [
            {**WORKERS[0], "metrics": {"cpu_pct": 1.0, "mem_avail_gb": 16.0}, "agents": {"claude": True}},
            {**WORKERS[1], "metrics": {"cpu_pct": 20.0, "mem_avail_gb": 0.5}, "agents": {"claude": True}},
        ]

    def _post(self):
        return next(b for m, u, b in self.calls if m == "POST" and u.endswith("/sessions"))

    def test_auto_picks_freest_remote_and_reports_host(self):
        out = self.run_sf(*self.ARGS)
        self.assertEqual(self._post()["host_id"], "duck-id")
        self.assertEqual(self._post()["work_dir"], "~/Documents/projects/sessionflow")
        self.assertIn("host auto → 🦆 Duck Server", out)
        self.assertIn("Macbook Air", out)  # descartado listado
        self.assertIn("host:", out)
        reg = json.loads(Path(".sessionflow/handoff/w1.registry.json").read_text(encoding="utf-8"))
        self.assertTrue(reg["auto"])
        self.assertEqual(reg["host_id"], "duck-id")

    def test_auto_uppercase_also_works(self):
        args = list(self.ARGS); args[args.index("auto")] = "AUTO"
        self.run_sf(*args)
        self.assertEqual(self._post()["host_id"], "duck-id")

    def test_auto_falls_back_to_local_when_no_candidate(self):
        """Nenhum remote serve MAS o local aguenta → Mac é escolhido (Phase 2)."""
        self.workers[0]["agents"] = {"claude": False}
        # Garante que o Mac local passa cpu/ram (default já é OK)
        self.workers[1]["metrics"] = {"cpu_pct": 10.0, "mem_avail_gb": 8.0}
        out = self.run_sf(*self.ARGS)
        self.assertEqual(self._post()["host_id"], "mac-id")
        self.assertIn("Macbook Air", out)

    def test_auto_dies_when_local_cannot_handle(self):
        """Nenhum remote serve E local está sem RAM (ou cpu alta) → die().
        Evita subir sessão que o worker mata por OOM em segundos."""
        self.workers[0]["agents"] = {"claude": False}
        # Mac com ram=0.5G (abaixo de AUTO_MIN_MEM_GB=2.0) — não aguenta um agent
        self.workers[1]["metrics"] = {"cpu_pct": 10.0, "mem_avail_gb": 0.5}
        out = self.run_sf_error(*self.ARGS)
        self.assertIn("não aguenta", out)
        self.assertIn("Duck", out)  # motivo de descarte do remoto listado
        self.assertIn("sf dirs", out)  # dica de como resolver

    def test_auto_dies_when_local_cpu_pegada(self):
        """Mac com cpu>85% → die() mesmo com RAM ok."""
        self.workers[0]["agents"] = {"claude": False}
        self.workers[1]["metrics"] = {"cpu_pct": 95.0, "mem_avail_gb": 8.0}
        out = self.run_sf_error(*self.ARGS)
        self.assertIn("não aguenta", out)

    def test_auto_local_serves_flag_in_pick_host_return(self):
        """O 4º elemento da tupla indica se o local aguenta — usado pelo caller
        pra decidir entre die() e fallback silencioso."""
        duck_busy = _w("duck", cpu=95, mem=0.5, agents={"claude": True})  # busy+ram<2G
        mac = _w("mac", cpu=10, mem=8)
        chosen, _, _, local_serves = sf.pick_host(
            [duck_busy, mac], "claude", "mac", OK)
        # Duck rejeitado nos filtros (ram<2G); Mac é o único no pool → chosen=Mac.
        # local_serves=True (Mac aguenta) → caller pode cair pra local sem erro.
        self.assertEqual(chosen["host_id"], "mac")
        self.assertTrue(local_serves)

        mac_bad = _w("mac-bad", cpu=10, mem=0.5)
        chosen, _, _, local_serves = sf.pick_host(
            [duck_busy, mac_bad], "claude", "mac-bad", OK)
        # Mac_bad rejeitado (ram<2G) → chosen=None, local_serves=False → die().
        self.assertIsNone(chosen)
        self.assertFalse(local_serves)

    def test_local_serves_helper(self):
        """``_local_serves`` espelha os filtros da Phase 2 pro host local."""
        self.assertTrue(sf._local_serves(_w("mac", cpu=10, mem=8)))
        self.assertFalse(sf._local_serves(_w("mac", cpu=99, mem=8)))  # cpu alta
        self.assertFalse(sf._local_serves(_w("mac", cpu=10, mem=0.5)))  # ram<2G
        self.assertFalse(sf._local_serves(_w("mac", online=False)))    # offline
        self.assertFalse(sf._local_serves(_w("mac", metrics=False)))    # sem metrics
        self.assertFalse(sf._local_serves(None))                       # sem worker

    def test_auto_remote_skips_local_even_when_freest(self):
        """--host auto-remote: mesmo ranking, mas nunca cai no host que dispara."""
        self.workers[1]["metrics"] = {"cpu_pct": 1.0, "mem_avail_gb": 30.0}
        args = list(self.ARGS); args[args.index("auto")] = "auto-remote"
        out = self.run_sf(*args)
        self.assertEqual(self._post()["host_id"], "duck-id")
        self.assertIn("host auto → 🦆 Duck Server", out)

    def test_auto_remote_dies_instead_of_local_fallback(self):
        self.workers[0]["agents"] = {"claude": False}
        self.workers[1]["metrics"] = {"cpu_pct": 10.0, "mem_avail_gb": 8.0}
        args = list(self.ARGS); args[args.index("auto")] = "AUTO-REMOTE"
        out = self.run_sf_error(*args)
        self.assertIn("nenhum host remoto serve", out)
        self.assertFalse([c for c in self.calls if c[0] == "POST" and c[1].endswith("/sessions")])

    def test_auto_picks_local_when_freest(self):
        self.workers[1]["metrics"] = {"cpu_pct": 1.0, "mem_avail_gb": 30.0}
        out = self.run_sf(*self.ARGS)
        self.assertEqual(self._post()["host_id"], "mac-id")
        self.assertIn("host auto → 🍎 Macbook Air", out)

    def test_auto_remote_dir_listing_error_does_not_abort(self):
        orig = sf._request
        def failing(method, url, token=None, body=None, timeout=30):
            if "/directories?" in url:
                return 500, {"detail": "boom"}
            return orig(method, url, token=token, body=body, timeout=timeout)
        self.workers[1]["metrics"] = {"cpu_pct": 1.0, "mem_avail_gb": 8.0}
        with mock.patch.object(sf, "_request", failing):
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.run_sf(*self.ARGS)
        self.assertEqual(self._post()["host_id"], "mac-id")

    def test_auto_worktree_without_remote_errors(self):
        self.workers[0]["online"] = False
        err = self.run_sf_error(*self.ARGS, "--worktree")
        self.assertIn("nenhum host", err)

    def test_gpu_flag_alone_routes_to_gpu_host(self):
        """`--gpu` sem --host = auto restrito a GPU: Mac (mais livre, sem GPU) fica fora."""
        self.workers[0]["hardware"] = {"cpu_cores": 8, "gpu": "RTX 3060"}
        self.workers[1]["metrics"] = {"cpu_pct": 1.0, "mem_avail_gb": 30.0}
        out = self.run_sf(*[a for a in self.ARGS if a not in ("--host", "auto")], "--gpu")
        self.assertEqual(self._post()["host_id"], "duck-id")
        self.assertIn("sem GPU", out)

    def test_gpu_dies_when_no_gpu_host(self):
        self.workers[1]["metrics"] = {"cpu_pct": 1.0, "mem_avail_gb": 30.0}
        err = self.run_sf_error(*self.ARGS, "--gpu")
        self.assertIn("--gpu: nenhum host com GPU", err)
        self.assertIn("sem GPU", err)
        self.assertFalse([c for c in self.calls if c[0] == "POST" and c[1].endswith("/sessions")])

    def test_gpu_with_explicit_host_without_gpu_dies(self):
        err = self.run_sf_error("delegate", "--provider", "claude", "--host", "duck", "--dir",
                                "sessionflow", "--name", "w1", "--task", "Do work", "--gpu")
        self.assertIn("sem GPU", err)

    def test_explicit_host_unchanged(self):
        out = self.run_sf("delegate", "--provider", "claude", "--host", "duck", "--dir", "sessionflow",
                          "--name", "w1", "--task", "Do work")
        self.assertNotIn("host auto", out)


class HostsCommandTests(SfApiTestCase):
    def test_hosts_shows_load_and_agents(self):
        self.workers = [{**WORKERS[0], "capabilities": {},
                         "metrics": {"cpu_pct": 18.0, "mem_avail_gb": 0.4, "mem_total_gb": 16.0},
                         "agents": {"claude": True, "codex": False, "gemini": True}}]
        out = self.run_sf("hosts")
        self.assertIn("] 18%", out)
        self.assertIn("15.6/16.0G uso (0.4G livre)", out)
        self.assertIn("agents=claude,gemini", out)

    def test_hosts_starting_is_yellow_not_online(self):
        self.workers = [{**WORKERS[0], "online": False, "starting": True, "capabilities": {}}]
        out = self.run_sf("hosts")
        self.assertIn("🟡", out)
        self.assertIn("ligando", out)
        self.assertNotIn("🟢", out)

    def test_hosts_old_worker_without_metrics(self):
        self.workers = [{**WORKERS[0], "capabilities": {}}]
        out = self.run_sf("hosts")
        self.assertIn("host_id=duck-id", out)
        self.assertNotIn("livre)", out)
        self.assertNotIn("agents=", out)

    def test_hosts_worker_with_null_hardware(self):
        # worker antigo (instância remota) publica "hardware": null
        self.workers = [{**WORKERS[0], "capabilities": {}, "hardware": None}]
        out = self.run_sf("hosts")
        self.assertIn("host_id=duck-id", out)
        self.assertNotIn("gpu [", out)

    def test_hosts_partial_metrics_and_no_agents(self):
        self.workers = [{**WORKERS[0], "capabilities": {},
                         "metrics": {"cpu_pct": 5.0, "mem_avail_gb": 3.0, "mem_total_gb": None},
                         "agents": {"claude": False}}]
        out = self.run_sf("hosts")
        self.assertIn("(3.0G livre)", out)  # sem "/0.0G" quando total é desconhecido
        self.assertNotIn("/0.0G", out)
        self.assertNotIn("agents=", out)


class MoveCommandTests(SfApiTestCase):
    def setUp(self):
        super().setUp()
        self.session.update({"host_id": "mac-id", "agent_type": "claude",
                             "work_dir": "~/Documents/projects/sessionflow"})
        self.workers = [
            {**WORKERS[0], "metrics": {"cpu_pct": 50.0, "mem_avail_gb": 4.0}, "agents": {"claude": True}},
            {**WORKERS[1], "metrics": {"cpu_pct": 1.0, "mem_avail_gb": 30.0}, "agents": {"claude": True}},
            {"host_id": "lucas-id", "display_name": "Lucas", "hostname": "lucas", "emoji": "🐧",
             "online": True, "metrics": {"cpu_pct": 10.0, "mem_avail_gb": 8.0}, "agents": {"claude": True}},
        ]
        self.move_status, self.move_body = 202, {"command_id": "c9", "status": "accepted"}
        # Estados devolvidos em sequência pelo GET /sessions/s1 (o último se repete).
        self.polls = [
            {"moving": {"target_host_id": "duck-id", "phase": "exporting"}, "host_id": "mac-id"},
            {"host_id": "duck-id"},
        ]
        orig = sf._request

        def fake(method, url, token=None, body=None, timeout=30):
            if method == "POST" and url.endswith("/sessions/s1/move"):
                self.calls.append((method, url, body))
                return self.move_status, self.move_body
            if method == "GET" and url.endswith("/sessions/s1"):
                self.calls.append((method, url, body))
                doc = self.polls.pop(0) if len(self.polls) > 1 else self.polls[0]
                return 200, {**self.session, **doc}
            return orig(method, url, token=token, body=body, timeout=timeout)

        patcher = mock.patch.object(sf, "_request", fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _move_body(self):
        return next(b for m, u, b in self.calls if m == "POST" and u.endswith("/move"))

    def test_success_posts_target_and_reports_new_host(self):
        out = self.run_sf("move", "w1", "--host", "duck")
        self.assertEqual(self._move_body(), {"target_host_id": "duck-id"})
        self.assertIn("movida para 🦆 Duck Server", out)

    def test_failure_prints_moving_error_and_exits_1(self):
        self.polls = [{"moving": {"target_host_id": "duck-id", "phase": "failed",
                                  "error": "Há mudanças não commitadas em Macbook Air: faça commit/push antes de mover."},
                       "host_id": "mac-id"}]
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as ctx:
            self.run_sf("move", "w1", "--host", "duck")
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("mudanças não commitadas", err.getvalue())

    def test_timeout_exits_1(self):
        self.polls = [{"moving": {"target_host_id": "duck-id", "phase": "importing"}, "host_id": "mac-id"}]
        err = self.run_sf_error("move", "w1", "--host", "duck")
        self.assertIn("180s", err)
        gets = [u for m, u, b in self.calls if m == "GET" and u.endswith("/sessions/s1")]
        self.assertEqual(len(gets), sf.MOVE_TIMEOUT_SECONDS // sf.MOVE_POLL_SECONDS)

    def test_http_409_prints_detail_without_polling(self):
        self.move_status, self.move_body = 409, {"detail": "Pasta sessionflow não encontrada em Duck Server."}
        err = self.run_sf_error("move", "w1", "--host", "duck")
        self.assertIn("Pasta sessionflow não encontrada em Duck Server.", err)
        self.assertFalse([u for m, u, b in self.calls if m == "GET" and u.endswith("/sessions/s1")])

    def test_auto_excludes_current_host(self):
        # mac-id é o mais livre, mas é onde a sessão já está → escolhe lucas.
        self.polls = [{"host_id": "lucas-id"}]
        out = self.run_sf("move", "w1", "--host", "auto")
        self.assertEqual(self._move_body(), {"target_host_id": "lucas-id"})
        self.assertIn("host auto → 🐧 Lucas", out)
        self.assertIn("movida para 🐧 Lucas", out)

    def test_auto_skips_host_without_folder(self):
        orig = sf._request
        def dirs_only_on_duck(method, url, token=None, body=None, timeout=30):
            if "/directories?" in url and "host_id=lucas-id" in url:
                return 200, {"items": []}
            return orig(method, url, token=token, body=body, timeout=timeout)
        with mock.patch.object(sf, "_request", dirs_only_on_duck):
            self.run_sf("move", "w1", "--host", "auto")
        self.assertEqual(self._move_body(), {"target_host_id": "duck-id"})

    def test_auto_without_candidate_dies(self):
        for w in self.workers:
            if w["host_id"] != "mac-id":
                w["online"] = False
        err = self.run_sf_error("move", "w1", "--host", "auto")
        self.assertIn("nenhum host", err)
        self.assertFalse([b for m, u, b in self.calls if u.endswith("/move")])

    def test_unknown_session_dies(self):
        self.assertIn("nenhuma sessão", self.run_sf_error("move", "nope", "--host", "duck"))


class NotifyCommandTests(SfApiTestCase):
    def setUp(self):
        super().setUp()
        self.notify_status, self.notify_body = 200, {"sent": 2}
        orig = sf._request

        def fake(method, url, token=None, body=None, timeout=30):
            if url.endswith("/push/notify"):
                self.calls.append((method, url, body, token))
                return self.notify_status, self.notify_body
            return orig(method, url, token=token, body=body, timeout=timeout)

        patcher = mock.patch.object(sf, "_request", fake)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_posts_title_body_url_with_token(self):
        out = self.run_sf("notify", "🤖 Colab precisa de você", "colab1: captcha",
                          "--url", "https://colab/x")
        self.assertEqual(self.calls, [("POST", "http://api/push/notify",
                                       {"title": "🤖 Colab precisa de você",
                                        "body": "colab1: captcha",
                                        "url": "https://colab/x"}, "tok")])
        self.assertIn("2 dispositivo(s)", out)

    def test_without_url_omits_field(self):
        self.run_sf("notify", "t", "b")
        self.assertEqual(self.calls[0][2], {"title": "t", "body": "b"})

    def test_http_error_dies(self):
        self.notify_status, self.notify_body = 503, {"detail": "sem chave"}
        self.assertIn("HTTP 503", self.run_sf_error("notify", "t", "b"))


class GpuCommandTests(SfApiTestCase):
    """`sf gpu` — liga/desliga/consulta os serviços de GPU de um host."""

    def setUp(self):
        super().setUp()
        self.workers = [{**WORKERS[0], "gpu_services": {
            "xtts": {"active": True, "unit": "xtts.service", "port": 5111,
                     "last_used_at": "2026-10-26T12:00:00+00:00"},
        }}]
        self.gpu_result = {
            "action": "start", "name": "xtts", "unit": "xtts.service", "active": True,
        }

        def fake_request(method, url, token=None, body=None, timeout=30):
            self.calls.append((method, url, body))
            if url.endswith("/workers"):
                return 200, self.workers
            if "/gpu-services/" in url:
                return 202, {"command_id": "g1", "status": "queued", "host_id": "duck-id"}
            if url.endswith("/commands/g1"):
                return 200, {"id": "g1", "status": "ok", "type": "gpu_service",
                             "result": self.gpu_result}
            raise AssertionError(f"rota inesperada: {method} {url}")

        patcher = mock.patch.object(sf, "_request", fake_request)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_gpu_status_lista_do_heartbeat(self):
        out = self.run_sf("gpu", "duck", "status")
        self.assertIn("xtts", out)
        self.assertIn("active=True", out)
        self.assertIn("xtts.service", out)
        # status sem nome NÃO publica comando (lê o heartbeat)
        self.assertFalse([u for m, u, b in self.calls if "/gpu-services/" in u])

    def test_gpu_status_de_um_servico_vai_como_comando(self):
        out = self.run_sf("gpu", "duck", "status", "xtts")
        self.assertIn("xtts", out)
        url = next(u for m, u, b in self.calls if "/gpu-services/" in u)
        self.assertTrue(url.endswith("/gpu-services/xtts/status"))

    def test_gpu_start_publica_comando(self):
        out = self.run_sf("gpu", "duck", "start", "xtts")
        self.assertIn("xtts", out)
        url = next(u for m, u, b in self.calls if "/gpu-services/" in u)
        self.assertTrue(url.endswith("/gpu-services/xtts/start"))

    def test_gpu_stop_publica_comando(self):
        self.run_sf("gpu", "duck", "stop", "xtts")
        url = next(u for m, u, b in self.calls if "/gpu-services/" in u)
        self.assertTrue(url.endswith("/gpu-services/xtts/stop"))

    def test_gpu_start_sem_nome_dies(self):
        err = self.run_sf_error("gpu", "duck", "start")
        self.assertIn("nome do serviço", err)

    def test_gpu_sem_servicos_configurados(self):
        self.workers = [{**WORKERS[0], "gpu_services": {}}]
        out = self.run_sf("gpu", "duck", "status")
        self.assertIn("nenhum serviço de GPU", out)


class CommandHelpersTests(unittest.TestCase):
    """Helpers do verbo `sf command` (isolamento de output por marcadores)."""

    def test_extract_marked_isolates_output_and_rc(self):
        screen = ("ruido\nSF_BEGIN_ab12cd34\nlinha1\nlinha2\n"
                  "SF_END_ab12cd34 rc=3\nmais ruido")
        lines, rc, done = sf._extract_marked(screen, "SF_BEGIN_ab12cd34", "SF_END_ab12cd34")
        self.assertTrue(done)
        self.assertEqual(lines, ["linha1", "linha2"])
        self.assertEqual(rc, 3)

    def test_extract_marked_without_end_marker_is_not_done(self):
        lines, rc, done = sf._extract_marked("SF_BEGIN_x\nsaida", "SF_BEGIN_x", "SF_END_x")
        self.assertFalse(done)
        self.assertIsNone(rc)
        self.assertEqual(lines, [])

    def test_extract_marked_uses_last_occurrence(self):
        screen = ("SF_BEGIN_x\nantigo\nSF_END_x rc=0\n"
                  "SF_BEGIN_x\nnovo\nSF_END_x rc=2")
        lines, rc, done = sf._extract_marked(screen, "SF_BEGIN_x", "SF_END_x")
        self.assertTrue(done)
        self.assertEqual(lines, ["novo"])
        self.assertEqual(rc, 2)

    def test_extract_marked_ignores_ansi_before_matching(self):
        screen = "\x1b[32mSF_BEGIN_x\x1b[0m\nok\n\x1b[32mSF_END_x rc=0\x1b[0m"
        lines, rc, done = sf._extract_marked(screen, "SF_BEGIN_x", "SF_END_x")
        self.assertTrue(done)
        self.assertEqual(lines, ["ok"])
        self.assertEqual(rc, 0)

    def test_extract_marked_rc_can_be_missing(self):
        screen = "SF_BEGIN_x\nok\nSF_END_x"
        lines, rc, done = sf._extract_marked(screen, "SF_BEGIN_x", "SF_END_x")
        self.assertTrue(done)
        self.assertIsNone(rc)

    def test_exact_session_matches_only_exact(self):
        sessions = [{"id": "s1", "tmux_name": "cmd-duck-server",
                     "display_name": "cmd-duck-server"}]
        # Substring NÃO casa: injetar num work errado é pior que falhar.
        self.assertIsNone(sf._exact_session(sessions, "cmd-duck"))
        self.assertIsNone(sf._exact_session(sessions, "duck"))
        self.assertIsNotNone(sf._exact_session(sessions, "cmd-duck-server"))
        self.assertIsNotNone(sf._exact_session(sessions, "s1"))

    def test_strip_ansi_removes_escape_sequences(self):
        self.assertEqual(sf._strip_ansi("\x1b[1;31mvermelho\x1b[0m"), "vermelho")
        self.assertEqual(sf._strip_ansi("plain"), "plain")


class CommandVerbTests(SfApiTestCase):
    """`sf command`: roda comando num shell de OUTRO host, sem agente LLM."""

    def setUp(self):
        super().setUp()
        self.sessions = []
        self.screen = ""
        self.remote_output = "arquivo-a\narquivo-b"
        self.remote_rc = 0
        self.inputs = []
        self.emit_markers = True
        self.create_status = 202
        self.on_conflict = None

        def fake(method, url, token=None, body=None, timeout=30):
            self.calls.append((method, url, body))
            if url.endswith("/workers"):
                return 200, self.workers
            if method == "GET" and url.endswith("/sessions"):
                return 200, {"items": list(self.sessions)}
            if method == "POST" and url.endswith("/sessions"):
                if self.create_status == 409:
                    if self.on_conflict:
                        self.on_conflict()
                    return 409, {"detail": {"code": "command_bridge_exists"}}
                self.sessions.append({
                    "id": "cmd-1", "tmux_name": body["name"],
                    "display_name": body["name"], "status": "running",
                    "agent_type": body.get("agent_type"),
                    "host_id": body.get("host_id"),
                    "work_dir": body.get("work_dir") or "/home/diego",
                })
                return 202, {"command_id": "c1"}
            if method == "PUT" and url.endswith("/bridge"):
                return 200, {}
            if method == "POST" and url.endswith("/input"):
                self.inputs.append(body)
                if self.emit_markers:
                    # Simula o shell remoto: ecoa o comando e emite os marcadores.
                    m = re.search(r"SF_BEGIN_([0-9a-f]+)", body.get("text", ""))
                    nonce = m.group(1) if m else "deadbeef"
                    self.screen = "\n".join([
                        "$ " + body.get("text", "").strip(),
                        f"SF_BEGIN_{nonce}",
                        self.remote_output,
                        f"SF_END_{nonce} rc={self.remote_rc}",
                    ])
                else:
                    self.screen = "comando rodando, sem marcador ainda"
                return 202, {}
            if method == "GET" and url.endswith("/screen"):
                return 200, {"text": self.screen, "scrollback": self.screen}
            raise AssertionError(f"rota inesperada: {method} {url}")

        for attr, value in (
            ("_request", fake),
            ("list_sessions", lambda env, token: list(self.sessions)),
        ):
            patcher = mock.patch.object(sf, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_cmd(self, *argv):
        """`sf command` termina com sys.exit(rc) — devolve (saída, rc)."""
        args = sf.build_parser().parse_args(list(argv))
        out = io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out):
            try:
                args.func(args)
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
        return out.getvalue(), code

    def _create_body(self):
        return next(b for m, u, b in self.calls if m == "POST" and u.endswith("/sessions"))

    def test_creates_command_session_without_model_or_effort(self):
        self.run_cmd("command", "duck", "ls ~/Documents")
        body = self._create_body()
        self.assertEqual(body["agent_type"], "command")
        self.assertEqual(body["host_id"], "duck-id")
        self.assertEqual(body["name"], "cmd-duck-server")
        self.assertNotIn("model", body)
        self.assertNotIn("effort", body)

    def test_wraps_command_with_markers_and_prints_output(self):
        out, code = self.run_cmd("command", "duck", "ls ~/Documents")
        text = self.inputs[0]["text"]
        self.assertIn("ls ~/Documents", text)
        self.assertRegex(text, r"printf 'SF_BEGIN_[0-9a-f]+")
        self.assertRegex(text, r"printf 'SF_END_[0-9a-f]+ rc=%s")
        self.assertTrue(self.inputs[0]["enter"])
        self.assertIn("arquivo-a", out)
        self.assertIn("arquivo-b", out)
        self.assertEqual(code, 0)

    def test_propagates_remote_exit_code(self):
        self.remote_rc = 7
        _, code = self.run_cmd("command", "duck", "false")
        self.assertEqual(code, 7)

    def test_reuses_existing_session_without_creating(self):
        self.sessions.append({"id": "cmd-1", "tmux_name": "cmd-duck-server",
                              "display_name": "cmd-duck-server", "status": "running",
                              "agent_type": "command", "host_id": "duck-id"})
        self.run_cmd("command", "duck", "echo hi")
        self.assertFalse(any(m == "POST" and u.endswith("/sessions")
                             for m, u, b in self.calls))

    def test_named_session_uses_given_name(self):
        self.run_cmd("command", "duck", "echo hi", "--name", "lab-mac")
        self.assertEqual(self._create_body()["name"], "lab-mac")

    def test_dir_becomes_work_dir(self):
        self.run_cmd("command", "duck", "ls", "--dir", "/tmp")
        self.assertEqual(self._create_body()["work_dir"], "/tmp")

    def test_no_wait_skips_screen_polling(self):
        out, _ = self.run_cmd("command", "duck", "echo hi", "--no-wait")
        self.assertIn("sem esperar", out)
        self.assertFalse(any(u.endswith("/screen") for m, u, b in self.calls))

    def test_tail_prints_screen_without_running_command(self):
        self.sessions.append({"id": "cmd-1", "tmux_name": "cmd-duck-server",
                              "display_name": "cmd-duck-server", "status": "running",
                              "agent_type": "command", "host_id": "duck-id"})
        self.screen = "linha1\nlinha2"
        out, _ = self.run_cmd("command", "duck", "--tail")
        self.assertIn("linha2", out)
        self.assertEqual(self.inputs, [])

    def test_without_command_only_reports_session(self):
        out, _ = self.run_cmd("command", "duck")
        self.assertIn("sessão de comando", out)
        self.assertIn("cmd-duck-server", out)
        self.assertEqual(self.inputs, [])

    def test_timeout_exits_2_and_says_command_not_cancelled(self):
        self.emit_markers = False
        err = io.StringIO()
        code = 0
        with contextlib.redirect_stderr(err):
            _, code = self.run_cmd("command", "duck", "sleep 999", "--timeout", "0.01")
        self.assertEqual(code, 2)
        self.assertIn("NÃO foi cancelado", err.getvalue())

    def test_unknown_host_dies_before_creating(self):
        err = self.run_sf_error("command", "nope", "ls")
        self.assertIn("nope", err)
        self.assertEqual(self.sessions, [])

    def test_wraps_command_in_subshell_so_exit_does_not_kill_pane(self):
        """`exit 7` NÃO pode matar o shell do pane.

        Sem sub-shell, o `exit` do comando encerra o shell antes do marcador
        END: a sessão morre, o CLI espera o timeout e perde o rc.
        """
        self.run_cmd("command", "duck", "exit 7")
        text = self.inputs[0]["text"]
        self.assertIn("( exit 7", text)
        self.assertIn("__sf_rc=$?", text)
        # O rc do comando é lido DEPOIS do sub-shell (que é quem roda o exit).
        self.assertLess(text.index("__sf_rc=$?"), text.index("SF_END_"))
        self.assertIn("\n )\n", text.replace("\r\n", "\n"))

    def test_captures_rc_inside_subshell_before_cwd_bookkeeping(self):
        """O `pwd` de bookkeeping NÃO pode sobrescrever o rc do comando.

        Sem isso, `ls /nao-existe` (rc=2) voltaria com rc=0.
        """
        self.run_cmd("command", "duck", "ls /nao-existe")
        text = self.inputs[0]["text"]
        self.assertLess(text.index("__sf_inner=$?"), text.index("pwd >"))
        self.assertLess(text.index("pwd >"), text.index("exit $__sf_inner"))

    def test_preserves_cwd_of_named_session(self):
        """O `pwd` final volta pro shell pai — `cd repo` persiste na sessão."""
        self.run_cmd("command", "duck", "cd /tmp", "--name", "lab-mac")
        text = self.inputs[0]["text"]
        self.assertIn("pwd >", text)
        self.assertIn("cd \"$(cat", text)

    def test_recreates_session_when_previous_pane_is_dead(self):
        """Sessão parada (pane morto) não é reusada — cria outra com o mesmo nome."""
        self.sessions.append({"id": "cmd-dead", "tmux_name": "cmd-duck-server",
                              "display_name": "cmd-duck-server", "status": "stopped",
                              "agent_type": "command", "host_id": "duck-id"})
        self.run_cmd("command", "duck", "echo hi")
        self.assertTrue(any(m == "POST" and u.endswith("/sessions")
                            for m, u, b in self.calls))
        self.assertEqual(self._create_body()["name"], "cmd-duck-server")
        self.assertEqual(self._create_body()["agent_type"], "command")

    def test_reuses_live_session_without_creating(self):
        self.sessions.append({"id": "cmd-live", "tmux_name": "cmd-duck-server",
                              "display_name": "cmd-duck-server", "status": "running",
                              "agent_type": "command", "host_id": "duck-id"})
        self.run_cmd("command", "duck", "echo hi")
        self.assertFalse(any(m == "POST" and u.endswith("/sessions")
                             for m, u, b in self.calls))

    def test_new_bridge_avoids_live_pane_with_same_name(self):
        """Pane VIVO com o mesmo nome → a ponte nova ganha sufixo.

        O tmux não aceita dois panes com o mesmo nome — sem o sufixo, o
        `sf command` morreria com "sessão tmux já existe" quando existe uma
        ponte de SERVIÇO (keep_alive) com o nome default.
        """
        self.sessions.append({"id": "svc", "tmux_name": "cmd-duck-server",
                              "display_name": "cmd-duck-server", "status": "running",
                              "agent_type": "command", "host_id": "duck-id",
                              "keep_alive": True})
        self.run_cmd("command", "duck", "echo hi")
        self.assertEqual(self._create_body()["name"], "cmd-duck-server-2")

    def test_reuses_bridge_when_create_conflicts(self):
        """Corrida: a API recusa a 2ª ponte do host → o CLI REAPROVEITA a que existe."""
        def spawn_bridge():
            self.sessions.append({
                "id": "cmd-race", "tmux_name": "cmd-duck-server",
                "display_name": "cmd-duck-server", "status": "running",
                "agent_type": "command", "host_id": "duck-id",
            })

        self.create_status = 409
        self.on_conflict = spawn_bridge
        out, code = self.run_cmd("command", "duck", "echo hi")
        self.assertEqual(code, 0)
        self.assertTrue(any(u.endswith("/sessions/cmd-race/input")
                            for m, u, b in self.calls))
        self.assertEqual(len([c for c in self.calls
                              if c[0] == "POST" and c[1].endswith("/sessions")]), 1)

    def test_bridge_put_always_sends_owner(self):
        """O PUT da ponte SEMPRE manda a dona — None limpa a dona antiga.

        A ponte pode ser recriada com o mesmo nome; sem o owner explícito a
        dona morta da vida velha continuaria no doc e mataria a ponte nova em
        cascata.
        """
        self.run_cmd("command", "duck", "echo hi")
        bridge = next(b for m, u, b in self.calls if u.endswith("/bridge"))
        self.assertIn("owner", bridge)
        self.assertIsNone(bridge["owner"])

    # --- ponte de comando: a identidade é o HOST, não o nome ---

    def test_reuses_bridge_of_same_host_even_with_other_name(self):
        """Ponte viva do MESMO host é reusada mesmo com outro nome.

        É a regra que mata a pilha de shells: o vínculo é o host, o nome é
        só rótulo (e vira apelido pra `sf send`).
        """
        self.sessions.append({"id": "cmd-live", "tmux_name": "cmd-duck-server",
                              "display_name": "cmd-duck-server", "status": "running",
                              "agent_type": "command", "host_id": "duck-id"})
        self.run_cmd("command", "duck", "echo hi", "--name", "lab-duck")
        self.assertFalse(any(m == "POST" and u.endswith("/sessions")
                             for m, u, b in self.calls))
        bridge = next(b for m, u, b in self.calls if u.endswith("/bridge"))
        self.assertEqual(bridge["alias"], "lab-duck")

    def test_does_not_reuse_bridge_of_another_host(self):
        self.sessions.append({"id": "cmd-mac", "tmux_name": "cmd-mac",
                              "display_name": "cmd-mac", "status": "running",
                              "agent_type": "command", "host_id": "mac-id"})
        self.run_cmd("command", "duck", "echo hi")
        self.assertTrue(any(m == "POST" and u.endswith("/sessions")
                            for m, u, b in self.calls))

    def test_keep_alive_session_is_not_used_as_bridge(self):
        """Serviço keep_alive NÃO é ponte: injetar comando derrubaria o serviço."""
        self.sessions.append({"id": "cmd-svc", "tmux_name": "tmux-sf-phone-relay",
                              "display_name": "tmux-sf-phone-relay", "status": "running",
                              "agent_type": "command", "host_id": "duck-id",
                              "keep_alive": True})
        self.run_cmd("command", "duck", "echo hi")
        self.assertTrue(any(m == "POST" and u.endswith("/sessions")
                            for m, u, b in self.calls))

    def test_registers_owner_of_the_bridge(self):
        # A dona precisa ser uma sessão REAL do SessionFlow: um tmux solto
        # (fora do app) não seria "viva" pra API e mataria a ponte no tick.
        self.sessions.append({"id": "s-pai", "tmux_name": "pai",
                              "status": "running", "agent_type": "claude"})
        self.run_cmd("command", "duck", "echo hi")
        bridge = next(b for m, u, b in self.calls if u.endswith("/bridge"))
        self.assertEqual(bridge["owner"], "pai")  # detect_parent() patchado

    def test_unknown_parent_is_not_registered_as_owner(self):
        self.run_cmd("command", "duck", "echo hi")
        bridge = next(b for m, u, b in self.calls if u.endswith("/bridge"))
        self.assertIsNone(bridge.get("owner"))

    def test_keep_alive_flag_marks_the_bridge(self):
        self.run_cmd("command", "duck", "echo hi", "--keep-alive")
        bridge = next(b for m, u, b in self.calls if u.endswith("/bridge"))
        self.assertTrue(bridge["keep_alive"])

    def test_resolves_bridge_by_alias(self):
        """`sf send lab-duck` acha a ponte pelo apelido."""
        sessions = [{"id": "s1", "tmux_name": "cmd-duck-server",
                     "display_name": "cmd-duck-server", "aliases": ["lab-duck"]}]
        self.assertEqual(sf.resolve_session(sessions, "lab-duck")["id"], "s1")

    # --- ordem dos argumentos: o texto do comando pode vir antes OU depois
    # --- das opções. Sem o pré-parse, o argparse rouba o valor da opção.

    def run_main(self, *argv):
        """Roda pelo `main()` (como o CLI de verdade) — devolve (saída, rc)."""
        out = io.StringIO()
        code = 0
        with contextlib.redirect_stdout(out):
            try:
                sf.main(list(argv))
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
        return out.getvalue(), code

    def test_command_text_after_options_is_not_swallowed_by_option_value(self):
        """`sf command duck --name lab "ls"` — o texto NÃO pode virar o
        valor da opção (bug do argparse com positional nargs='?')."""
        _, code = self.run_main("command", "duck", "--name", "lab-duck", "echo hi")
        self.assertEqual(code, 0)
        self.assertIn("echo hi", self.inputs[0]["text"])
        self.assertEqual(self.sessions[0]["tmux_name"], "lab-duck")

    def test_command_text_before_options_still_works(self):
        _, code = self.run_main("command", "duck", "echo hi", "--name", "lab-duck")
        self.assertEqual(code, 0)
        self.assertIn("echo hi", self.inputs[0]["text"])
        self.assertEqual(self.sessions[0]["tmux_name"], "lab-duck")

    def test_options_with_values_are_known_to_the_preparser(self):
        """Trava anti-drift: opção nova com valor precisa entrar na lista."""
        parser = sf.build_parser()
        sub = None
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                sub = action.choices.get("command")
                break
        self.assertIsNotNone(sub)
        from_parser = set()
        for action in sub._actions:
            if not action.option_strings or action.nargs == 0:
                continue
            from_parser.update(action.option_strings)
        self.assertEqual(from_parser, set(sf._CMD_VALUE_OPTS))


if __name__ == "__main__":
    unittest.main()
