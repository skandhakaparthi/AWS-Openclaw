
import os
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key


class DynamoDBStateManager:
    """Persist CodePilot-AI task status and execution history."""

    def __init__(self, table_name=None, region=None):
        self.table_name = table_name or os.getenv(
            "CODEPILOT_DYNAMODB_TABLE", "CodePilotTaskState"
        )
        self.region = region or os.getenv(
            "AWS_REGION", "ap-southeast-2"
        )
        self.dynamodb = boto3.resource(
            "dynamodb", region_name=self.region
        )
        self.table = self.dynamodb.Table(self.table_name)

    def create_task(self, task_id, status="CREATED"):
        now = datetime.now(timezone.utc).isoformat()
        item = {
            "task_id": task_id,
            "status": status,
            "iterations": 0,
            "estimated_cost": 0.0,
            "started_at": now,
            "history": [],
        }
        self.table.put_item(
            Item=item,
            ConditionExpression="attribute_not_exists(task_id)",
        )
        return item

    def update_task(self, task_id, status, iterations=None,
                    estimated_cost=None):
        names = {"#status": "status"}
        values = {":status": status}
        updates = ["#status = :status"]

        if iterations is not None:
            values[":iterations"] = iterations
            updates.append("iterations = :iterations")

        if estimated_cost is not None:
            values[":cost"] = estimated_cost
            updates.append("estimated_cost = :cost")

        if status in ("SUCCESS", "FAILED", "ROLLED_BACK"):
            names["#completed"] = "completed_at"
            values[":completed"] = datetime.now(
                timezone.utc
            ).isoformat()
            updates.append("#completed = :completed")

        self.table.update_item(
            Key={"task_id": task_id},
            UpdateExpression="SET " + ", ".join(updates),
            ExpressionAttributeNames=names,
            ExpressionAttributeValues=values,
            ConditionExpression="attribute_exists(task_id)",
        )

    def append_history(self, task_id, event):
        self.table.update_item(
            Key={"task_id": task_id},
            UpdateExpression="SET #history = list_append(#history, :event)",
            ExpressionAttributeNames={"#history": "history"},
            ExpressionAttributeValues={":event": [event]},
            ConditionExpression="attribute_exists(task_id)",
        )
