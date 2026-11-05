"""Testes de ``tools/update-mcp.sh`` — manter o MCP em sincronia com o repo.

O problema que o script resolve: o servidor MCP é um processo stdio filho do
Claude Code. Ele lê ``tools/mcp_server.py`` **uma vez** por sessão, então
código novo no disco não chega ao processo já em execução, e um wheel/dep nova
nem está no venv. Numa frota de 4 hosts isso vira divergência silenciosa: o
MCP do Mac tem a tool nova e o do Duck não.

O script é idempotente e barato (roda no self-update a cada 30 min), então os
testes cobrem exatamente as decisões que ele toma: já está em dia? falta só a
dep? o processo em execução está defasado?

Sandbox: o script resolve o repo a partir da própria localização, então cada
teste monta um ``<tmp>/tools/`` com cópias reais dos scripts. O ``uv`` é
substituído por um stub que registra a chamada — fronteira de ferramenta
externa, não lógica sob teste.
"""

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest

HERE = Path(__file__).parent
REAL_TOOLS = "update-mcp.sh", "install-mcp.sh"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _combined(*paths: Path) -> str:
    """Hash combinado — mesma fórmula que o script e o servidor usam."""
    h = hashlib.sha256()
    for p in paths:
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


class UpdateMcpTestCase(unittest.TestCase):
    """Sandbox com tools/ real + HOME/CLAUDE_CONFIG isolados."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.repo = self.root / "repo"
        self.tools = self.repo / "tools"
        self.tools.mkdir(parents=True)
        self.venv = self.tools / ".venv-mcp"
        self.home = self.root / "home"
        self.home.mkdir()
        self.claude_json = self.home / ".claude.json"
        self.claude_json.write_text('{"mcpServers": {}}', encoding="utf-8")

        # Cópias REAIS dos scripts/fluxo de dados.
        for name in REAL_TOOLS:
            shutil.copy2(HERE / name, self.tools / name)
            (self.tools / name).chmod(0o755)
        (self.tools / "mcp_server.py").write_text("# servidor v1\n", encoding="utf-8")
        (self.tools / "sf").write_text("# cli v1\n", encoding="utf-8")
        (self.tools / "mcp-requirements.txt").write_text("mcp\n", encoding="utf-8")

        # Stub do uv: registra cada invocação em log em vez de ir à rede.
        # `uv venv` materializa um python-wrapper de verdade, senão o
        # install-mcp.sh (chamado de propósito, não stubado) não teria com o
        # que escrever o JSON e o teste deixaria de cobrir a integração.
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.uv_log = self.root / "uv.log"
        uv = self.bin / "uv"
        uv.write_text(
            f"""#!/bin/sh
echo "$@" >> "{self.uv_log}"
if [ "$1" = "venv" ]; then
  mkdir -p "$2/bin"
  cat > "$2/bin/python" <<'PYEOF'
#!/bin/sh
# Python de mentira: `import mcp` sempre funciona (é o que o installer checa),
# qualquer outra coisa delega ao interpretador real (heredocs de JSON).
if [ "$1" = "-c" ] && [ "$2" = "import mcp" ]; then exit 0; fi
exec {sys.executable} "$@"
PYEOF
  chmod +x "$2/bin/python"
