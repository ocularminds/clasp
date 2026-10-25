"""AWS SDK contracts and the RCS sandbox's separately verified SMS fallback."""
import boto3
import pytest
from botocore.stub import ANY, Stubber

from services.notify import router
from services.notify.router import AwsNotifier, Message


@pytest.fixture
def messaging(aws, monkeypatch):
    monkeypatch.setenv("RCS_POOL_ID", "pool-00000000000000000000000000000000")
    monkeypatch.setenv("SMS_ORIGINATION_IDENTITY", "CLASP")
    client = boto3.client("pinpoint-sms-voice-v2", region_name="us-east-1")
    notifier = AwsNotifier()
    notifier._client = lambda name: client
    events = []
    monkeypatch.setattr(router.log, "info", lambda event, **fields: events.append({"event": event, **fields}))
    with Stubber(client) as stub:
        yield notifier, client, stub, events
        stub.assert_no_pending_responses()


def sample_message():
    return Message(channel="rcs", to="+15551234567", text="Clasp: your order is held.",
                   fallback_text="Clasp: your order is held. Continue in WhatsApp.", correlation_id="notify-test",
                   rich_card={"title": "Order held", "description": "Upload a dispatch photo.",
                              "suggestions": [{"text": "Upload dispatch photo", "postback": "dispatch:ord-test"}]})


def reject_rcs(stub, reason):
    stub.add_client_error("send_rcs_message", service_error_code="ConflictException", http_status_code=409,
                          modeled_fields={"Reason": reason})


def test_rich_card_uses_the_current_sdk_contract(messaging):
    notifier, client, stub, events = messaging
    stub.add_response("send_rcs_message", {"MessageId": "rcs-message"}, {
        "DestinationPhoneNumber": "+15551234567", "OriginationIdentity": ANY,
        "MessageTrafficType": "TRANSACTION",
        "RcsMessageContent": {"Content": {"RichCard": {
            "CardOrientation": "VERTICAL", "CardContent": {
                "Title": "Order held", "Description": "Upload a dispatch photo.",
                "Suggestions": [{"Reply": {"Text": "Upload dispatch photo", "PostbackData": "dispatch:ord-test"}}],
            },
        }}},
        "FallbackConfiguration": {"Channel": "SMS", "MessageBody": sample_message().fallback_text},
        "Context": {"correlation_id": "notify-test"},
    })
    assert notifier.send(sample_message()) == "rcs-message"
    assert events[-1]["channel"] == "rcs"


def test_unverified_rcs_destination_uses_separately_verified_sms(messaging):
    notifier, client, stub, events = messaging
    reject_rcs(stub, "DESTINATION_PHONE_NUMBER_NOT_VERIFIED")
    stub.add_response("send_text_message", {"MessageId": "sms-message"}, {
        "DestinationPhoneNumber": "+15551234567", "OriginationIdentity": "CLASP",
        "MessageBody": sample_message().fallback_text, "MessageType": "TRANSACTIONAL",
        "Context": {"correlation_id": "notify-test"},
    })
    message = sample_message()
    assert notifier.send(message) == "sms-message"
    assert message.channel == "rcs"
    assert events[-1]["channel"] == "sms"
    assert events[-1]["requested_channel"] == "rcs"


def test_rcs_opt_out_does_not_attempt_sms(messaging):
    notifier, client, stub, events = messaging
    reject_rcs(stub, "DESTINATION_PHONE_NUMBER_OPTED_OUT")
    with pytest.raises(client.exceptions.ConflictException):
        notifier.send(sample_message())
    assert not events


def test_sms_verification_rejection_propagates(messaging):
    notifier, client, stub, events = messaging
    reject_rcs(stub, "DESTINATION_PHONE_NUMBER_NOT_VERIFIED")
    stub.add_client_error("send_text_message", service_error_code="ConflictException", http_status_code=409,
                          modeled_fields={"Reason": "DESTINATION_PHONE_NUMBER_NOT_VERIFIED"})
    with pytest.raises(client.exceptions.ConflictException):
        notifier.send(sample_message())
    assert not any(event["event"] == "notify.sent" for event in events)
