"""Testes da célula de auto-boot do Kaggle.

O Colab lê os secrets com ``google.colab.userdata``; o Kaggle **não tem** esse
módulo — lá é ``kaggle_secrets.UserSecretClient``. Uma célula copiada do Colab
falha com ``ModuleNotFoundError`` no primeiro import, antes de qualquer coisa
útil acontecer.

Contrato travado aqui:

1. lê secrets pelo cliente do Kaggle, nunca por ``google.colab``;
2. resolve o notebook URL do *release* (não da máquina de quem escreveu);
3. aceita o ``SF_HOST_ID`` como secret — o Kaggle também é sandbox efêmero e
   registra o host no primeiro heartbeat, igual ao Colab;
4. a célula é auto-suficiente (nada de depender de arquivo local já presente).
"""

from pathlib import Path
import re
import unittest

CELULA = Path(__file__).with_name("kaggle-selfboot-cell.py")


class CelulaKaggleTests(unittest.TestCase):
    def setUp(self):
        self.txt = CELULA.read_text(encoding="utf-8")
        self.code = "\n".join(
            linha for linha in self.txt.splitlines()
            if not linha.lstrip().startswith("#")
        )

    def test_usa_o_cliente_de_secrets_do_kaggle(self):
        """O nome da classe é ``UserSecretsClient`` — com "s" no plural.

        A primeira versão desta célula usava ``UserSecretClient`` (singular):
        um ``ImportError`` em runtime, no notebook real, porque a célula nem
        chega a rodar. O erro passou pela suíte porque o teste assertava a
        mesma grafia errada que o código — teste que espelha o bug não é
        teste. Aqui a grafia vem da execução no Kaggle (Python 3.13), não da
        memória de quem escreveu.
        """
        self.assertIn("kaggle_secrets", self.code)
        self.assertIn("UserSecretsClient", self.code,
                      "grafia errada quebra no import, antes de qualquer uso")
        self.assertNotIn("UserSecretClient\n", self.code.replace("UserSecretsClient", ""),
                         "a grafia singular não existe no módulo")

    def test_nao_importa_google_colab(self):
        self.assertNotIn("google.colab", self.code,
                         "google.colab não existe no Kaggle — quebra no import")

    def test_baixa_o_selfboot_do_release(self):
        self.assertIn("kaggle-selfboot.sh", self.code)
        self.assertRegex(self.code, r"https://raw\.githubusercontent\.com/",
                         "célula vem sozinha: precisa baixar do release público")

    def test_repassa_os_secrets_para_o_processo(self):
        for k in ("SF_ENV_B64", "SF_HOST_ID"):
            self.assertIn(k, self.code, f"secret obrigatório {k} não repassado")
        # Os opcionais entram por lista, não por if-por-variável.
        for k in ("SF_HOST_ID_GPU", "ANTHROPIC_AUTH_TOKEN", "GRAPHIFY_S3_SECRET_KEY"):
            self.assertIn(k, self.code)

    def test_secret_ausente_nao_derruba_a_celula(self):
        # Kaggle levanta UserSecretNotFoundError; tem que ser tolerado.
        self.assertRegex(self.code, r"except[^\n]*(Exception|SecretNotFound)",
                         "secret opcional ausente não pode abortar o boot")

    def test_nao_imprime_valor_de_secret(self):
        # print de env é vazamento em log de notebook (que é compartilhável).
        for linha in self.code.splitlines():
            if "print" in linha:
                self.assertNotRegex(linha, r"SF_ENV_B64|ANTHROPIC_AUTH_TOKEN|SECRET_KEY",
                                    f"possível vazamento: {linha.strip()}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
