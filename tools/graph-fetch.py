#!/usr/bin/env python3
"""Compartilha o artefato do graphify via MinIO (S3) — publica no Mac, consome
no resto da frota.

Por que existe
--------------
``graphify extract . --code-only`` custa tempo (e, no modo semântico, LLM). O Mac
já indexa o repositório; Deepin, Duck e Colab não têm por que refazer o mesmo
trabalho. Este script move o artefato pronto.

O que viaja (medido em 2026-11-05, repo sessionflow):

============================  ======  ==============================
arquivo                       tam.    portátil?
============================  ======  ==============================
``graph.json``                6.6M     sim — 0 caminhos absolutos
``.graphify_analysis.json``   404K     sim
``.graphify_labels.json``     8K       sim
``cache/stat-index.json``     84K      sim — 358 chaves, todas relativas
============================  ======  ==============================

As pastas datadas (``graphify-out/2026-11-03/`` etc.) somam ~84M e são
snapshots históricos: ficam de fora de propósito. Total transportado: ~7.1 MB.

A pegadinha
-----------
``.graphify_root`` guarda o caminho absoluto do Mac e **não** é portátil. O
``graphify query`` ignora isso (só lê ``graph.json``, e funciona: verificado
rodando no destino com o marcador apontando para um caminho inexistente). Mas o
``graphify update .`` tem um guarda ``out-of-repo`` que descarta o marcador
estranho e **reindexa a árvore inteira do zero**. Por isso o ``fetch``
*reescreve* o marcador para o root local.

Uso
---
    ./tools/graph-fetch.py publish          # no Mac, após indexar
    ./tools/graph-fetch.py fetch            # no Deepin/Duck/Colab
    ./tools/graph-fetch.py status           # em qualquer host

Configuração: ``~/.claude/secrets/graphify-s3.env`` (ou variáveis de ambiente)

    GRAPHIFY_S3_ENDPOINT=https://s3.boletoazap.dev.br
    GRAPHIFY_S3_ACCESS_KEY=...
    GRAPHIFY_S3_SECRET_KEY=...
    GRAPHIFY_S3_BUCKET=graphify
    GRAPHIFY_S3_PREFIX=sessionflow          # opcional

Sem dependências: só stdlib. A frota não tem ``aws-cli``, ``rclone`` nem ``mc``
de forma uniforme (Hetzner e Deepin não têm ``aws``; o Mac não tem ``mc``), mas
todos têm ``python3`` com ``ssl``/``hashlib``/``hmac``. Daí o SigV4 na mão.
"""

import argparse
import hashlib
import hmac
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

GRAPHIFY_OUT = "graphify-out"
MANIFESTO = "PUBLISHED.json"
ENV_PADRAO = Path.home() / ".claude" / "secrets" / "graphify-s3.env"

# Só o que é essencial para consultar o grafo. NUNCA inclua as pastas datadas:
# somam ~84M de snapshots e não servem para consulta.
ESSENCIAIS = (
    "graph.json",
    ".graphify_analysis.json",
    ".graphify_labels.json",
    "cache/stat-index.json",
)


# --------------------------------------------------------------------- SigV4


def uri_canonica(path: str) -> str:
    """Percent-encode de cada segmento do path, preservando as barras.

    O SigV4 exige que o URI seja normalizado exatamente como o cliente o envia;
    se o ``urllib`` escapar um espaço e a assinatura não fizer o mesmo, o MinIO
    devolve 403 SignatureDoesNotMatch.
    """
    return urllib.parse.quote(path, safe="/-_.~")


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def derive_signing_key(secret: str, date: str, region: str, service: str) -> bytes:
    """Cadeia HMAC da AWS: secret -> data -> região -> serviço -> aws4_request."""
    k = _hmac(("AWS4" + secret).encode("utf-8"), date)
    k = _hmac(k, region)
    k = _hmac(k, service)
    return _hmac(k, "aws4_request")


