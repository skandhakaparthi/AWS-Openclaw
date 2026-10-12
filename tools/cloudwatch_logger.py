
import logging
import os
import boto3
from datetime import datetime, timezone


class CloudWatchLogger:
    """Send CodePilot-AI logs to Amazon CloudWatch."""

    def __init__(self, log_group=None, region=None):
        self.log_group = log_group or os.getenv(
            "CODEPILOT_LOG_GROUP", "/codepilot/agent"
        )
        self.region = region or os.getenv(
            "AWS_REGION", "ap-southeast-2"
        )
        self.logs = boto3.client(
            "logs", region_name=self.region
        )
        self._ensure_log_group()
        self.log_stream = (
            "codepilot-" +
            datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        )
        self.logs.create_log_stream(
            logGroupName=self.log_group,
            logStreamName=self.log_stream,
        )

    def _ensure_log_group(self):
        groups = self.logs.describe_log_groups(
            logGroupNamePrefix=self.log_group
        ).get("logGroups", [])

        if not any(
            group["logGroupName"] == self.log_group
            for group in groups
        ):
            self.logs.create_log_group(
                logGroupName=self.log_group
            )

    def log_event(self, level, message, task_id=None):
        event = {
            "timestamp": int(
                datetime.now(timezone.utc).timestamp() * 1000
            ),
            "message": (
                f"level={level} task_id={task_id or '-'} "
                f"message={message}"
            ),
        }
        self.logs.put_log_events(
            logGroupName=self.log_group,
            logStreamName=self.log_stream,
            logEvents=[event],
        )
