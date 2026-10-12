
import json
import os
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError


class S3CheckpointManager:
    """Store and retrieve CodePilot-AI checkpoints in Amazon S3."""

    def __init__(self, bucket_name=None, region=None):
        self.bucket_name = bucket_name or os.getenv(
            "CODEPILOT_S3_BUCKET", "codepilot-checkpoints"
        )
        self.region = region or os.getenv(
            "AWS_REGION", "ap-southeast-2"
        )
        self.s3 = boto3.client("s3", region_name=self.region)

    def save_checkpoint(self, task_id, files, metadata=None):
        """Save a JSON snapshot. files must map relative paths to text."""
        if not task_id or not files:
            raise ValueError("task_id and files are required")

        snapshot = {
            "task_id": task_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "files": files,
            "metadata": metadata or {},
        }

        key = f"checkpoints/{task_id}/{snapshot['created_at'].replace(':', '-')}.json"

        self.s3.put_object(
            Bucket=self.bucket_name,
            Key=key,
            Body=json.dumps(snapshot, indent=2).encode("utf-8"),
            ContentType="application/json",
        )

        return {"bucket": self.bucket_name, "key": key}

    def load_checkpoint(self, key):
        """Load a checkpoint using its S3 object key."""
        response = self.s3.get_object(
            Bucket=self.bucket_name,
            Key=key,
        )
        return json.loads(response["Body"].read().decode("utf-8"))