def calcular_assinatura(
    *,
    method: str,
    canonical_uri: str,
    canonical_query: str,
    headers: dict,
    payload_hash: str,
    amz_date: str,
    key: str,
    secret: str,
    region: str,
    service: str,
    date: str,
) -> tuple:
    """Devolve ``(signed_headers, signature)`` prontos para o header Authorization."""
    normalizados = {k.lower().strip(): str(v).strip() for k, v in headers.items()}
    nomes = sorted(normalizados)
    canonical_headers = "".join(f"{n}:{normalizados[n]}\n" for n in nomes)
    signed_headers = ";".join(nomes)

    canonical_request = "\n".join([
        method,
        canonical_uri,
        canonical_query,
        canonical_headers,
        signed_headers,
        payload_hash,
    ])

    scope = f"{date}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256",
        amz_date,
        scope,
        hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
    ])

    signing_key = derive_signing_key(secret, date, region, service)
    signature = hmac.new(
        signing_key, string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return signed_headers, signature


# ------------------------------------------------------------------ config


def carregar_config(env=None, arquivo=None) -> dict:
    """Lê a configuração de env + arquivo. Variáveis de ambiente vencem.

    Nunca imprime os valores lidos — só usa.
    """
    env = os.environ if env is None else env
    arquivo = ENV_PADRAO if arquivo is None else Path(arquivo)

    valores = {}
    if arquivo.is_file():
        for linha in arquivo.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            k, _, v = linha.partition("=")
            valores[k.strip()] = v.strip().strip('"').strip("'")

    for k in (
        "GRAPHIFY_S3_ENDPOINT",
        "GRAPHIFY_S3_ACCESS_KEY",
        "GRAPHIFY_S3_SECRET_KEY",
        "GRAPHIFY_S3_BUCKET",
        "GRAPHIFY_S3_PREFIX",
        "GRAPHIFY_S3_REGION",
    ):
        if env.get(k):
            valores[k] = env[k]

    faltando = [
        k for k in ("GRAPHIFY_S3_ENDPOINT", "GRAPHIFY_S3_ACCESS_KEY",
                    "GRAPHIFY_S3_SECRET_KEY")
        if not valores.get(k)
    ]
    if faltando:
        raise SystemExit(
            "config ausente: falta " + ", ".join(faltando) + ".\n"
            f"Rode o setup do MinIO e escreva em {ENV_PADRAO}:\n"
            "  GRAPHIFY_S3_ENDPOINT=https://s3.boletoazap.dev.br\n"
            "  GRAPHIFY_S3_ACCESS_KEY=<access key do usuario graphify>\n"
            "  GRAPHIFY_S3_SECRET_KEY=<secret key do usuario graphify>\n"
            "  GRAPHIFY_S3_BUCKET=graphify"
        )

    return {
        "endpoint": valores["GRAPHIFY_S3_ENDPOINT"].rstrip("/"),
        "access_key": valores["GRAPHIFY_S3_ACCESS_KEY"],
        "secret_key": valores["GRAPHIFY_S3_SECRET_KEY"],
        "bucket": valores.get("GRAPHIFY_S3_BUCKET") or "graphify",
        "prefixo": (valores.get("GRAPHIFY_S3_PREFIX") or "").strip("/"),
        "region": valores.get("GRAPHIFY_S3_REGION") or "us-east-1",
    }


# ------------------------------------------------------------------- HTTP


def _requisitar(cfg, method, chave, body=b"", aws_date=None):
    """Assina e executa uma requisição S3 path-style. Devolve (status, bytes)."""
    aws_date = aws_date or __import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    ).strftime("%Y%m%dT%H%M%SZ")
    date = aws_date[:8]

    alvo = f"/{cfg['bucket']}/{chave}" if chave else f"/{cfg['bucket']}"
    u = urllib.parse.urlparse(cfg["endpoint"])
    host = u.netloc
    payload_hash = hashlib.sha256(body).hexdigest()

    headers = {
        "host": host,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": aws_date,
    }

    signed_headers, signature = calcular_assinatura(
        method=method,
        canonical_uri=uri_canonica(alvo),
        canonical_query="",
        headers=headers,
        payload_hash=payload_hash,
        amz_date=aws_date,
        key=cfg["access_key"],
        secret=cfg["secret_key"],
        region=cfg["region"],
        service="s3",
        date=date,
    )

    scope = f"{date}/{cfg['region']}/s3/aws4_request"
    headers["Authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={cfg['access_key']}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )

    req = urllib.request.Request(
        f"{cfg['endpoint']}{uri_canonica(alvo)}",
        data=body if method == "PUT" else None,
        headers=headers,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except urllib.error.URLError as e:
        raise SystemExit(f"falha de rede ao falar com {cfg['endpoint']}: {e.reason}")


# ---------------------------------------------------------------- artefato


def _out(root: Path) -> Path:
    return Path(root) / GRAPHIFY_OUT


def prefixo_efetivo(cfg: dict, root: Path) -> str:
    """Namespace do projeto dentro do balde.

    Padrão = nome da pasta do repositório. Isso é o que impede dois projetos de
    se sobrescreverem: se o prefixo viesse fixo do arquivo de config, publicar
    ``sessionflow`` e depois ``prata`` sobrescreveria o primeiro — e o consumidor
    baixaria o grafo do projeto errado sem nenhum aviso.
    """
    explicito = (cfg.get("prefixo") or "").strip().strip("/")
    if explicito:
        return explicito
    return Path(root).resolve().name


def chaves_essenciais(root: Path) -> list:
    """Chaves (relativas a graphify-out/) que existem e devem viajar."""
    base = _out(root)
    return [k for k in ESSENCIAIS if (base / k).is_file()]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _marcador_origem(root: Path) -> str:
    """Root de varredura registrado pelo graphify (absoluto, do host de origem)."""
    marcador = _out(root) / ".graphify_root"
    if marcador.is_file():
        txt = marcador.read_text(encoding="utf-8-sig").strip()
        if txt:
            return txt
    return str(Path(root).resolve())


def _commit(root: Path):
    try:
        d = json.loads((_out(root) / "graph.json").read_text(encoding="utf-8"))
        return d.get("built_at_commit")
    except (OSError, ValueError):
        return None


def publicar(root: Path, cfg, aws_date=None) -> list:
    """Sobe os essenciais + manifesto. Devolve as chaves enviadas."""
    root = Path(root).resolve()
    if not (_out(root) / "graph.json").is_file():
        raise SystemExit(
            f"nada para publicar: {_out(root) / 'graph.json'} não existe.\n"
            "Rode `graphify extract . --code-only` (ou `graphify update .`) antes."
        )

    chaves = chaves_essenciais(root)
    base = _out(root)
    manifest = {
        "origem": _marcador_origem(root),
        "commit": _commit(root),
        "gerado_em": aws_date,
        "arquivos": {k: _sha256(base / k) for k in chaves},
    }

    pref = prefixo_efetivo(cfg, root)

    def remota(k):
        return f"{pref}/{k}"

    enviados = []
    for k in chaves:
        status, corpo = _requisitar(cfg, "PUT", remota(k), (base / k).read_bytes(), aws_date)
        if status not in (200, 201, 204):
            raise SystemExit(
                f"falha ao enviar {k}: HTTP {status} — {corpo[:200].decode(errors='replace')}"
            )
        enviados.append(remota(k))

    corpo = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
    status, resp = _requisitar(cfg, "PUT", remota(MANIFESTO), corpo, aws_date)
    if status not in (200, 201, 204):
        raise SystemExit(f"falha ao enviar {MANIFESTO}: HTTP {status}")

    return enviados


def _ler_manifesto(cfg, root, aws_date=None):
    chave = f"{prefixo_efetivo(cfg, root)}/{MANIFESTO}"
    status, corpo = _requisitar(cfg, "GET", chave, aws_date=aws_date)
    if status == 404:
        return None
    if status != 200:
        raise SystemExit(f"falha ao ler {MANIFESTO}: HTTP {status}")
    try:
        return json.loads(corpo)
    except ValueError:
        raise SystemExit(f"{MANIFESTO} no balde não é JSON válido — republique.")


def baixar(root: Path, cfg, aws_date=None) -> list:
    """Baixa os essenciais para ``<root>/graphify-out`` e reescreve o marcador."""
    root = Path(root).resolve()
    manifest = _ler_manifesto(cfg, root, aws_date)
    if manifest is None:
        raise SystemExit(
            f"nada publicado no balde ({cfg['bucket']}).\n"
            "No Mac, rode: ./tools/graph-fetch.py publish"
        )

    base = _out(root)
    baixados = []
    pref = prefixo_efetivo(cfg, root)
    for k, sha_esperado in sorted(manifest.get("arquivos", {}).items()):
        chave = f"{pref}/{k}"
        status, corpo = _requisitar(cfg, "GET", chave, aws_date=aws_date)
        if status != 200:
            raise SystemExit(f"falha ao baixar {k}: HTTP {status}")

        obtido = hashlib.sha256(corpo).hexdigest()
        if obtido != sha_esperado:
            raise SystemExit(
                f"sha não confere em {k}: esperado {sha_esperado[:12]}…, "
                f"obtido {obtido[:12]}… — objeto corrompido; republique no Mac."
            )

        destino = base / k
        destino.parent.mkdir(parents=True, exist_ok=True)
        tmp = destino.with_suffix(destino.suffix + ".tmp")
        tmp.write_bytes(corpo)
        tmp.replace(destino)
        baixados.append(k)

    # O marcador do Mac aponta para um caminho que não existe aqui; deixá-lo
    # faria o `graphify update .` descartar e reindexar tudo do zero.
    (base / ".graphify_root").parent.mkdir(parents=True, exist_ok=True)
    (base / ".graphify_root").write_text(str(root), encoding="utf-8")

    return baixados


def estado(root: Path, cfg, aws_date=None) -> dict:
    """Compara o artefato local com o do balde. Não escreve nada."""
    root = Path(root).resolve()
    manifest = _ler_manifesto(cfg, root, aws_date)
    if manifest is None:
        return {"situacao": "sem-publicacao", "remoto": None, "local": None}

    sha_remoto = (manifest.get("arquivos") or {}).get("graph.json")
    local = _out(root) / "graph.json"
    rel = {
        "remoto": {"origem": manifest.get("origem"), "commit": manifest.get("commit"),
                   "sha": sha_remoto},
        "local": None,
    }
    if not local.is_file():
        rel["situacao"] = "ausente"
        return rel

    sha_local = _sha256(local)
    rel["local"] = {"caminho": str(local), "sha": sha_local,
                    "commit": _commit(root)}
    rel["situacao"] = "em-dia" if sha_local == sha_remoto else "desatualizado"
    return rel


# --------------------------------------------------------------------- CLI


def _raiz(args) -> Path:
    return Path(args.dir).resolve() if args.dir else Path(__file__).resolve().parent.parent


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="graph-fetch.py",
        description="Publica/consome o artefato do graphify via MinIO (S3).",
    )
    ap.add_argument("--dir", help="raiz do repositório (default: o do script)")
    ap.add_argument("--env-file", help=f"arquivo de config (default: {ENV_PADRAO})")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("publish", help="envia os essenciais do host local para o balde")
    sub.add_parser("fetch", help="baixa os essenciais do balde para o host local")
    sub.add_parser("status", help="compara local x balde (não escreve nada)")

    args = ap.parse_args(argv)
    root = _raiz(args)
    cfg = carregar_config(arquivo=args.env_file)

    if args.cmd == "publish":
        enviados = publicar(root, cfg)
        print(f"publicado: {len(enviados)} objeto(s) em {cfg['bucket']}/"
              f"{prefixo_efetivo(cfg, root)}")
        for k in enviados:
            print(f"  ↑ {k}")
        return 0

    if args.cmd == "fetch":
        baixados = baixar(root, cfg)
        print(f"baixado: {len(baixados)} objeto(s) para {_out(root)}")
        for k in baixados:
            print(f"  ↓ {k}")
        print(f"  .graphify_root -> {root}")
        return 0

    rel = estado(root, cfg)
    sit = rel["situacao"]
    print(f"situacao: {sit}")
    if rel["remoto"]:
        print(f"remoto: commit={rel['remoto']['commit']} origem={rel['remoto']['origem']}")
    if rel["local"]:
        print(f"local:  commit={rel['local']['commit']} sha={rel['local']['sha'][:12]}…")
    return 0 if sit == "em-dia" else 1


if __name__ == "__main__":
    sys.exit(main())
