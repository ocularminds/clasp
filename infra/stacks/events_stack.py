"""Events: the `clasp-<stage>` EventBridge bus that every order lifecycle step publishes to.

Rules: all order events are written to a CloudWatch log group (the audit trail beside the AgentSafe decision
log) and archived for replay; delivery events (dispatch, delivery code, release) fan out to an SNS topic as the
integration point for courier and fulfilment systems. Publishers are the workflow task and tools Lambdas,
granted events:PutEvents on this bus by name in the Workflow stack; the executor never publishes."""
from __future__ import annotations

import sys
from pathlib import Path

from aws_cdk import Duration, RemovalPolicy, Stack
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_logs as logs
from aws_cdk import aws_sns as sns
from constructs import Construct

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from services.common.events import DELIVERY_EVENT_TYPES, SOURCE  # noqa: E402

from ..common import output, stage  # noqa: E402


class EventsStack(Stack):
    def __init__(self, scope: Construct, id_: str, **kwargs):
        super().__init__(scope, id_, **kwargs)
        self.bus = events.EventBus(self, "OrderBus", event_bus_name=f"clasp-{stage()}",
                                   description="Clasp order lifecycle events (source clasp.order)")
        events.Archive(self, "OrderArchive", source_event_bus=self.bus, archive_name=f"clasp-{stage()}-orders",
                       description="Replayable record of every order lifecycle event", retention=Duration.days(7),
                       event_pattern=events.EventPattern(source=[SOURCE]))

        self.log_group = logs.LogGroup(self, "OrderEventLog", log_group_name=f"/clasp/{stage()}/events",
                                       retention=logs.RetentionDays.TWO_WEEKS, removal_policy=RemovalPolicy.DESTROY)
        events.Rule(self, "AllOrderEventsToLogs", event_bus=self.bus, description="Audit trail of every order lifecycle event",
                    event_pattern=events.EventPattern(source=[SOURCE]), targets=[targets.CloudWatchLogGroup(self.log_group)])

        self.delivery_topic = sns.Topic(self, "DeliveryEvents", topic_name=f"clasp-{stage()}-delivery-events",
                                        display_name="Clasp delivery events")
        events.Rule(self, "DeliveryEventsToSns", event_bus=self.bus,
                    description="Dispatch, delivery-code and release events for courier / fulfilment integrations",
                    event_pattern=events.EventPattern(source=[SOURCE], detail_type=list(DELIVERY_EVENT_TYPES)),
                    targets=[targets.SnsTopic(self.delivery_topic)])

        output(self, "EVENT_BUS_NAME", self.bus.event_bus_name)
        output(self, "EVENT_BUS_ARN", self.bus.event_bus_arn)
        output(self, "DELIVERY_EVENTS_TOPIC_ARN", self.delivery_topic.topic_arn)
        output(self, "EVENTS_LOG_GROUP", self.log_group.log_group_name)
