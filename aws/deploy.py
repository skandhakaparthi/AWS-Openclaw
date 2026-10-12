
import argparse
import json
import os
from pathlib import Path

import boto3
from botocore.exceptions import ClientError


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "aws_services_config.json"


def load_config():
    """Load AWS configuration, including UTF-8 BOM files."""
    with CONFIG_PATH.open("r", encoding="utf-8-sig") as file:
        return json.load(file)


def error_code(exc):
    return str(exc.response.get("Error", {}).get("Code", ""))


def resource_exists_s3(s3, bucket):
    """Return True/False when existence can be determined."""
    try:
        s3.head_bucket(Bucket=bucket)
        return True
    except ClientError as exc:
        code = error_code(exc)
        status = exc.response.get(
            "ResponseMetadata", {}
        ).get("HTTPStatusCode")

        if code in ("404", "NoSuchBucket", "NotFound") or status == 404:
            return False

        raise RuntimeError(
            f"Cannot verify S3 bucket '{bucket}' "
            f"(AWS error: {code}). Check permissions and bucket ownership."
        ) from exc


def ensure_s3_bucket(s3, bucket, region, apply):
    if resource_exists_s3(s3, bucket):
        print(f"[EXISTS] S3 bucket: {bucket}")
        return

    print(f"[MISSING] S3 bucket: {bucket}")

    if not apply:
        print("         Dry run: would create bucket.")
        return

    # S3 bucket names are globally unique.
    params = {"Bucket": bucket}
    if region != "us-east-1":
        params["CreateBucketConfiguration"] = {
            "LocationConstraint": region
        }

    s3.create_bucket(**params)

    # Keep the bucket private.
    s3.put_public_access_block(
        Bucket=bucket,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )

    # Enable encryption at rest using S3-managed keys.
    s3.put_bucket_encryption(
        Bucket=bucket,
        ServerSideEncryptionConfiguration={
            "Rules": [
                {
                    "ApplyServerSideEncryptionByDefault": {
                        "SSEAlgorithm": "AES256"
                    }
                }
            ]
        },
    )

    print(f"[CREATED] S3 bucket: {bucket}")


def ensure_dynamodb_table(dynamodb, table_name, apply):
    try:
        response = dynamodb.describe_table(TableName=table_name)
        table = response["Table"]
        print(
            f"[EXISTS] DynamoDB table: {table_name} "
            f"({table['TableStatus']})"
        )
        return
    except ClientError as exc:
        if error_code(exc) != "ResourceNotFoundException":
            raise

    print(f"[MISSING] DynamoDB table: {table_name}")

    if not apply:
        print("         Dry run: would create table.")
        return

    dynamodb.create_table(
        TableName=table_name,
        AttributeDefinitions=[
            {"AttributeName": "task_id", "AttributeType": "S"}
        ],
        KeySchema=[
            {"AttributeName": "task_id", "KeyType": "HASH"}
        ],
        BillingMode="PAY_PER_REQUEST",
    )

    dynamodb.get_waiter("table_exists").wait(
        TableName=table_name
    )
    print(f"[CREATED] DynamoDB table: {table_name}")


def ensure_log_group(logs, log_group, apply):
    response = logs.describe_log_groups(
        logGroupNamePrefix=log_group
    )
    exists = any(
        item["logGroupName"] == log_group
        for item in response.get("logGroups", [])
    )

    if exists:
        print(f"[EXISTS] CloudWatch log group: {log_group}")
        return

    print(f"[MISSING] CloudWatch log group: {log_group}")

    if not apply:
        print("         Dry run: would create log group.")
        return

    logs.create_log_group(logGroupName=log_group)
    print(f"[CREATED] CloudWatch log group: {log_group}")


def main():
    parser = argparse.ArgumentParser(
        description="Prepare CodePilot-AI AWS storage and logging resources."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Create missing S3, DynamoDB, and CloudWatch resources.",
    )
    args = parser.parse_args()

    config = load_config()
    region = os.getenv("AWS_REGION", "ap-southeast-2")

    session = boto3.Session(region_name=region)
    s3 = session.client("s3")
    dynamodb = session.client("dynamodb")
    logs = session.client("logs")

    print("=" * 60)
    print(f"Project: {config.get('project', 'CodePilot-AI')}")
    print(f"Region:  {region}")
    print(f"Mode:    {'APPLY' if args.apply else 'DRY RUN'}")
    print("=" * 60)

    ensure_s3_bucket(
        s3,
        config["s3"]["bucket_name"],
        region,
        args.apply,
    )

    ensure_dynamodb_table(
        dynamodb,
        config["dynamodb"]["table_name"],
        args.apply,
    )

    ensure_log_group(
        logs,
        config["cloudwatch"]["log_group"],
        args.apply,
    )

    print("=" * 60)
    print("Resource preparation finished.")

    if not args.apply:
        print("Dry run only: no resources were created.")
        print("Review the output before using --apply.")


if __name__ == "__main__":
    main()
