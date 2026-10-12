
import json
import os
import time
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

ROOT = Path(__file__).resolve().parent
PACKAGE = ROOT / "lambda_package.zip"
REGION = os.getenv("AWS_REGION", "ap-southeast-2")
ROLE_NAME = "CodePilotLambdaExecutionRole"

FUNCTIONS = {
    "codepilot-planner": "handlers.planner_handler",
    "codepilot-observer": "handlers.observer_handler",
    "codepilot-error-analyzer": "handlers.error_analyzer_handler",
    "codepilot-rollback": "handlers.rollback_handler",
}


def main():
    if not PACKAGE.exists():
        raise FileNotFoundError(
            f"Missing {PACKAGE}. Create lambda_package.zip first."
        )

    with zipfile.ZipFile(PACKAGE) as archive:
        if "handlers.py" not in archive.namelist():
            raise ValueError("ZIP must contain handlers.py at its root.")

    iam = boto3.client("iam")
    lam = boto3.client("lambda", region_name=REGION)

    role = iam.get_role(RoleName=ROLE_NAME)["Role"]["Arn"]
    account_id = boto3.client(
        "sts", region_name=REGION
    ).get_caller_identity()["Account"]

    zip_bytes = PACKAGE.read_bytes()

    print(f"Region: {REGION}")
    print(f"Execution role: {ROLE_NAME}")

    # IAM role propagation can take a short time.
    print("Waiting briefly for IAM role propagation...")
    time.sleep(10)

    for name, handler in FUNCTIONS.items():
        try:
            lam.get_function(FunctionName=name)
            exists = True
        except lam.exceptions.ResourceNotFoundException:
            exists = False

        if exists:
            print(f"[UPDATE] {name}")
            lam.update_function_code(
                FunctionName=name,
                ZipFile=zip_bytes,
                Publish=False,
            )
            lam.get_waiter("function_updated_v2").wait(
                FunctionName=name
            )
            lam.update_function_configuration(
                FunctionName=name,
                Role=role,
                Handler=handler,
                Runtime="python3.12",
                Timeout=30,
                MemorySize=128,
            )
        else:
            print(f"[CREATE] {name}")
            try:
                lam.create_function(
                    FunctionName=name,
                    Runtime="python3.12",
                    Role=role,
                    Handler=handler,
                    Code={"ZipFile": zip_bytes},
                    Timeout=30,
                    MemorySize=128,
                    Publish=False,
                )
            except ClientError as exc:
                print(
                    f"[ERROR] {name}: "
                    f"{exc.response['Error'].get('Message', exc)}"
                )
                raise

        print(f"[READY] {name}")

    print("\nLambda deployment complete.")
    print(f"Account: {account_id}")
    print("Next: connect the state machine to these function ARNs.")


if __name__ == "__main__":
    main()
