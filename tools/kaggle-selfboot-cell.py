# Célula de auto-boot do SessionFlow — cole como 1ª célula do notebook Kaggle.
# Pré-requisito (1x neste notebook): Add-ons ▸ Secrets, com "Attach" ligado:
#   SF_ENV_B64            .env do worker em base64 (obrigatório)
#   SF_HOST_ID            host_id deste Kaggle em runtime CPU (obrigatório)
#   SF_HOST_ID_GPU        host_id quando o runtime tem GPU (opcional)
#   ANTHROPIC_AUTH_TOKEN  9router (opcional)
#   GRAPHIFY_S3_SECRET_KEY  grafo do MinIO (opcional — sobe sem)
#
# Valores no Mac:  ./tools/colab-secrets.sh SF_ENV_B64
#                  ./tools/colab-secrets.sh GRAPHIFY_S3_SECRET_KEY
#
# Antes de rodar: Settings ▸ Internet ▸ On. O kernel reinicia ao ligar a
# internet, então ligue PRIMEIRO e só depois execute esta célula.
#
# Diferenças em relação ao Colab (por isso a célula é separada):
#   - secrets: `kaggle_secrets.UserSecretsClient` (plural!), não
#     `google.colab.userdata`. A grafia singular não existe no módulo e
#     quebra no import, antes de qualquer coisa rodar;
#   - sandbox root: /kaggle/working, não /content (o wrapper resolve isso);
#   - a sessão cai sozinha (~12h GPU / ~9h CPU).
import os, subprocess

from kaggle_secrets import UserSecretsClient

OBRIGATORIOS = ("SF_ENV_B64", "SF_HOST_ID")
# SF_KAGGLE_URL é o que faz o host se identificar como Kaggle e QUAL notebook
# o botão Ligar abre. Sem ela, cada notebook/conta nova dependeria de uma
# lista chumbada de host_ids na API — que nunca alcança a 2ª conta.
OPCIONAIS = (
    "SF_HOST_ID_GPU", "SF_KAGGLE_URL", "ANTHROPIC_AUTH_TOKEN",
    "GRAPHIFY_S3_SECRET_KEY",
)

cliente = UserSecretsClient()
env = dict(os.environ)
faltando = []

for chave in OBRIGATORIOS + OPCIONAIS:
    try:
        env[chave] = cliente.get_secret(chave)
    except Exception:
        # Secret opcional ausente é normal; obrigatório é checado abaixo.
        if chave in OBRIGATORIOS:
            faltando.append(chave)

if faltando:
    raise SystemExit(
        "faltam secrets obrigatórios no Kaggle: " + ", ".join(faltando)
        + "\nAdd-ons ▸ Secrets ▸ Add, e marque 'Attach to notebook'."
    )

URL = ("https://raw.githubusercontent.com/daraujo85/sessionflow-release/"
       "main/tools/kaggle-selfboot.sh")
subprocess.run(
    f"curl -fsSL {URL} -o /tmp/kaggle-selfboot.sh && bash /tmp/kaggle-selfboot.sh",
    shell=True, check=True, env=env,
)
