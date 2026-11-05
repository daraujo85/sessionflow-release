"""Testes de ``tools/graph-fetch.py`` — artefato graphify compartilhado via MinIO.

O problema que o script resolve: cada host que quer usar o grafo precisa rodar
``graphify extract . --code-only`` (custa tempo e, no modo semântico, LLM). O Mac
já indexa tudo; Deepin, Duck e Colab só precisam *consumir* o mesmo artefato.

Medição que definiu o desenho (2026-11-05, repo sessionflow):

* ``graph.json`` ........ 6.6M, **0 caminhos absolutos** -> portátil de graça
* ``stat-index.json`` ... 84K, 358 chaves, **todas relativas** -> portátil
* ``.graphify_root`` .... 1 linha, caminho absoluto do Mac -> **não** portátil

E ``graphify query`` funciona no destino mesmo com ``.graphify_root`` apontando
para um caminho que não existe lá (verificado empiricamente): o ``query`` só lê
``graph.json``. Quem quebra é o ``graphify update .``, cujo guarda
``out-of-repo`` descarta o marcador e reindexa "do zero" a árvore inteira.

Daí as duas regras de ouro do fetcher: (1) só transportar os essenciais — nunca
as pastas datadas, que somam 91M; (2) **reescrever** ``.graphify_root`` para o
root local, para que o ``update`` incremental continue funcionando.

Fronteira de teste: a assinatura SigV4 é validada contra os vetores oficiais da
AWS (verdade externa, sem rede); o fluxo GET/PUT roda contra um servidor HTTP
de mentira em thread local. MinIO real nunca é tocado pela suíte.
"""

import hashlib
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import importlib.util

_spec = importlib.util.spec_from_file_location("graph_fetch", HERE / "graph-fetch.py")
gf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gf)


# ---------------------------------------------------------------- vetores AWS

# Exemplo oficial "GET Object" do guia de SigV4 da AWS (S3, us-east-1).
AWS_VECTOR = {
    "access_key": "AKIAIOSFODNN7EXAMPLE",
    "secret_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "region": "us-east-1",
    "service": "s3",
    "date": "20130524",
    "amz_date": "20130524T000000Z",
    "host": "examplebucket.s3.amazonaws.com",
    "canonical_uri": "/test.txt",
    "range": "bytes=0-9",
    "payload_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "expected_signature": "f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41",
    "expected_signed_headers": "host;range;x-amz-content-sha256;x-amz-date",
}

# Chave derivada da cadeia HMAC para a data/região/serviço do vetor acima.
# Conferida por cálculo independente (openssl + script separado). O vetor
# autoritativo, porém, é a ASSINATURA FINAL abaixo: se a cadeia de chaves
# estivesse errada por um byte, `test_assinatura_get_com_range_vetor_aws`
# não conseguiria reproduzir o valor publicado pela AWS.
AWS_SIGNING_KEY = "dbb893acc010964918f1fd433add87c70e8b0db6be30c1fbeafefa5ec6ba8378"

CFG_BASE = {
    "endpoint": "https://s3.example.test",
    "access_key": AWS_VECTOR["access_key"],
    "secret_key": AWS_VECTOR["secret_key"],
    "bucket": "graphify",
    "region": "us-east-1",
    "prefixo": "sessionflow",
}


# ------------------------------------------------------- servidor de mentira


