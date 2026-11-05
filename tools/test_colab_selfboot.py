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


class ColabSelfbootGrafoTests(unittest.TestCase):
    """O Colab consome o grafo do Mac em vez de reindexar.

    ``graphify extract`` custa minutos e a VM do Colab é efêmera: reindexar a
    cada boot é desperdício puro quando o artefato pronto (~7 MB) vem do MinIO.

    Regra que o script precisa respeitar: no Colab o fetch **não** pode ser
    fatal. O Colab é um host de execução, não de indexação — se o MinIO estiver
    fora ou o token faltar, o worker ainda tem que subir. Por isso todo o bloco
    roda dentro de ``if``, nunca no fluxo principal com ``set -e``.
    """

    def setUp(self):
        self.text = SCRIPT.read_text(encoding="utf-8")
        self.code = "\n".join(
            line for line in self.text.splitlines()
            if not line.lstrip().startswith("#")
        )

    def test_baixa_o_grafo_no_boot(self):
        self.assertIn("graph-fetch.py", self.code,
                      "runtime novo do Colab viria sem grafo — a VM é efêmera")

    def test_aceita_credencial_opcional_do_minio(self):
        # Sem o secret, o fetch é pulado — não pode derrubar o worker.
        self.assertIn("GRAPHIFY_S3_SECRET_KEY", self.code)

    def test_falha_do_fetch_nao_derruba_o_boot(self):
        """Bloco precisa estar protegido: MinIO fora ≠ Colab sem worker."""
        idx = self.code.find("graph-fetch.py")
        if idx == -1:
            self.fail("não usa graph-fetch.py")
        bloco = self.code[max(0, idx - 700): idx + 400]
        self.assertTrue(
            re.search(r"\bif\b.*(graph-fetch|GRAPHIFY)", bloco),
            "o fetch tem que estar dentro de um `if` — senão set -e mata o boot",
        )

    def test_fetch_roda_como_diego_no_repo_dele(self):
        """O grafo precisa ficar no repo que o `diego` usa como --dir."""
        idx = self.code.find("graph-fetch.py")
        bloco = self.code[max(0, idx - 700): idx + 400]
        self.assertTrue(
            "/content/sessionflow" in bloco or "SF_GRAPH" in bloco,
            "o fetch precisa apontar para o repo que o agente vê",
        )


if __name__ == "__main__":
    unittest.main()
