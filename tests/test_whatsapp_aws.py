"""Exercise WhatsApp's boto3 operations and preserve E.164 destinations offline."""
import json

import boto3
import pytest
from botocore.stub import Stubber

from services.common import aws as aws_clients
from services.common.media import LocalMedia
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


def test_whatsapp_photo_uses_the_sdk_download_operation(social, monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_BUCKET", "clasp-test-media")
    monkeypatch.setattr(aws_clients, "client", lambda name: social)
    monkeypatch.setattr(dispatch, "get_media", lambda: LocalMedia(tmp_path))
    inbound = Inbound(channel="whatsapp", sender="+15551234567", to=None, text="Dispatch photo",
                      media=[{"id": "media-123", "mime": "image/jpeg", "kind": "image"}])
    key = "inbound/15551234567/media-123"
    with Stubber(social) as stub:
        stub.add_response("get_whatsapp_message_media", {"mimeType": "image/jpeg", "fileSize": 1024}, {
            "mediaId": "media-123", "originationPhoneNumberId": PHONE_ID,
            "destinationS3File": {"bucketName": "clasp-test-media", "key": key},
        })
        assert dispatch.fetch_whatsapp_media(inbound) == [key]
        stub.assert_no_pending_responses()