fi
exit 0
""",
            encoding="utf-8",
        )
        uv.chmod(0o755)

    # -- helpers ----------------------------------------------------------- #
    def script(self) -> Path:
        return self.tools / "update-mcp.sh"

    def run_script(self, *args: str) -> subprocess.CompletedProcess:
        env = {
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "HOME": str(self.home),
            "CLAUDE_CONFIG": str(self.claude_json),
            "PYTHON": sys.executable,
        }
        return subprocess.run(
            [str(self.script()), *args],
            capture_output=True, text=True, env=env, timeout=120,
        )

    def fake_install(self):
        """Simula instalação já SINCRONIZADA: venv, registro e estado em dia.

        O `.state` importa: sem ele o script (corretamente) assume que nunca
        rodou e reinstala as deps. Os testes de "só quando muda" precisam
        partir de um estado conhecido.
        """
        (self.venv / "bin").mkdir(parents=True)
        py = self.venv / "bin" / "python"
        # Delega ao interpretador real, MENOS o `import mcp` que o installer
        # usa como sonda. Um stub que só faz `exit 0` faria o installer achar
        # que o SDK existe e não escrever o JSON — o teste passaria a medir o
        # stub, não o script.
        py.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "-c" ] && [ "$2" = "import mcp" ]; then exit 0; fi\n'
            f'exec {sys.executable} "$@"\n',
            encoding="utf-8",
        )
        py.chmod(0o755)
        import json
        self.claude_json.write_text(json.dumps({
            "mcpServers": {"sessionflow": {
                "command": str(py),
                "args": [str(self.tools / "mcp_server.py")],
            }}
        }), encoding="utf-8")
        (self.venv / ".state").write_text(
            f"code={_combined(self.tools / 'mcp_server.py', self.tools / 'sf')}\n"
            f"reqs={_sha(self.tools / 'mcp-requirements.txt')}\n",
            encoding="utf-8",
        )

    # -- testes ------------------------------------------------------------ #
    def test_check_acusa_instalacao_ausente(self):
        out = self.run_script("--check")
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertIn("não instalado", out.stdout)

    def test_check_diz_em_dia_quando_nada_mudou(self):
        self.fake_install()
        self.assertEqual(self.run_script().returncode, 0)   # grava o estado
        out = self.run_script("--check")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("em dia", out.stdout)

    def test_check_acusa_servidor_alterado(self):
        self.fake_install()
        self.assertEqual(self.run_script().returncode, 0)
        # Release novo mexe no servidor → deixa de estar em dia.
        (self.tools / "mcp_server.py").write_text("# servidor v2\n", encoding="utf-8")
        out = self.run_script("--check")
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertIn("desatualizado", out.stdout)

    def test_check_acusa_cli_alterado(self):
        # mcp_server carrega tools/sf como módulo: mudar o CLI também exige
        # processo novo, senão o agente segue com o comportamento velho.
        self.fake_install()
        self.assertEqual(self.run_script().returncode, 0)
        (self.tools / "sf").write_text("# cli v2\n", encoding="utf-8")
        self.assertEqual(self.run_script("--check").returncode, 1)

    def test_instala_deps_quando_requirements_mudam(self):
        self.fake_install()
        self.assertEqual(self.run_script().returncode, 0)
        self.assertFalse(self.uv_log.exists() and "mcp" in self.uv_log.read_text())
        (self.tools / "mcp-requirements.txt").write_text("mcp\nhttpx\n", encoding="utf-8")
        out = self.run_script()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        chamadas = self.uv_log.read_text()
        self.assertIn("pip install", chamadas)
        self.assertIn("mcp-requirements.txt", chamadas)

    def test_nao_reinstala_deps_sem_mudanca(self):
        self.fake_install()
        self.assertEqual(self.run_script().returncode, 0)
        antes = self.uv_log.read_text() if self.uv_log.exists() else ""
        self.assertEqual(self.run_script().returncode, 0)
        depois = self.uv_log.read_text() if self.uv_log.exists() else ""
        self.assertEqual(antes, depois, "segunda execução não deveria tocar no venv")

    def test_avisa_restart_quando_processo_em_execucao_esta_defasado(self):
        self.fake_install()
        self.assertEqual(self.run_script().returncode, 0)
        # O servidor grava o hash do que carregou; simula um processo antigo.
        (self.venv / ".loaded").write_text("deadbeefdeadbeef", encoding="utf-8")
        out = self.run_script("--check")
        # Aviso de restart NÃO é "desatualizado no disco" — o disco está certo,
        # quem está velho é o processo. São dois diagnósticos distintos.
        self.assertIn("defasado", out.stdout)
        self.assertIn("reinicie o claude code", out.stdout.lower())
        self.assertEqual(out.returncode, 1)

    def test_nao_avisa_restart_quando_carregado_bate_com_disco(self):
        self.fake_install()
        self.assertEqual(self.run_script().returncode, 0)
        desejado = _combined(self.tools / "mcp_server.py", self.tools / "sf")
        (self.venv / ".loaded").write_text(desejado, encoding="utf-8")
        out = self.run_script("--check")
        self.assertNotIn("defasado", out.stdout)
        self.assertEqual(out.returncode, 0)

    def test_registra_entrada_no_claude_json_se_faltar(self):
        self.fake_install()
        import json
        self.claude_json.write_text('{"mcpServers": {}}', encoding="utf-8")
        self.assertEqual(self.run_script().returncode, 0)
        data = json.loads(self.claude_json.read_text())
        self.assertIn("sessionflow", data["mcpServers"])
        self.assertEqual(data["mcpServers"]["sessionflow"]["args"],
                         [str(self.tools / "mcp_server.py")])

    def test_instala_quando_venv_nao_existe(self):
        out = self.run_script()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        # Delegou pro install-mcp.sh (que cria o venv) em vez de duplicar lógica.
        self.assertIn("instalando", out.stdout.lower())


if __name__ == "__main__":
    unittest.main()
