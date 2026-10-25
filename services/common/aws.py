"""Cached boto3 clients. Region comes from settings; tests run under moto."""
from __future__ import annotations

from functools import lru_cache

import boto3

from .config import settings


@lru_cache(maxsize=32)
def client(service: str, region: str | None = None):
    return boto3.client(service, region_name=region or settings.region)


@lru_cache(maxsize=8)
def resource(service: str, region: str | None = None):
    return boto3.resource(service, region_name=region or settings.region)


def reset_cache() -> None:
    client.cache_clear()
    resource.cache_clear()
