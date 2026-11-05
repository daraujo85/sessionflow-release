"""Testes de ``tools/install-mcp.sh`` — achar o ``uv`` sem depender do PATH.

Regressão real (Deepin 25): o ``uv`` mora em ``~/.local/bin/uv`` e esse
diretório **não** entra no PATH de shell não-interativo (ssh, cron, systemd).
O installer usava ``command -v uv`` puro, não achava, e caía no fallback
``python3 -m venv`` — que ali morre com ``ModuleNotFoundError: ensurepip``
porque o pacote ``python3.12-venv`` nunca foi instalado. O sintoma na frota é
"MCP: desatualizado/não instalado" num host onde a ferramenta está presente e
funciona perfeitamente quando o humano roda o mesmo comando no terminal.

O contrato testado: se ``uv`` existe, o installer o usa **independente de
PATH**. O sandbox substitui o ``uv`` por um stub (fronteira de ferramenta
externa); o que está sob teste é a descoberta, não o gerenciador de pacotes.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest

HERE = Path(__file__).parent


class InstallMcpFindsUvTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.repo = self.root / "repo"
        self.tools = self.repo / "tools"
        self.tools.mkdir(parents=True)
        self.home = self.root / "home"
        self.home.mkdir()
        self.claude_json = self.home / ".claude.json"
        self.claude_json.write_text('{"mcpServers": {}}', encoding="utf-8")

        shutil.copy2(HERE / "install-mcp.sh", self.tools / "install-mcp.sh")
        (self.tools / "install-mcp.sh").chmod(0o755)
        (self.tools / "mcp_server.py").write_text("# servidor\n", encoding="utf-8")

        self.uv_log = self.root / "uv.log"
        # `uv` só no ~/.local/bin — fora do PATH do teste, como no host real.
        local_bin = self.home / ".local" / "bin"
        local_bin.mkdir(parents=True)
        self._uv(local_bin / "uv")

        # PATH mínimo: nada de diretório com uv. O `python3` é um symlink para
        # o interpretador que roda os testes, senão o sandbox pegaria o 3.9 do
        # macOS e o teste mediria versão de Python em vez da descoberta do uv.
        fakebin = self.root / "bin"
        fakebin.mkdir()
        (fakebin / "python3").symlink_to(sys.executable)
        self.path = f"{fakebin}:/usr/bin:/bin"

    def _uv(self, destino: Path) -> None:
        destino.write_text(
            f"""#!/bin/sh
echo "$@" >> "{self.uv_log}"
if [ "$1" = "venv" ]; then
  mkdir -p "$2/bin"
  cat > "$2/bin/python" <<'PYEOF'
#!/bin/sh
if [ "$1" = "-c" ] && [ "$2" = "import mcp" ]; then exit 0; fi
exec {sys.executable} "$@"
PYEOF
  chmod +x "$2/bin/python"
fi
exit 0
""",
            encoding="utf-8",
        )
        destino.chmod(0o755)

    def run_installer(self) -> subprocess.CompletedProcess:
        env = {
            "PATH": self.path,
            "HOME": str(self.home),
            "CLAUDE_CONFIG": str(self.claude_json),
        }
        return subprocess.run(
            [str(self.tools / "install-mcp.sh")],
            capture_output=True, text=True, env=env, timeout=120,
        )

    def test_acha_uv_em_local_bin_fora_do_path(self):
        out = self.run_installer()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertTrue(self.uv_log.exists(),
                        "não chamou o uv de ~/.local/bin — cairia no venv do sistema")
        self.assertIn("venv", self.uv_log.read_text())

    def test_venv_criado_pelo_uv_e_registrado(self):
        # O caminho feliz precisa terminar com registro utilizável: o objetivo
        # do script não é achar o uv, é deixar o agente com MCP funcionando.
        out = self.run_installer()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertTrue((self.tools / ".venv-mcp" / "bin" / "python").exists())
        self.assertIn("sessionflow", self.claude_json.read_text())

    def test_nao_quebra_quando_uv_realmente_nao_existe(self):
        # Sem uv em lugar nenhum, o fallback (venv do sistema) é o caminho
        # certo — a descoberta não pode virar erro fatal.
        (self.home / ".local" / "bin" / "uv").unlink()
        out = self.run_installer()
        self.assertIn("venv", " ".join([out.stdout, out.stderr]).lower())


if __name__ == "__main__":
    unittest.main()
