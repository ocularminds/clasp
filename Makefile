# Clasp — make targets. Every target reads .env when present.
PY        := .venv/bin/python
UV        := uv
STAGE     ?= dev
export JSII_SILENCE_WARNING_UNTESTED_NODE_VERSION=1
-include .env
export

.PHONY: help venv deps test lint synth bootstrap deploy destroy seed simulate logs decisions agent-deploy agent-local api-local clean

help:
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

venv:            ## Create the Python 3.12 virtualenv with uv
	$(UV) venv --python 3.12 .venv

deps: venv       ## Install dev + agent dependencies into .venv
	$(UV) pip install --python $(PY) -e ".[dev]"
	$(UV) pip install --python $(PY) -e ".[agent]" || echo "agent extras skipped (install later with: make deps-agent)"

deps-agent:      ## Install only the Strands / AgentCore extras
	$(UV) pip install --python $(PY) -e ".[agent]"

test:            ## Run the test suite (trust core first)
	$(PY) -m pytest -q tests

lint:            ## Byte-compile everything (cheap sanity check)
	$(PY) -m compileall -q services infra demo tests

synth:           ## cdk synth all stacks
	cd infra && ../$(PY) -m cdk_app synth >/dev/null && echo "synth ok (infra/cdk.out)"

bootstrap:       ## cdk bootstrap the target account/region
	cd infra && npx --yes cdk@2 bootstrap --app "../$(PY) app.py"

deploy:          ## Deploy every CDK stack, then write outputs into .env
	cd infra && npx --yes cdk@2 deploy --all --require-approval never --outputs-file cdk-outputs.json --app "../$(PY) app.py"
	$(PY) infra/write_env.py infra/cdk-outputs.json .env

destroy:         ## Tear everything down
	cd infra && npx --yes cdk@2 destroy --all --force --app "../$(PY) app.py"

agent-deploy:    ## Deploy the Strands agent to AgentCore Runtime with the starter toolkit
	$(PY) -m services.agent.deploy

agent-local:     ## Run the agent entrypoint locally on :8080 (AgentCore dev loop)
	$(PY) -m services.agent.main

api-local:       ## Run the local HTTP shim (hosted checkout, webhook, approvals) on :8080
	$(PY) -m demo.local_api

seed:            ## Seed items, parties and Cognito users
	$(PY) demo/seed.py

simulate:        ## Drive a full order (buy → hold → deliver → Truce → escalate) with no phones
	$(PY) demo/simulate.py $(ARGS)

logs:            ## Tail Lambda logs for the stage
	aws logs tail /aws/lambda/clasp-$(STAGE)-tools --follow --since 10m

decisions:       ## Tail the AgentSafe decision log (the final demo shot)
	$(PY) demo/decisions.py --follow

clean:
	rm -rf .pytest_cache infra/cdk.out demo/state demo/outbox.jsonl
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