class _FakeS3:
    """S3 mínimo em memória: guarda objetos e registra as requisições."""

    def __init__(self):
        self.objects = {}
        self.requests = []
        self.deny = set()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _record(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                outer.requests.append(
                    {
                        "method": self.command,
                        "path": self.path,
                        "headers": dict(self.headers),
                        "body": body,
                    }
                )
                return body

            @staticmethod
            def _chave(path):
                """Path-style: /<bucket>/<key> -> <key>. Igual ao MinIO real."""
                p = path.split("?", 1)[0].lstrip("/")
                bucket, _, resto = p.partition("/")
                outer.requests[-1]["bucket"] = bucket
                return resto

            def do_GET(self):
                self._record()
                key = self._chave(self.path)
                if key in outer.deny or key not in outer.objects:
                    self.send_response(404)
                    self.end_headers()
                    return
                data = outer.objects[key]
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.send_header(
                    "ETag", '"' + hashlib.md5(data).hexdigest() + '"'
                )
                self.end_headers()
                self.wfile.write(data)

            def do_HEAD(self):
                self._record()
                key = self._chave(self.path)
                if key in outer.deny or key not in outer.objects:
                    self.send_response(404)
                    self.end_headers()
                    return
                data = outer.objects[key]
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.send_header(
                    "ETag", '"' + hashlib.md5(data).hexdigest() + '"'
                )
                self.end_headers()

            def do_PUT(self):
                body = self._record()
                key = self._chave(self.path)
                if key in outer.deny:
                    self.send_response(403)
                    self.end_headers()
                    return
                outer.objects[key] = body
                self.send_response(200)
                self.send_header(
                    "ETag", '"' + hashlib.md5(body).hexdigest() + '"'
                )
                self.end_headers()

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()
        self.httpd.server_close()

    def cfg(self, **kw):
        c = dict(CFG_BASE)
        c["endpoint"] = f"http://127.0.0.1:{self.port}"
        c.update(kw)
        return c


def _grafo_falso(root: Path) -> None:
    """Monta um graphify-out/ com essenciais + uma pasta datada (ruído)."""
    out = root / "graphify-out"
    (out / "cache").mkdir(parents=True, exist_ok=True)
    (out / "2026-11-03").mkdir(parents=True, exist_ok=True)
    (out / "graph.json").write_text('{"nodes": [{"id": "a"}], "links": []}')
    (out / ".graphify_analysis.json").write_text('{"origem": "mac"}')
    (out / ".graphify_labels.json").write_text('{"clusters": {}}')
    (out / "cache" / "stat-index.json").write_text('{"a.py": {"size": 1}}')
    (out / ".graphify_root").write_text("/Users/diegoaraujo/projects/sessionflow")
    # snapshots datados: NUNCA devem ser transportados
    (out / "2026-11-03" / "graph.json").write_text("{\"velho\": true}" * 200)


# --------------------------------------------------------------------- testes


class AssinaturaTestCase(unittest.TestCase):
    """SigV4 contra os vetores oficiais — prova a correção sem tocar a rede."""

    def test_derive_signing_key_vetor_aws(self):
        k = gf.derive_signing_key(
            AWS_VECTOR["secret_key"], AWS_VECTOR["date"],
            AWS_VECTOR["region"], AWS_VECTOR["service"],
        )
        self.assertEqual(k.hex(), AWS_SIGNING_KEY)

    def test_assinatura_get_com_range_vetor_aws(self):
        """Reproduz o exemplo 'GET Object' do guia da AWS, byte a byte."""
        canon_headers = {
            "host": AWS_VECTOR["host"],
            "range": AWS_VECTOR["range"],
            "x-amz-content-sha256": AWS_VECTOR["payload_hash"],
            "x-amz-date": AWS_VECTOR["amz_date"],
        }
        signed_headers, signature = gf.calcular_assinatura(
            method="GET",
            canonical_uri=AWS_VECTOR["canonical_uri"],
            canonical_query="",
            headers=canon_headers,
            payload_hash=AWS_VECTOR["payload_hash"],
            amz_date=AWS_VECTOR["amz_date"],
            key=AWS_VECTOR["access_key"],
            secret=AWS_VECTOR["secret_key"],
            region=AWS_VECTOR["region"],
            service=AWS_VECTOR["service"],
            date=AWS_VECTOR["date"],
        )
        self.assertEqual(signed_headers, AWS_VECTOR["expected_signed_headers"])
        self.assertEqual(signature, AWS_VECTOR["expected_signature"])

    def test_uri_canonica_escapa_espacos_mas_preserva_barras(self):
        self.assertEqual(
            gf.uri_canonica("/graphify/sessionflow/graph.json"),
            "/graphify/sessionflow/graph.json",
        )
        self.assertEqual(gf.uri_canonica("/a b/c d.json"), "/a%20b/c%20d.json")


class ConfigTestCase(unittest.TestCase):
    """Precedência env > arquivo, e erro explícito quando falta tudo."""

    def test_env_tem_precedencia_sobre_arquivo(self):
        env = {
            "GRAPHIFY_S3_ENDPOINT": "https://env.test",
            "GRAPHIFY_S3_ACCESS_KEY": "ENVKEY",
            "GRAPHIFY_S3_SECRET_KEY": "ENVSECRET",
            "GRAPHIFY_S3_BUCKET": "envbucket",
        }
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "s3.env"
            p.write_text(
                "GRAPHIFY_S3_ENDPOINT=https://file.test\n"
                "GRAPHIFY_S3_ACCESS_KEY=FILEKEY\n"
                "GRAPHIFY_S3_SECRET_KEY=FILESECRET\n"
                "GRAPHIFY_S3_BUCKET=filebucket\n"
            )
            cfg = gf.carregar_config(env=env, arquivo=p)
        self.assertEqual(cfg["endpoint"], "https://env.test")
        self.assertEqual(cfg["access_key"], "ENVKEY")
        self.assertEqual(cfg["bucket"], "envbucket")

    def test_le_do_arquivo_quando_env_vazio(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "s3.env"
            p.write_text(
                "# comentario\n"
                "GRAPHIFY_S3_ENDPOINT=https://file.test\n"
                "GRAPHIFY_S3_ACCESS_KEY=FILEKEY\n"
                "GRAPHIFY_S3_SECRET_KEY=FILESECRET\n"
                "GRAPHIFY_S3_BUCKET=filebucket\n"
            )
            cfg = gf.carregar_config(env={}, arquivo=p)
        self.assertEqual(cfg["endpoint"], "https://file.test")
        self.assertEqual(cfg["secret_key"], "FILESECRET")

    def test_falha_com_mensagem_clara_sem_credencial(self):
        with tempfile.TemporaryDirectory() as td:
            vazio = Path(td) / "nao-existe.env"
            with self.assertRaises(SystemExit) as cm:
                gf.carregar_config(env={}, arquivo=vazio)
        msg = str(cm.exception.code)
        self.assertIn("GRAPHIFY_S3_ACCESS_KEY", msg)
        self.assertIn("MINIO", msg.upper())

    def test_prefixo_ausente_nao_e_inventado(self):
        """O namespace do balde NÃO deve vir da config global.

        Se o prefixo fosse fixo no arquivo de config, dois projetos publicados
        pela mesma máquina escreveriam no mesmo caminho e o último a rodar
        apagaria o artefato do outro.
        """
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "s3.env"
            p.write_text(
                "GRAPHIFY_S3_ENDPOINT=https://file.test\n"
                "GRAPHIFY_S3_ACCESS_KEY=K\n"
                "GRAPHIFY_S3_SECRET_KEY=S\n"
            )
            cfg = gf.carregar_config(env={}, arquivo=p)
        self.assertEqual(cfg["prefixo"], "")


class NamespaceTestCase(unittest.TestCase):
    """Cada projeto ganha seu próprio prefixo, derivado do nome da pasta."""

    def test_prefixo_padrao_e_o_nome_do_projeto(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "meu-projeto"
            root.mkdir()
            cfg = {"endpoint": "https://x", "access_key": "K", "secret_key": "S",
                   "bucket": "graphify", "region": "us-east-1", "prefixo": ""}
            self.assertEqual(gf.prefixo_efetivo(cfg, root), "meu-projeto")

    def test_prefixo_explicito_tem_precedencia(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td) / "ignorado"
            root.mkdir()
            cfg = {"endpoint": "https://x", "access_key": "K", "secret_key": "S",
                   "bucket": "graphify", "region": "us-east-1", "prefixo": "compartilhado"}
            self.assertEqual(gf.prefixo_efetivo(cfg, root), "compartilhado")

    def test_dois_projetos_nao_se_sobrescrevem_no_balde(self):
        with _FakeS3() as s3, tempfile.TemporaryDirectory() as td:
            cfg = s3.cfg(prefixo="")
            a = Path(td) / "proj-a"
            b = Path(td) / "proj-b"
            for r, marca in ((a, "A"), (b, "B")):
                (r / "graphify-out").mkdir(parents=True)
                (r / "graphify-out" / "graph.json").write_text('{"marca": "' + marca + '"}')

            gf.publicar(a, cfg, aws_date="20260101T000000Z")
            gf.publicar(b, cfg, aws_date="20260101T000000Z")

            self.assertIn("proj-a/graph.json", s3.objects)
            self.assertIn("proj-b/graph.json", s3.objects)
            self.assertIn(b'"A"', s3.objects["proj-a/graph.json"])
            self.assertIn(b'"B"', s3.objects["proj-b/graph.json"])


class EssenciaisTestCase(unittest.TestCase):
    """A seleção do que viaja é a decisão econômica do script."""

    def test_seleciona_so_os_essenciais(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _grafo_falso(root)
            chaves = sorted(gf.chaves_essenciais(root))
        self.assertEqual(
            chaves,
            sorted([
                ".graphify_analysis.json",
                ".graphify_labels.json",
                "cache/stat-index.json",
                "graph.json",
            ]),
        )

    def test_nunca_inclui_pasta_datada(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _grafo_falso(root)
            for k in gf.chaves_essenciais(root):
                self.assertFalse(k[:4].isdigit(), f"snapshot datado vazou: {k}")


class PublicarTestCase(unittest.TestCase):
    def test_publica_apenas_essenciais(self):
        with _FakeS3() as s3, tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _grafo_falso(root)
            enviados = gf.publicar(root, s3.cfg(), aws_date="20260101T000000Z")

            self.assertEqual(
                sorted(enviados),
                sorted([
                    "sessionflow/.graphify_analysis.json",
                    "sessionflow/.graphify_labels.json",
                    "sessionflow/cache/stat-index.json",
                    "sessionflow/graph.json",
                ]),
            )
            # nada de snapshot datado no balde
            self.assertFalse(any("2026-11-03" in k for k in s3.objects))

    def test_publica_manifesto_com_sha_e_origem(self):
        with _FakeS3() as s3, tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _grafo_falso(root)
            gf.publicar(root, s3.cfg(), aws_date="20260101T000000Z")

            manifesto = json.loads(s3.objects["sessionflow/PUBLISHED.json"])
            self.assertEqual(manifesto["origem"], "/Users/diegoaraujo/projects/sessionflow")
            self.assertIn("graph.json", manifesto["arquivos"])
            self.assertEqual(len(manifesto["arquivos"]["graph.json"]), 64)
            # assinou tudo (todo PUT traz Authorization SigV4)
            puts = [r for r in s3.requests if r["method"] == "PUT"]
            self.assertTrue(puts)
            for r in puts:
                self.assertIn(
                    "AWS4-HMAC-SHA256", r["headers"].get("Authorization", "")
                )


class BaixarTestCase(unittest.TestCase):
    def _semeia_remoto(self, s3, cfg):
        _root_remoto = "/Users/diegoaraujo/Documents/projects/sessionflow"
        dados = {
            "graph.json": '{"nodes": [{"id": "a"}], "links": [], "built_at_commit": "abc123"}',
            ".graphify_analysis.json": '{"origem": "mac"}',
            ".graphify_labels.json": '{"clusters": {}}',
            "cache/stat-index.json": '{"a.py": {"size": 1}}',
        }
        manifest = {"origem": _root_remoto, "commit": "abc123", "arquivos": {}}
        for k, v in dados.items():
            s3.objects[f"sessionflow/{k}"] = v.encode()
            manifest["arquivos"][k] = hashlib.sha256(v.encode()).hexdigest()
        s3.objects["sessionflow/PUBLISHED.json"] = json.dumps(manifest).encode()
        return dados

    def test_baixa_essenciais_para_o_repo_local(self):
        with _FakeS3() as s3, tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = s3.cfg()
            dados = self._semeia_remoto(s3, cfg)

            baixados = gf.baixar(root, cfg, aws_date="20260101T000000Z")

            self.assertEqual(len(baixados), 4)
            out = root / "graphify-out"
            for k, v in dados.items():
                self.assertEqual((out / k).read_text(), v, f"conteudo errado: {k}")

    def test_reescreve_graphify_root_para_o_root_local(self):
        """Sem isso o `graphify update .` reindexa a árvore inteira do zero."""
        with _FakeS3() as s3, tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = s3.cfg()
            self._semeia_remoto(s3, cfg)

            gf.baixar(root, cfg, aws_date="20260101T000000Z")

            marcador = (root / "graphify-out" / ".graphify_root").read_text().strip()
            self.assertEqual(marcador, str(root.resolve()))
            self.assertNotIn("/Users/diegoaraujo", marcador)

    def test_nao_apaga_snapshots_datados_locais(self):
        """Host pode ter snapshots próprios; o fetch é aditivo, não destrutivo."""
        with _FakeS3() as s3, tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _grafo_falso(root)
            cfg = s3.cfg()
            self._semeia_remoto(s3, cfg)
            preservado = root / "graphify-out" / "2026-11-03" / "graph.json"
            antes = preservado.read_text()

            gf.baixar(root, cfg, aws_date="20260101T000000Z")

            self.assertTrue(preservado.exists())
            self.assertEqual(preservado.read_text(), antes)

    def test_falha_alto_quando_objeto_nao_existe_no_balde(self):
        with _FakeS3() as s3, tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = s3.cfg()
            # balde vazio: nem manifesto existe
            with self.assertRaises(SystemExit) as cm:
                gf.baixar(root, cfg, aws_date="20260101T000000Z")
            self.assertIn("publica", str(cm.exception.code).lower())

    def test_aborta_se_sha_nao_bate_com_manifesto(self):
        """Objeto corrompido/truncado não pode virar artefato silenciosamente."""
        with _FakeS3() as s3, tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = s3.cfg()
            self._semeia_remoto(s3, cfg)
            s3.objects["sessionflow/graph.json"] = b'{"corrompido": true}'

            with self.assertRaises(SystemExit) as cm:
                gf.baixar(root, cfg, aws_date="20260101T000000Z")
            self.assertIn("sha", str(cm.exception.code).lower())


class EstadoTestCase(unittest.TestCase):
    def test_estado_reporta_ausente_quando_local_nao_tem_artefato(self):
        with _FakeS3() as s3, tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = s3.cfg()
            s3.objects["sessionflow/PUBLISHED.json"] = json.dumps(
                {"origem": "/x", "commit": "abc", "arquivos": {"graph.json": "0" * 64}}
            ).encode()
            rel = gf.estado(root, cfg, aws_date="20260101T000000Z")
        self.assertEqual(rel["situacao"], "ausente")

    def test_estado_reporta_em_dia_quando_sha_bate(self):
        with _FakeS3() as s3, tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = s3.cfg()
            _grafo_falso(root)
            sha = hashlib.sha256(
                (root / "graphify-out" / "graph.json").read_bytes()
            ).hexdigest()
            s3.objects["sessionflow/PUBLISHED.json"] = json.dumps(
                {"origem": "/x", "commit": "abc", "arquivos": {"graph.json": sha}}
            ).encode()
            rel = gf.estado(root, cfg, aws_date="20260101T000000Z")
        self.assertEqual(rel["situacao"], "em-dia")

    def test_estado_reporta_desatualizado_quando_sha_diverge(self):
        with _FakeS3() as s3, tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = s3.cfg()
            _grafo_falso(root)
            s3.objects["sessionflow/PUBLISHED.json"] = json.dumps(
                {"origem": "/x", "commit": "abc", "arquivos": {"graph.json": "f" * 64}}
            ).encode()
            rel = gf.estado(root, cfg, aws_date="20260101T000000Z")
        self.assertEqual(rel["situacao"], "desatualizado")


if __name__ == "__main__":
    unittest.main(verbosity=2)
