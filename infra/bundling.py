"""Lambda code asset: services/ + the tiny requirements-lambda.txt, bundled locally with uv or pip
(no Docker needed). Falls back to CDK's Docker bundling image if local bundling fails."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import jsii
from aws_cdk import BundlingOptions, ILocalBundling
from aws_cdk import aws_lambda as lam

ROOT = Path(__file__).resolve().parents[1]
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", "requirements.txt", ".DS_Store")


@jsii.implements(ILocalBundling)
class LocalPipBundler:
    def try_bundle(self, output_dir: str, options) -> bool:  # noqa: D401
        out = Path(output_dir)
        shutil.copytree(ROOT / "services", out / "services", ignore=IGNORE, dirs_exist_ok=True)
        req = str(ROOT / "requirements-lambda.txt")
        attempts = []
        if shutil.which("uv"):
            attempts.append(["uv", "pip", "install", "-q", "--python-version", "3.12", "--python-platform", "x86_64-manylinux2014",
                             "--only-binary", ":all:", "--target", str(out), "-r", req])
        attempts.append([sys.executable, "-m", "pip", "install", "-q", "--platform", "manylinux2014_x86_64", "--only-binary=:all:",
                         "--python-version", "3.12", "--implementation", "cp", "-t", str(out), "-r", req])
        attempts.append(["pip3", "install", "-q", "--platform", "manylinux2014_x86_64", "--only-binary=:all:", "--python-version", "3.12",
                         "--implementation", "cp", "-t", str(out), "-r", req])
        for cmd in attempts:
            try:
                subprocess.run(cmd, check=True, capture_output=True)
                return True
            except (subprocess.CalledProcessError, FileNotFoundError):
                continue
        return False


_code: dict[str, lam.Code] = {}


def lambda_code(scope) -> lam.Code:
    """One asset per stack (CDK forbids sharing a Code across stacks; the asset hash dedupes uploads)."""
    from aws_cdk import Stack

    key = Stack.of(scope).stack_name
    if key not in _code:
        _code[key] = lam.Code.from_asset(
            str(ROOT / "services"),
            exclude=["**/__pycache__", "*.pyc", "agent/requirements.txt"],
            bundling=BundlingOptions(
                image=lam.Runtime.PYTHON_3_12.bundling_image,
                command=["bash", "-c", "mkdir -p /asset-output/services && cp -r /asset-input/. /asset-output/services/ "
                                       "&& pip install -q pyyaml -t /asset-output"],
                local=LocalPipBundler(),
            ),
        )
    return _code[key]
