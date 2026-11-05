"""Testes do servidor MCP do SessionFlow (``tools/mcp_server.py``).

Mesmo padrão de ``test_sf.py``: o CLI ``sf`` (sem extensão) é carregado como
módulo pelo ``SourceFileLoader`` — assim o MCP reusa os helpers já testados
(``load_env``/``login``/``_request``/``resolve_host``) em vez de duplicar HTTP.

Nada sai pra rede: ``_request`` é substituído por um fake em memória.
"""

import asyncio
import contextlib
import importlib.machinery
import importlib.util
import io
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock
import unittest

HERE = Path(__file__).parent


def _load(name, filename):
    """Carrega um script sem extensão como módulo, registrando em sys.modules.

    O registro importa: ``mcp_server`` reusa a instância de ``sf`` já carregada
    (estado de módulo compartilhado), então os mocks aplicados aqui precisam
    ser os mesmos que o servidor enxerga.
    """
    existing = sys.modules.get(name)
    if existing is not None and hasattr(existing, "__file__"):
        return existing
    loader = importlib.machinery.SourceFileLoader(name, str(HERE / filename))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    loader.exec_module(module)
    return module


sf = _load("sf", "sf")


class ServerTestCase(unittest.TestCase):
    """Base: ``sf`` já carregado, rede bloqueada, saída de terminal capturada."""

    def setUp(self):
        self.mcp = _load("sessionflow_mcp_server", "mcp_server.py")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmpdir = tmp.name
        cwd = os.getcwd()
        os.chdir(self.tmpdir)
        self.addCleanup(os.chdir, cwd)

        self.calls = []
        # Resultado devolvido pelo polling de GET /commands/{id}; cada teste de
        # arquivo ajusta pro que a operação dele produz.
        self.command_error = None
        self.command_result = {
            "path": "/tmp/x", "text": "conteudo", "returned_bytes": 8,
            "size_bytes": 8, "binary": False, "truncated": False,
            "entries": [{"name": "a.txt", "type": "file", "size": 3}],
            "total": 1, "bytes_written": 2, "created": True,
        }
        self.sessions = [
            {
                "id": "s1",
                "tmux_name": "w1",
                "display_name": "Worker Um",
                "status": "running",
                "activity": "thinking",
                "agent_type": "claude",
                "host_id": "mac-id",
                "work_dir": "/tmp/w1",
            }
        ]
        # Forma REAL do GET /workers: `agents` é dict {provider: bool}.
        self.workers = [
            {
                "host_id": "mac-id",
                "hostname": "mac.local",
                "display_name": "Mac",
                "emoji": "🍎",
                "online": True,
                "starting": False,
                "agents": {"claude": True, "gemini": False},
                "capabilities": {"fetch": True},
                "hardware": {"gpu": False, "cpu_cores": 10},
                "metrics": {"cpu_pct": 10.0, "mem_avail_gb": 8.0, "mem_total_gb": 16.0},
                "directories": [],
            }
        ]

        def fake_request(method, url, token=None, body=None, timeout=30):
            self.calls.append((method, url, body))
            if url.endswith("/auth/login"):
                return 200, {"token": "tok"}
            if url.endswith("/workers"):
                return 200, self.workers
            if "/directories?" in url:
                return 200, {"items": []}
            if "/sessions?" in url or url.endswith("/sessions"):
                if method == "GET":
                    wanted = None
                    if "status=" in url:
                        wanted = url.split("status=", 1)[1].split("&")[0]
                    items = [s for s in self.sessions
                             if wanted is None or s.get("status") == wanted]
                    return 200, {"items": items}
                return 202, {"command_id": "c1"}
            if url.endswith("/input"):
                return 202, {}
            if url.endswith("/fs"):
                return 202, {"command_id": "cmd-1", "host_id": "mac-id", "work_dir": "/tmp/w1"}
            if "/commands/" in url:
                if self.command_error:
                    return 200, {"status": "error", "error": self.command_error}
                return 200, {"status": "ok", "result": self.command_result}
            if "/push/notify" in url:
                return 200, {"sent": 2}
            return 200, {"ok": True}

        for attr, value in {
            "_request": fake_request,
            "load_env": lambda path=None: {
                "SESSIONFLOW_EMAIL": "a@b.c",
                "SESSIONFLOW_PASSWORD": "pw",
                "API_HOST_PORT": "8000",
            },
            "api_base": lambda env: "http://api",
            "local_host_id": lambda path=None: "mac-id",
            "detect_parent": lambda: None,
            "wait_running": lambda env, token, name: self.sessions[0],
        }.items():
            patcher = mock.patch.object(sf, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    # -- helpers ----------------------------------------------------------- #
    def call(self, name, arguments=None):
        """Invoca uma tool como o cliente MCP faria; devolve o dict de resultado.

        O SDK 2.x entrega o retorno no ``content`` como texto JSON (``dict`` não
        é serializável em ``structuredContent``), então o cliente — igual um
        agente — parseia o texto.
        """
        result = asyncio.run(self.mcp.server.call_tool(name, arguments or {}))
        texts = [c.text for c in result.content if getattr(c, "text", None)]
        payload = "\n".join(texts)
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return {"_text": payload}

    def call_error(self, name, arguments=None):
        """Espera que a tool vire erro RECUPERÁVEL (ToolError), não SystemExit."""
        try:
            self.call(name, arguments)
        except Exception as exc:  # noqa: BLE001 - queremos o tipo real
            return exc
        self.fail(f"tool {name} deveria ter falhado")

    def tool_names(self):
        return {t.name for t in asyncio.run(self.mcp.server.list_tools())}


class ToolSurfaceTests(ServerTestCase):
    """O catálogo espelha os VERBOS do CLI, não os 87 endpoints."""

    def test_exposes_a_tool_for_every_core_cli_verb(self):
        expected = {
            "list_sessions",
            "list_hosts",
            "delegate",
            "check",
            "send",
            "fetch_file",
            "share_file",
            "run_command",
            "move_session",
            "start_host",
            "stop_host",
            "gpu_service",
            "notify",
            "list_dirs",
            "request_context",
            "metrics",
            "api_call",
            "fs",
            "copy_between_sessions",
        }
        self.assertEqual(self.tool_names(), expected)

    def test_catalog_stays_small_enough_for_context(self):
        # Cada schema entra no system prompt de TODO turno: acima disso o custo
        # fixo supera o ganho de ter a tool.
        self.assertLessEqual(len(self.tool_names()), 20)


class ListSessionsTests(ServerTestCase):
    def test_returns_structured_sessions_not_terminal_text(self):
        out = self.call("list_sessions")
        self.assertEqual(len(out["sessions"]), 1)
        session = out["sessions"][0]
        self.assertEqual(session["id"], "s1")
        self.assertEqual(session["name"], "w1")
        self.assertEqual(session["status"], "running")
        self.assertEqual(session["host_id"], "mac-id")

    def test_active_only_filters_to_running_and_waiting_input(self):
        self.sessions = [
            {"id": "s1", "tmux_name": "viva", "status": "running", "host_id": "mac-id"},
            {"id": "s2", "tmux_name": "esperando", "status": "waiting_input", "host_id": "mac-id"},
            {"id": "s3", "tmux_name": "morta", "status": "completed", "host_id": "mac-id"},
            {"id": "s4", "tmux_name": "solta", "status": "detached", "host_id": "mac-id"},
        ]
        out = self.call("list_sessions", {"active_only": True})
        self.assertEqual({s["name"] for s in out["sessions"]}, {"viva", "esperando"})

    def test_filter_is_pushed_to_the_api_not_done_locally(self):
        # O historico tem centenas de sessoes; filtrar aqui nao economiza I/O
        # nem payload — o filtro tem que ir na query.
        self.call("list_sessions", {"active_only": True})
        urls = [c[1] for c in self.calls if c[1].endswith("/sessions") or "/sessions?" in c[1]]
        self.assertTrue(any("status=running" in u for u in urls), urls)

    def test_limit_caps_what_reaches_the_agent(self):
        self.sessions = [
            {"id": f"s{i}", "tmux_name": f"w{i}", "status": "running", "host_id": "mac-id"}
            for i in range(30)
        ]
        out = self.call("list_sessions", {"limit": 5})
        self.assertEqual(len(out["sessions"]), 5)
        self.assertEqual(out["total"], 30)
        self.assertTrue(out["truncated"])


class ListHostsTests(ServerTestCase):
    def test_returns_host_state_with_agent_map(self):
        out = self.call("list_hosts")
        self.assertEqual(len(out["hosts"]), 1)
        host = out["hosts"][0]
        self.assertEqual(host["host_id"], "mac-id")
        self.assertTrue(host["online"])
        # `agents` chega como dict do worker; a tool achata nos disponíveis.
        self.assertEqual(host["agents"], ["claude"])


class SendTests(ServerTestCase):
    def test_sends_text_to_resolved_session(self):
        out = self.call("send", {"target": "w1", "text": "rode os testes"})
        self.assertEqual(out["session_id"], "s1")
        self.assertTrue(out["delivered"])
        sent = [c for c in self.calls if "/input" in c[1]]
        self.assertEqual(len(sent), 1)
        self.assertEqual(sent[0][2]["text"], "rode os testes")
        self.assertTrue(sent[0][2]["enter"])

    def test_no_enter_flag_is_forwarded(self):
        self.call("send", {"target": "w1", "text": "rascunho", "enter": False})
        method, url, body = [c for c in self.calls if "/input" in c[1]][0]
        self.assertFalse(body["enter"])

    def test_unknown_target_raises_tool_error_instead_of_exiting(self):
        # sf.die() faz sys.exit(1) — dentro de um servidor MCP isso mataria o
        # processo inteiro. A tool tem que virar erro recuperável.
        exc = self.call_error("send", {"target": "nao-existe", "text": "oi"})
        self.assertIn("ToolError", type(exc).__name__)
        self.assertIn("nao-existe", str(exc))


class DelegateTests(ServerTestCase):
    def test_delegates_and_returns_worker_identity(self):
        out = self.call(
            "delegate",
            {"provider": "claude", "task": "Refatore o módulo X", "dir": self.tmpdir},
        )
        self.assertEqual(out["session_id"], "s1")
        self.assertTrue(out["handle"])
        created = [c for c in self.calls if c[0] == "POST" and c[1].endswith("/sessions")]
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0][2]["agent_type"], "claude")
        self.assertEqual(created[0][2]["work_dir"], self.tmpdir)

    def test_invalid_provider_raises_tool_error(self):
        exc = self.call_error(
            "delegate", {"provider": "inexistente", "task": "x", "dir": self.tmpdir}
        )
        self.assertIn("ToolError", type(exc).__name__)


