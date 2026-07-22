"""Application settings loaded from environment / .env.

The app runs inside a Docker container at runtime, so by default it reads
``MONGO_URI`` / ``RABBITMQ_URI`` which point at the docker service names
(``mongo`` / ``rabbitmq``). Tests run on the host, where those service names
do not resolve; for that case the host-facing variants ``MONGO_URI_HOST`` /
``RABBITMQ_URI_HOST`` (which use ``127.0.0.1``) are provided. A test fixture
can flip selection by setting ``SESSIONFLOW_USE_HOST_URIS=1``.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Container-facing URIs (default runtime).
    mongo_uri: str = "mongodb://localhost:27017"
    rabbitmq_uri: str = "amqp://localhost:5672/"

    # Host-facing URIs (for tests / host-run processes).
    mongo_uri_host: str | None = None
    rabbitmq_uri_host: str | None = None

    mongo_db: str = "sessionflow"

    # Collection holding session documents. Configurable so tests can inject
    # an isolated collection (e.g. ``sessions_test_<uuid>``).
    sessions_collection: str = "sessions"

    # Collection holding scanned host directories (written by the Worker).
    # Configurable so tests can inject an isolated collection
    # (e.g. ``host_directories_test_<uuid>``).
    host_directories_collection: str = "host_directories"

    # Collection holding the REAL per-agent model lists discovered on the host
    # (written by the Worker's model_discovery). Configurable so tests can
    # inject an isolated collection (e.g. ``host_models_test_<uuid>``).
    models_collection: str = "host_models"

    # Read-only collections written by the Worker (D5). Each is configurable so
    # tests can inject an isolated collection within ``sessionflow``.
    output_collection: str = "session_output"
    # Collection holding the live screen mirror (one upserted doc per session,
    # written by the Worker via capture-pane). Configurable for isolated tests.
    screen_collection: str = "session_screen"
    events_collection: str = "events"
    tasks_collection: str = "tasks"
    notifications_collection: str = "events"

    # Collection holding scheduled recurring commands (comandos programados) —
    # instrução + intervalo enviada periodicamente ao terminal de uma sessão.
    # Configurável para tests isolarem (ex.: ``scheduled_commands_test_<uuid>``).
    scheduled_commands_collection: str = "scheduled_commands"
    # Intervalo (s) do loop que varre comandos programados vencidos.
    scheduler_poll_seconds: int = 20

    # Intervalo (s) entre revisões automáticas de milestones por sessão ativa.
    # Mecanismo PRÓPRIO e independente de `scheduled_commands` (não aparece no
    # painel "Comandos programados" do usuário nem compete por essa coleção).
    milestones_refresh_interval_seconds: int = 14400  # 4h

    # Directory where uploaded files (e.g. audio) are stored. Mounted as a
    # volume in the container (env ``UPLOADS_DIR``). Tests inject ``tmp_path``.
    uploads_dir: str = "/data/uploads"

    # Collection holding upload metadata documents. Configurable so tests can
    # inject an isolated collection (e.g. ``uploads_test_<uuid>``).
    uploads_collection: str = "uploads"

    # Collection holding arquivos que o AGENTE compartilha de volta com o
    # usuário (sentido inverso do upload acima — ver ``sf share``).
    shared_files_collection: str = "shared_files"

    # When true, prefer the *_HOST URIs (used by tests on the host).
    use_host_uris: bool = False

    # --- Auth (single-user password -> JWT, plus WebAuthn) ---
    # The single account's credentials. Compared on /auth/login.
    # validation_alias uses AliasChoices so the field is populatable both by the
    # SESSIONFLOW_* env var AND by its plain field name (tests inject by name).
    auth_email: str = Field(
        default="", validation_alias=AliasChoices("SESSIONFLOW_EMAIL", "auth_email")
    )
    auth_password: str = Field(
        default="", validation_alias=AliasChoices("SESSIONFLOW_PASSWORD", "auth_password")
    )
    # HS256 signing secret for the session JWT.
    jwt_secret: str = Field(
        default="dev-insecure-secret",
        validation_alias=AliasChoices("SESSIONFLOW_JWT_SECRET", "jwt_secret"),
    )
    # JWT lifetime in seconds (default 7 days).
    jwt_ttl_seconds: int = 604800
    # WebAuthn relying-party config (front-end domain).
    rp_id: str = Field(
        default="localhost", validation_alias=AliasChoices("SESSIONFLOW_RP_ID", "rp_id")
    )
    rp_origin: str = Field(
        default="http://localhost",
        validation_alias=AliasChoices("SESSIONFLOW_RP_ORIGIN", "rp_origin"),
    )
    rp_name: str = "SessionFlow"
    # Collection holding registered WebAuthn credentials. Configurable so tests
    # can inject an isolated collection (e.g. ``webauthn_credentials_test_<uuid>``).
    webauthn_collection: str = "webauthn_credentials"

    # Web Push (VAPID). A API serve a chave PÚBLICA (browser subscribe) e guarda
    # as subscrições; quem ASSINA/envia o push é o Worker (tem a privada).
    vapid_public: str = Field(
        default="", validation_alias=AliasChoices("SESSIONFLOW_VAPID_PUBLIC", "vapid_public")
    )
    push_subscriptions_collection: str = "push_subscriptions"
    # Config geral do app (single-user), ex.: auto-instruir marcos nas sessões.
    app_settings_collection: str = "app_settings"

    # Token compartilhado que protege o webhook inbound do JARVIS
    # (``POST /jarvis/webhook``). O hook do JARVIS (host) envia no header
    # ``X-Jarvis-Token``. Vazio = endpoint desabilitado (rejeita tudo).
    jarvis_token: str = Field(
        default="",
        validation_alias=AliasChoices("SESSIONFLOW_JARVIS_TOKEN", "jarvis_token"),
    )

    # SHA curto do commit deployado — build arg do Dockerfile (git rev-parse
    # --short HEAD no host, no momento do `docker compose build`). "unknown"
    # fora de um build via compose (ex.: rodando testes no host).
    git_sha: str = Field(
        default="unknown",
        validation_alias=AliasChoices("SESSIONFLOW_GIT_SHA", "git_sha"),
    )

    # Versão "humana" — <épico>.<data do commit AAAAMMDD>.<hora HHMM>, ex.:
    # "1.20260722.1213". Build arg calculado a partir da data do commit HEAD
    # (não do momento do build): mesmo commit -> sempre a mesma versão, mesmo
    # rebuildando sem mudança de código.
    release_version: str = Field(
        default="unknown",
        validation_alias=AliasChoices("SESSIONFLOW_RELEASE_VERSION", "release_version"),
    )

    # CORS origins allowed for the front-end.
    cors_origins: list[str] = [
        "https://sessionflow.boletoazap.dev.br",
        "http://localhost:4200",
        "http://127.0.0.1:4200",
    ]

    @property
    def effective_mongo_uri(self) -> str:
        if self.use_host_uris and self.mongo_uri_host:
            return self.mongo_uri_host
        return self.mongo_uri

    @property
    def effective_rabbitmq_uri(self) -> str:
        if self.use_host_uris and self.rabbitmq_uri_host:
            return self.rabbitmq_uri_host
        return self.rabbitmq_uri


@lru_cache
def get_settings() -> Settings:
    return Settings()
