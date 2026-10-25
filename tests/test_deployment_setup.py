"""Deployment packaging and identity checks that prevent broken or misbound live runs."""
import pytest

from demo.seed import parties
from services.agent.deploy import runtime_arn, stage_source


def test_staged_runtime_has_imports_and_policy_without_checkout_secrets(tmp_path):
    services = tmp_path / "services"
    for name, text in {
        "__init__.py": "",
        "agent/main.py": "app = None\n",
        "agent/requirements.txt": "strands-agents\n",
        "common/config.py": "REGION = 'us-east-1'\n",
        "agentsafe/policy.yaml": "version: 1\n",
        ".env": "SECRET=do-not-upload\n",
        "common/__pycache__/config.pyc": "cache",
    }.items():
        path = services / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (tmp_path / ".env").write_text("SECRET=checkout-secret\n")
    staged = stage_source(tmp_path)
    assert (staged / "services/common/config.py").is_file()
    assert (staged / "services/agentsafe/policy.yaml").is_file()
    assert "from services.agent.main import app" in (staged / "agent_entrypoint.py").read_text()
    assert not list(staged.rglob(".env"))
    assert not list(staged.rglob("*.pyc"))
    # Re-staging removes stale source while retaining the toolkit's deployment state.
    (staged / ".bedrock_agentcore.yaml").write_text("existing: state\n")
    (services / "common/config.py").unlink()
    stage_source(tmp_path)
    assert not (staged / "services/common/config.py").exists()
    assert (staged / ".bedrock_agentcore.yaml").read_text() == "existing: state\n"


def test_runtime_arn_must_come_from_complete_toolkit_state(tmp_path):
    config = tmp_path / ".bedrock_agentcore.yaml"
    prefix = "agents:\n  clasp_agent:\n    bedrock_agentcore:\n      agent_arn: "
    config.write_text(prefix + "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/clasp_agent-ABC123\n")
    assert runtime_arn(tmp_path).endswith("runtime/clasp_agent-ABC123")
    config.write_text(prefix + "null\n")
    with pytest.raises(ValueError, match="deployment is incomplete"):
        runtime_arn(tmp_path)


@pytest.fixture
def live_people(monkeypatch):
    monkeypatch.setenv("DEMO_BUYER_PHONE", "+15551234567")
    monkeypatch.setenv("DEMO_SELLER_PHONE", "+15557654321")
    monkeypatch.setenv("DEMO_BUYER2_PHONE", "")
    monkeypatch.setenv("DEMO_SUPERVISOR_PHONE", "")


def test_live_seed_allows_email_supervisor_and_omits_unused_buyer(live_people):
    people = parties(live=True)
    assert {p["party_id"] for p in people} == {"buyer_demo", "seller_demo", "supervisor_demo"}
    assert next(p for p in people if p["role"] == "supervisor")["phone"] is None


@pytest.mark.parametrize("buyer", ["", "+15550000001", "not-a-number"])
def test_live_seed_rejects_placeholder_identity(live_people, monkeypatch, buyer):
    monkeypatch.setenv("DEMO_BUYER_PHONE", buyer)
    with pytest.raises(ValueError, match="real verified E.164"):
        parties(live=True)


def test_live_seed_rejects_buyer_seller_phone_collision(live_people, monkeypatch):
    monkeypatch.setenv("DEMO_SELLER_PHONE", "+15551234567")
    with pytest.raises(ValueError, match="distinct phone"):
        parties(live=True)