class CheckTests(ServerTestCase):
    def test_reports_status_even_without_handoff(self):
        out = self.call("check", {"target": "w1", "dir": self.tmpdir})
        self.assertEqual(out["status"], "running")
        self.assertIn("w1", out["output"])


class NotifyTests(ServerTestCase):
    def test_pushes_and_reports_devices(self):
        out = self.call("notify", {"title": "precisa de voce", "body": "worker travou"})
        self.assertEqual(out["sent"], 2)


class ApiCallTests(ServerTestCase):
    def test_escape_hatch_hits_arbitrary_route_with_auth(self):
        out = self.call("api_call", {"method": "GET", "path": "/skills"})
        self.assertEqual(out["status"], 200)
        called = self.calls[-1]
        self.assertEqual(called[1], "http://api/skills")
        self.assertEqual(called[0], "GET")

    def test_rejects_absolute_urls_to_keep_target_pinned(self):
        exc = self.call_error("api_call", {"method": "GET", "path": "http://evil/x"})
        self.assertIn("ToolError", type(exc).__name__)


class FsTests(ServerTestCase):
    """Operações de arquivo endereçadas por SESSÃO (não por host)."""

    def _enqueue_result(self, **result):
        self.command_result = result

    def test_list_files_resolves_session_and_polls_command(self):
        out = self.call("fs", {"session": "w1", "op": "list", "path": "."})
        posted = [c for c in self.calls if c[0] == "POST" and "/fs" in c[1]]
        self.assertEqual(len(posted), 1)
        self.assertEqual(posted[0][2]["op"], "list")
        self.assertEqual(posted[0][2]["path"], ".")
        self.assertIn("entries", out)

    def test_read_file_returns_content(self):
        out = self.call("fs", {"session": "w1", "op": "read", "path": "a.txt"})
        posted = [c for c in self.calls if c[0] == "POST" and "/fs" in c[1]][0]
        self.assertEqual(posted[2]["op"], "read")
        self.assertEqual(out["text"], "conteudo")
        self.assertFalse(out["binary"])

    def test_write_file_sends_content(self):
        self.call(
            "fs", {"session": "w1", "op": "write", "path": "a.txt", "content": "oi"}
        )
        posted = [c for c in self.calls if c[0] == "POST" and "/fs" in c[1]][0]
        self.assertEqual(posted[2]["op"], "write")
        self.assertEqual(posted[2]["content"], "oi")

    def test_copy_between_sessions_reads_then_writes(self):
        # Transferência entre sessões = read na origem + write no destino.
        self.sessions = [
            {"id": "s1", "tmux_name": "w1", "status": "running", "host_id": "mac-id",
             "work_dir": "/tmp/w1"},
            {"id": "s2", "tmux_name": "w2", "status": "running", "host_id": "duck-id",
             "work_dir": "/tmp/w2"},
        ]
        out = self.call(
            "copy_between_sessions",
            {"source_session": "w1", "source_path": "a.txt",
             "target_session": "w2", "target_path": "b.txt"},
        )
        posts = [c for c in self.calls if c[0] == "POST" and "/fs" in c[1]]
        ops = [p[2]["op"] for p in posts]
        self.assertEqual(ops, ["read", "write"])
        # Origem e destino são sessões DIFERENTES (hosts diferentes inclusive).
        self.assertIn("/sessions/s1/fs", posts[0][1])
        self.assertIn("/sessions/s2/fs", posts[1][1])
        self.assertTrue(out["copied"])

    def test_delete_surfaces_worker_error_as_tool_error(self):
        # O worker recusa dir não-vazio sem recursive; a tool tem que propagar
        # como erro recuperável, não engolir.
        self.command_error = "diretório não vazio (use recursive=true): /tmp/w1/d"
        exc = self.call_error(
            "fs", {"session": "w1", "op": "delete", "path": "d", "recursive": False}
        )
        self.assertIn("ToolError", type(exc).__name__)
        self.assertIn("não vazio", str(exc))


class StdioHygieneTests(ServerTestCase):
    """stdout é o canal JSON-RPC: nada de print() escapar pra lá."""

    def test_tool_output_never_leaks_to_stdout(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.call("list_sessions")
            self.call("list_hosts")
            self.call("send", {"target": "w1", "text": "x"})
        self.assertEqual(buf.getvalue(), "")

    def test_internal_prints_are_captured_not_forwarded(self):
        captured = self.mcp._run_capturing(sf.cmd_list, {"remote": None})
        self.assertIn("w1", captured)


class DeterminismTests(ServerTestCase):
    def test_two_calls_produce_equal_structures(self):
        first = self.call("list_sessions")
        second = self.call("list_sessions")
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
