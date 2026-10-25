"""Exercise WhatsApp's boto3 operations and preserve E.164 destinations offline."""
import json
from urllib.parse import unquote, urlsplit

import boto3
import pytest
from botocore.stub import ANY, Stubber

from services.common import aws as aws_clients
from services.common.media import S3Media
from services.ingress import dispatch
from services.ingress.normalize import Inbound
from services.notify.router import AwsNotifier, Message


PHONE_ID = "phone-number-id-00000000000000000000000000000000"


@pytest.fixture
def social(aws, monkeypatch):
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", PHONE_ID)
    monkeypatch.setenv("META_API_VERSION", "v20.0")
    return boto3.client("socialmessaging", region_name="us-east-1")


def test_whatsapp_reply_preserves_e164_and_uses_the_sdk_send_operation(social):
    notifier = AwsNotifier()
    notifier._client = lambda name: social
    expected_payload = {
        "messaging_product": "whatsapp", "recipient_type": "individual",
        "to": "+15551234567", "type": "text",
        "text": {"preview_url": False, "body": "The Zoom H6 is available."},
    }
    with Stubber(social) as stub:
        stub.add_response("send_whatsapp_message", {"messageId": "wamid.test"}, {
            "originationPhoneNumberId": PHONE_ID, "metaApiVersion": "v20.0",
            "message": json.dumps(expected_payload).encode("utf-8"),
        })
        assert notifier.send(Message(channel="whatsapp", to="+15551234567",
                                     text="The Zoom H6 is available.")) == "wamid.test"
        stub.assert_no_pending_responses()


def test_whatsapp_photo_download_targets_the_exact_returned_s3_key(social, monkeypatch):
    monkeypatch.setenv("MEDIA_BUCKET", "clasp-test-media")
    media = S3Media("clasp-test-media", s3=boto3.client("s3", region_name="us-east-1"))
    monkeypatch.setattr(aws_clients, "client", lambda name: social)
    monkeypatch.setattr(dispatch, "get_media", lambda: media)
    inbound = Inbound(channel="whatsapp", sender="+15551234567", to=None, text="Dispatch photo",
                      media=[{"id": "123456789", "mime": "image/jpeg", "kind": "image"}])
    captured = {}
    download = social.get_whatsapp_message_media

    def capture_download(**kwargs):
        captured.update(kwargs)
        return download(**kwargs)

    monkeypatch.setattr(social, "get_whatsapp_message_media", capture_download)
    with Stubber(social) as stub:
        stub.add_response("get_whatsapp_message_media", {"mimeType": "image/jpeg", "fileSize": 1024}, {
            "mediaId": "123456789", "originationPhoneNumberId": PHONE_ID,
            "destinationS3PresignedUrl": {"url": ANY, "headers": {}},
        })
        keys = dispatch.fetch_whatsapp_media(inbound)
        stub.assert_no_pending_responses()
    target = urlsplit(captured["destinationS3PresignedUrl"]["url"])
    assert target.scheme == "https"
    assert target.hostname == "clasp-test-media.s3.amazonaws.com"
    assert keys == ["inbound/15551234567/123456789"]
    assert unquote(target.path).lstrip("/") == keys[0]
