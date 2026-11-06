"""Testes do ``sandbox-selfboot.sh`` — o mesmo selfboot servindo Kaggle e Colab.

Kaggle e Colab são o mesmo problema: VM efêmera, ``root``, precisa de
``tmux``/``cloudflared``/``uv``, agente roda como ``diego`` (não root), grafo
consumido do MinIO. A diferença real é o **root do sandbox**:

    Colab   /content              (persistente só durante a sessão)
    Kaggle  /kaggle/working       (é o que sobrevive ao "Save Version")

Por isso não se forka o script: ele lê ``SF_SANDBOX_ROOT`` (default
``/content``), e o Kaggle é um wrapper que exporta a variável e chama o mesmo
arquivo. Forkar criaria duas cópias divergindo em silêncio — exatamente a
classe de bug que o ``graph-fetch.py`` já sofreu entre Mac e Deepin.

O contrato testado aqui:

1. nada de ``/content`` hardcoded fora do default;
2. o default continua ``/content`` (Colab não muda);
3. o wrapper do Kaggle exporta o root e delega ao mesmo script;
4. o ``--dir sessionflow`` que o agente vê aponta para dentro do root.
"""

import re
from pathlib import Path
import unittest

HERE = Path(__file__).parent
SELFBOOT = HERE / "colab-selfboot.sh"
KAGGLE = HERE / "kaggle-selfboot.sh"


class SandboxRootParametrizacaoTests(unittest.TestCase):
    def setUp(self):
        self.text = SELFBOOT.read_text(encoding="utf-8")
        self.code = "\n".join(
            linha for linha in self.text.splitlines()
            if not linha.lstrip().startswith("#")
        )

    def test_default_do_root_continua_content(self):
        # Colab não pode mudar de comportamento.
        self.assertRegex(self.code, r"\$\{SF_SANDBOX_ROOT:-/content\}",
                         "o default tem que ser /content (Colab)")

    def test_work_deriva_do_root(self):
        self.assertNotIn("WORK=/content", self.code,
                         "WORK precisa derivar de SF_SANDBOX_ROOT, não ser fixo")

    def test_scan_roots_deriva_do_root(self):
        self.assertNotIn("SESSIONFLOW_SCAN_ROOTS=/content ", self.code + " ",
                         "SCAN_ROOTS fixo em /content quebra o Kaggle")

    def test_clones_e_work_derivam_do_root(self):
        # mkdir/setfacl não podem apontar para /content fixo.
        for linha in self.code.splitlines():
            if "setfacl" in linha or "mkdir -p /content" in linha:
                self.assertNotRegex(linha, r"(?<!SF_SANDBOX_ROOT)\s/content/",
                                    f"caminho fixo: {linha.strip()}")

    def test_reconhece_kaggle_pelo_ambiente(self):
        # Kaggle expõe KAGGLE_KERNEL_RUN_TYPE; dá para ajustar sozinho.
        self.assertIn("KAGGLE", self.code,
                      "sem isso um notebook Kaggle usaria /content inexistente")


class WrapperKaggleTests(unittest.TestCase):
    def test_wrapper_existe_e_e_executavel(self):
        self.assertTrue(KAGGLE.is_file(), "falta tools/kaggle-selfboot.sh")

    def test_wrapper_exporta_root_do_kaggle_e_delega(self):
        txt = KAGGLE.read_text(encoding="utf-8")
        code = "\n".join(
            linha for linha in txt.splitlines()
            if not linha.lstrip().startswith("#")
        )
        self.assertIn("/kaggle/working", code)
        self.assertIn("colab-selfboot.sh", code,
                      "o wrapper tem que chamar o MESMO selfboot, não duplicar")

    def test_wrapper_nao_duplica_logica_de_worker(self):
        txt = KAGGLE.read_text(encoding="utf-8")
        # Sinais de fork: recriar tmux do worker / uv sync por conta própria.
        self.assertNotIn("tmux new-session -d -s sessionflow-worker", txt)
        self.assertNotIn("uv sync", txt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
