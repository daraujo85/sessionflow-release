"""Testes do ``tools/colab-selfboot.sh`` — o contrato que o Colab precisa honrar.

O Colab tem uma armadilha específica: o worker roda como **root**, mas o agente
(`claude`) roda como o usuário **diego** — `/root` é `drwx------`. Um servidor
MCP instalado em ``/root/sessionflow/tools/.venv-mcp`` fica registrado no
``.claude.json`` (que é symlink para ``/home/diego``) e simplesmente **não
executa**: o agente sobe sem MCP nenhum e o sintoma é "as tools não aparecem".

Estes testes travam as decisões que evitam essa regressão:

1. o MCP é instalado num caminho legível pelo ``diego``;
2. a instalação roda no selfboot (senão cada runtime novo do Colab volta
   zerado, já que a VM é efêmera);
3. o registro usa o python do venv desse caminho, não o de root.

Testar bash puro exigiria subir um Colab. Em vez disso, o contrato é verificado
lendo o script — é o artefato que precisa estar certo, e uma asserção sobre o
texto pega exatamente a classe de erro que já aconteceu (caminho inacessível).
"""

import re
from pathlib import Path
import unittest

SCRIPT = Path(__file__).with_name("colab-selfboot.sh")


class ColabSelfbootMcpTests(unittest.TestCase):
    def setUp(self):
        self.text = SCRIPT.read_text(encoding="utf-8")
        # Ignora comentários: o contrato é o que o script EXECUTA.
        self.code = "\n".join(
            line for line in self.text.splitlines()
            if not line.lstrip().startswith("#")
        )

    def test_instala_o_mcp_no_selfboot(self):
        self.assertIn("install-mcp.sh", self.code,
                      "runtime novo do Colab viria sem MCP — a VM é efêmera")

    def test_instala_em_caminho_acessivel_ao_diego_nao_em_root(self):
        # O venv precisa viver onde `diego` lê. /root é drwx------.
        self.assertNotIn("/root/sessionflow/tools/.venv-mcp", self.code,
                         "venv em /root é inacessível ao agente (usuário diego)")

    def test_registro_do_mcp_aponta_para_o_venv_acessivel(self):
        # O install-mcp.sh grava CLAUDE_CONFIG/Home do processo que o chama.
        # Se rodar como root sem HOME=/home/diego, registra um caminho que o
        # agente não abre.
        mcp_block = self._mcp_section()
        self.assertTrue(
            "HOME=/home/diego" in mcp_block or "CLAUDE_CONFIG" in mcp_block,
            "instalação precisa rodar com o HOME (ou CLAUDE_CONFIG) do diego",
        )

    def test_concede_leitura_do_caminho_ao_diego(self):
        mcp_block = self._mcp_section()
        self.assertTrue(
            re.search(r"chown|setfacl", mcp_block),
            "o diretório do MCP precisa ser legível pelo diego",
        )

    def _mcp_section(self) -> str:
        """Trecho do script que trata do MCP."""
        idx = self.code.find("install-mcp.sh")
        if idx == -1:
            self.fail("não instala o MCP")
        return self.code[max(0, idx - 600): idx + 600]


if __name__ == "__main__":
    unittest.main()
