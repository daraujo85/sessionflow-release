# Célula de auto-boot do SessionFlow — cole como 1ª célula do notebook (depois do keepalive).
# Pré-requisito (1x por conta Google): Colab ▸ 🔑 Secrets com acesso ao notebook ligado:
#   SF_ENV_B64, SF_HOST_ID, [SF_HOST_ID_GPU], [ANTHROPIC_AUTH_TOKEN]
# Valores: no Mac, `tools/colab-secrets.sh SF_ENV_B64` (copia pro clipboard).
import os, subprocess
from google.colab import userdata

env = dict(os.environ)
for k in ("SF_ENV_B64", "SF_HOST_ID", "SF_HOST_ID_GPU", "ANTHROPIC_AUTH_TOKEN"):
    try:
        env[k] = userdata.get(k)
    except Exception:
        pass  # opcional ausente; os obrigatórios o script valida
url = "https://raw.githubusercontent.com/daraujo85/sessionflow-release/main/tools/colab-selfboot.sh"
subprocess.run(f"curl -fsSL {url} -o /tmp/sf-selfboot.sh && bash /tmp/sf-selfboot.sh",
               shell=True, check=True, env=env)
