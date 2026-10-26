"""Media storage: S3 when MEDIA_BUCKET is set, demo/state/media otherwise.
Returned URLs are presigned (S3) or file:// (local); both are short-lived by intent."""
from __future__ import annotations

from pathlib import Path

from .config import repo_root, settings


class MediaStore:
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str: ...
    def get(self, key: str) -> bytes: ...
    def url(self, key: str, expires: int = 3600) -> str: ...
    def exists(self, key: str) -> bool: ...


class S3Media(MediaStore):
    def __init__(self, bucket: str, s3=None):
        from .aws import client

        self.bucket = bucket
        self._s3 = s3 or client("s3")

    def put(self, key, data, content_type="application/octet-stream"):
        self._s3.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        return key

    def get(self, key):
        return self._s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def url(self, key, expires=3600):
        return self._s3.generate_presigned_url("get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=expires)

    def exists(self, key):
        try:
            self._s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:  # noqa: BLE001
            return False

    def copy_from_presigned_target(self, key: str, content_type: str = "application/octet-stream") -> dict:
        """Presigned PUT used by GetWhatsAppMessageMedia(destinationS3PresignedUrl)."""
        url = self._s3.generate_presigned_url(
            "put_object", Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type}, ExpiresIn=600,
        )
        return {"url": url, "headers": {"Content-Type": content_type}}


class LocalMedia(MediaStore):
    def __init__(self, root: Path | None = None):
        self.root = root or repo_root() / "demo" / "state" / "media"

    def _p(self, key: str) -> Path:
        p = self.root / key
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def put(self, key, data, content_type="application/octet-stream"):
        self._p(key).write_bytes(data)
        return key

    def get(self, key):
        return self._p(key).read_bytes()

    def url(self, key, expires=3600):
        return self._p(key).resolve().as_uri()

    def exists(self, key):
        return self._p(key).exists()


_media: MediaStore | None = None


def get_media() -> MediaStore:
    global _media
    if _media is None:
        _media = S3Media(settings.media_bucket) if settings.media_bucket else LocalMedia()
    return _media


def set_media(m: MediaStore | None) -> None:
    global _media
    _media = m
