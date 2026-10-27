"""Environment-driven configuration. Reads .env locally; never in Lambda."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

_IN_LAMBDA = bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))


@lru_cache(maxsize=1)
def load_dotenv_once() -> bool:
    if _IN_LAMBDA or os.environ.get("CLASP_SKIP_DOTENV"):
        return False
    try:
        from dotenv import load_dotenv  # type: ignore
    except ImportError:  # pragma: no cover
        return False
    for candidate in (Path.cwd() / ".env", Path(__file__).resolve().parents[2] / ".env"):
        if candidate.exists():
            load_dotenv(candidate, override=False)
            return True
    return False


def env(name: str, default: str | None = None) -> str | None:
    load_dotenv_once()
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value


def env_int(name: str, default: int) -> int:
    raw = env(name)
    return int(raw) if raw is not None else default


def env_bool(name: str, default: bool = False) -> bool:
    raw = env(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


_ssm_hits: dict[str, str] = {}


def _ssm_cached(name: str) -> str | None:
    """Hits are cached for the process lifetime; misses are retried, so a parameter published by a later
    stack deploy is picked up without waiting for a cold start."""
    if name in _ssm_hits:
        return _ssm_hits[name]
    try:
        from .aws import client

        value = client("ssm").get_parameter(Name=name)["Parameter"]["Value"]
    except Exception:  # noqa: BLE001 — missing parameter or no credentials: fall back to defaults
        return None
    if value:
        _ssm_hits[name] = value
    return value or None


def _ssm(key: str) -> str | None:
    """Values other stacks publish to SSM to avoid cross-stack cycles (deployed only)."""
    prefix = env("SSM_PREFIX")
    if not prefix:
        return None
    return _ssm_cached(f"{prefix.rstrip('/')}/{key}")


class Settings:
    """Lazy accessors so tests can flip env vars between cases."""

    @property
    def region(self) -> str:
        return env("AWS_REGION", env("AWS_DEFAULT_REGION", "us-east-1")) or "us-east-1"

    @property
    def stage(self) -> str:
        return env("STAGE", "dev") or "dev"

    # data
    @property
    def store_mode(self) -> str:
        return env("STORE_MODE", "local") or "local"

    def table(self, logical: str) -> str:
        return env(f"{logical.upper()}_TABLE", f"clasp-{logical}") or f"clasp-{logical}"

    @property
    def media_bucket(self) -> str | None:
        return env("MEDIA_BUCKET")

    # trust core
    @property
    def kms_key_id(self) -> str | None:
        return env("KMS_SIGNING_KEY_ID")

    @property
    def policy_path(self) -> Path:
        raw = env("POLICY_PATH")
        if raw:
            p = Path(raw)
            return p if p.is_absolute() else repo_root() / p
        return Path(__file__).resolve().parents[1] / "agentsafe" / "policy.yaml"

    @property
    def authz_max_ttl(self) -> int:
        return min(env_int("AUTHZ_MAX_TTL_SECONDS", 120), 120)

    @property
    def agentsafe_url(self) -> str | None:
        return env("AGENTSAFE_URL")

    @property
    def agentsafe_function_arn(self) -> str | None:
        return env("AGENTSAFE_FUNCTION_ARN")

    @property
    def agentsafe_tenant_key(self) -> str | None:
        """Decionis hosted-gateway tenant key (AgentSafe-Tenant-Key). Never logged, never put in a URL."""
        return _secret("AGENTSAFE_TENANT_KEY")

    @property
    def agentsafe_upstream_token(self) -> str | None:
        """Bearer token the gateway relays to Clasp's POST /decide, so only governed calls reach the upstream."""
        return _secret("AGENTSAFE_UPSTREAM_TOKEN")

    @property
    def agentsafe_gateway_timeout(self) -> float:
        """Covers the authority's 4 s budget plus the upstream's answer through API Gateway."""
        return float(env("AGENTSAFE_GATEWAY_TIMEOUT", "15") or 15)

    @property
    def agentsafe_gateway_mode(self) -> str:
        """enforcement: the gateway is the only way to a decision; if it can't answer, nothing moves (fail closed).
        shadow: the gateway only observes, so the reference service may decide directly when it is unreachable."""
        return (env("AGENTSAFE_GATEWAY_MODE", "enforcement") or "enforcement").lower()

    @property
    def agentsafe_upstream_header(self) -> str:
        """The header the gateway's upstreamCredential sets on every relayed call (authorization: "Bearer …")."""
        return (env("AGENTSAFE_UPSTREAM_HEADER", "authorization") or "authorization").lower()

    @property
    def agentsafe_upstream_proof(self) -> str:
        """Token line(s) served at /.well-known/agentsafe-upstream (`make upstream-proof` computes one)."""
        return (env("AGENTSAFE_UPSTREAM_PROOF", "") or "").replace("\\n", "\n")

    @property
    def executor_function_arn(self) -> str | None:
        return env("EXECUTOR_FUNCTION_ARN")

    # workflow
    @property
    def workflow_mode(self) -> str:
        return env("WORKFLOW_MODE", "local") or "local"

    @property
    def state_machine_arn(self) -> str | None:
        return env("STATE_MACHINE_ARN")

    # channels
    @property
    def notify_mode(self) -> str:
        return env("NOTIFY_MODE", "console") or "console"

    # --- events + knowledge --------------------------------------------------------
    @property
    def events_mode(self) -> str:
        """eventbridge | console | memory | off"""
        return env("EVENTS_MODE", "console") or "console"

    @property
    def event_bus_name(self) -> str:
        return env("EVENT_BUS_NAME", f"clasp-{self.stage}") or f"clasp-{self.stage}"

    @property
    def knowledge_base_id(self) -> str | None:
        return env("KNOWLEDGE_BASE_ID") or _ssm("knowledge_base_id")

    @property
    def auto_enrol_channels(self) -> frozenset[str]:
        """Channels whose unknown senders are enrolled as buyers on first contact (judging / demo). Empty = reject."""
        raw = env("AUTO_ENROL_CHANNELS", "") or ""
        return frozenset(c.strip().lower() for c in raw.split(",") if c.strip())

    @property
    def whatsapp_phone_number_id(self) -> str | None:
        return env("WHATSAPP_PHONE_NUMBER_ID")

    @property
    def meta_api_version(self) -> str:
        return env("META_API_VERSION", "v20.0") or "v20.0"

    @property
    def rcs_pool_id(self) -> str | None:
        return env("RCS_POOL_ID")

    @property
    def sms_origination_identity(self) -> str | None:
        return env("SMS_ORIGINATION_IDENTITY") or env("RCS_POOL_ID")

    @property
    def ses_from_email(self) -> str:
        return env("SES_FROM_EMAIL", "clasp@example.com") or "clasp@example.com"

    @property
    def supervisor_email(self) -> str:
        return env("SUPERVISOR_EMAIL", "supervisor@example.com") or "supervisor@example.com"

    @property
    def api_base_url(self) -> str:
        return (env("API_BASE_URL") or _ssm("api_base_url") or "http://localhost:8080").rstrip("/")

    # identity
    @property
    def cognito_user_pool_id(self) -> str | None:
        return env("COGNITO_USER_POOL_ID")

    # agent
    @property
    def agent_mode(self) -> str:
        return env("AGENT_MODE", "local") or "local"

    @property
    def agent_runtime_arn(self) -> str | None:
        return env("AGENT_RUNTIME_ARN") or _ssm("agent_runtime_arn")

    @property
    def model_id(self) -> str:
        return env("BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0") or ""

    @property
    def vision_model_id(self) -> str:
        return env("BEDROCK_VISION_MODEL_ID", self.model_id) or self.model_id

    @property
    def video_model_id(self) -> str:
        """Short evidence videos need a video-capable Bedrock model (Amazon Nova)."""
        return env("BEDROCK_VIDEO_MODEL_ID", "us.amazon.nova-pro-v1:0") or "us.amazon.nova-pro-v1:0"

    @property
    def guardrail_id(self) -> str | None:
        return env("GUARDRAIL_ID")

    @property
    def guardrail_version(self) -> str:
        return env("GUARDRAIL_VERSION", "DRAFT") or "DRAFT"

    @property
    def memory_id(self) -> str | None:
        return env("MEMORY_ID")

    @property
    def gateway_url(self) -> str | None:
        return env("GATEWAY_URL")

    # payments
    @property
    def psp(self) -> str:
        return env("PSP", "mock") or "mock"

    @property
    def psp_webhook_secret(self) -> str:
        arn = env("PSP_WEBHOOK_SECRET_ARN")
        if arn:
            from .aws import client

            return client("secretsmanager").get_secret_value(SecretId=arn)["SecretString"]
        return env("PSP_WEBHOOK_SECRET", "change-me-local-only") or "change-me-local-only"

    @property
    def evidence_mode(self) -> str:
        return env("EVIDENCE_MODE", "stub") or "stub"

    # courier (simulated) and the buyer's way back to the chat
    @property
    def courier_webhook_secret(self) -> str:
        return _secret("COURIER_WEBHOOK_SECRET") or "change-me-local-courier"

    @property
    def courier_mode(self) -> str:
        """manual: a courier page records pickup with a parcel photo; auto: pickup is scanned as soon as funds are held."""
        return (env("COURIER_MODE", "manual") or "manual").lower()

    @property
    def whatsapp_business_number(self) -> str | None:
        """Display number (E.164) for wa.me links on hosted pages. Optional."""
        return env("WHATSAPP_BUSINESS_NUMBER")


_secrets: dict[str, str] = {}


def _secret(name: str) -> str | None:
    """`<NAME>_SECRET_ARN` (Secrets Manager, cached per container) or the plain `<NAME>` env var."""
    arn = env(f"{name}_SECRET_ARN")
    if arn:
        if arn not in _secrets:
            from .aws import client

            _secrets[arn] = client("secretsmanager").get_secret_value(SecretId=arn)["SecretString"]
        return _secrets[arn] or None
    return env(name)


settings = Settings()
