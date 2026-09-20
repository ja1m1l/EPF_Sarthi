import json
import os
import time
from datetime import date, timedelta
from unittest.mock import patch
import uuid
import sys

# Setup moto before importing boto3
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_SECURITY_TOKEN"] = "testing"
os.environ["AWS_SESSION_TOKEN"] = "testing"
os.environ["AWS_DEFAULT_REGION"] = "ap-south-1"

from moto import mock_aws
import boto3

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from shared.models import Claim, ClaimStatus, ClaimType, AnalysisRun

@mock_aws
def simulate():
    print("Setting up mocked AWS environment...")
    dynamodb = boto3.resource("dynamodb", region_name="ap-south-1")
    sns = boto3.client("sns", region_name="ap-south-1")
    cloudwatch = boto3.client("cloudwatch", region_name="ap-south-1")

    # Create tables
    dynamodb.create_table(
        TableName="epf-sentinel-Claims-dev",
        KeySchema=[{"AttributeName": "userId", "KeyType": "HASH"}, {"AttributeName": "claimId", "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": "userId", "AttributeType": "S"}, {"AttributeName": "claimId", "AttributeType": "S"}, {"AttributeName": "status", "AttributeType": "S"}],
        GlobalSecondaryIndexes=[{
            "IndexName": "byStatus",
            "KeySchema": [{"AttributeName": "userId", "KeyType": "HASH"}, {"AttributeName": "status", "KeyType": "RANGE"}],
            "Projection": {"ProjectionType": "ALL"}
        }],
        BillingMode="PAY_PER_REQUEST"
    )
    
    dynamodb.create_table(
        TableName="epf-sentinel-AnalysisRuns-dev",
        KeySchema=[{"AttributeName": "claimId", "KeyType": "HASH"}, {"AttributeName": "runId", "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": "claimId", "AttributeType": "S"}, {"AttributeName": "runId", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST"
    )

    dynamodb.create_table(
        TableName="epf-sentinel-StatusTransitions-dev",
        KeySchema=[{"AttributeName": "claimId", "KeyType": "HASH"}, {"AttributeName": "transitionKey", "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": "claimId", "AttributeType": "S"}, {"AttributeName": "transitionKey", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST"
    )

    # Create SNS Topic
    topic = sns.create_topic(Name="epf-sentinel-status-notifications-dev")
    topic_arn = topic["TopicArn"]

    os.environ["STAGE"] = "dev"
    os.environ["CLAIMS_TABLE_NAME"] = "epf-sentinel-Claims-dev"
    os.environ["ANALYSIS_RUNS_TABLE_NAME"] = "epf-sentinel-AnalysisRuns-dev"
    os.environ["STATUS_TRANSITIONS_TABLE_NAME"] = "epf-sentinel-StatusTransitions-dev"
    os.environ["STATUS_NOTIFICATIONS_TOPIC_ARN"] = topic_arn

    from functions.sweep_function.app import handler

    claims_table = dynamodb.Table("epf-sentinel-Claims-dev")
    runs_table = dynamodb.Table("epf-sentinel-AnalysisRuns-dev")
    transitions_table = dynamodb.Table("epf-sentinel-StatusTransitions-dev")

    user_id = "testuser@example.com"
    today = date.today()

    print("\n--- Seeding Data ---")
    claims = []
    
    # We need 6 claims to trigger the 5-notification cap.
    # We will make 6 claims all APPROACHING so they all trigger notifications.
    for i in range(6):
        c = Claim.new(
            userId=user_id,
            claimType=ClaimType.FINAL_SETTLEMENT,
            claimDateIso=(today - timedelta(days=18)).isoformat(), # APPROACHING
            amountPaise=10000 * (i+1),
            status=ClaimStatus.UNDER_PROCESS
        )
        run = AnalysisRun.new(
            claimId=c.claimId,
            correlationId=str(uuid.uuid4()),
            expiresAt=int(time.time()) + 86400
        )
        run.rulesAgentOutputJson = json.dumps({"applicable": True, "timelineDays": 20, "timelineBasis": "CALENDAR"})
        run.slaAgentOutputJson = json.dumps({"status": "WITHIN"})
        c.latestRunId = run.runId
        
        claims_table.put_item(Item=c.to_dict())
        runs_table.put_item(Item=run.to_dict())
        claims.append(c)

    print("Data seeded. Running Sweep #1...")
    
    # We will capture logs and SNS messages
    sns_messages = []
    
    # Mock sns.publish inside the handler to capture easily
    original_publish = sns.publish
    def mock_publish(**kwargs):
        sns_messages.append(kwargs)
        return original_publish(**kwargs)
        
    with patch("functions.sweep_function.app.sns.publish", side_effect=mock_publish):
        import logging
        from io import StringIO
        log_stream = StringIO()
        logger = logging.getLogger("functions.sweep_function.app")
        logger.setLevel(logging.INFO)
        handler_obj = logging.StreamHandler(log_stream)
        handler_obj.setFormatter(logging.Formatter('%(levelname)s - %(message)s'))
        logger.addHandler(handler_obj)

        result1 = handler({}, {})
        
        log_output_1 = log_stream.getvalue()
        logger.removeHandler(handler_obj)

    print("\n--- Logs for Sweep #1 ---")
    print(log_output_1)
    
    print("\n--- SNS Delivery Records (Captured in memory) ---")
    for msg in sns_messages:
        print(json.dumps(msg, indent=2))
        
    print(f"\nResult 1: {result1}")
        
    print("\n--- StatusTransition Items in DynamoDB ---")
    transitions = transitions_table.scan()["Items"]
    for t in transitions:
        # Convert Decimals for JSON serialization
        t['expiresAt'] = int(t['expiresAt'])
        print(json.dumps(t, indent=2))

    print("\nRunning Sweep #2 (Deduplication Test)...")
    sns_messages.clear()
    
    with patch("functions.sweep_function.app.sns.publish", side_effect=mock_publish):
        log_stream = StringIO()
        handler_obj = logging.StreamHandler(log_stream)
        logger.addHandler(handler_obj)

        result2 = handler({}, {})
        
        log_output_2 = log_stream.getvalue()
        logger.removeHandler(handler_obj)

    print("\n--- Logs for Sweep #2 ---")
    print(log_output_2)
    print(f"Result 2: {result2}")
    
    # Alarm testing
    print("\n--- Alarm Configuration and Testing ---")
    cloudwatch.put_metric_alarm(
        AlarmName="epf-sentinel-sweep-silent-stop-dev",
        AlarmDescription="Triggers if SweepFunction hasn't successfully run in 26 hours",
        MetricName="Invocations",
        Namespace="AWS/Lambda",
        Statistic="Sum",
        Period=93600,
        EvaluationPeriods=1,
        Threshold=1,
        ComparisonOperator="LessThanThreshold",
        TreatMissingData="breaching",
        Dimensions=[{"Name": "FunctionName", "Value": "epf-sentinel-sweep-dev"}]
    )
    
    alarms = cloudwatch.describe_alarms(AlarmNames=["epf-sentinel-sweep-silent-stop-dev"])
    print("Alarm Config:", json.dumps(alarms["MetricAlarms"][0], indent=2, default=str))
    
    print("Setting state to OK (assuming it ran)...")
    cloudwatch.set_alarm_state(
        AlarmName="epf-sentinel-sweep-silent-stop-dev",
        StateValue="OK",
        StateReason="Function was invoked"
    )
    print("Current State:", cloudwatch.describe_alarms(AlarmNames=["epf-sentinel-sweep-silent-stop-dev"])["MetricAlarms"][0]["StateValue"])
    
    print("Faking 26 hours of no invocations (breaching state)...")
    cloudwatch.set_alarm_state(
        AlarmName="epf-sentinel-sweep-silent-stop-dev",
        StateValue="ALARM",
        StateReason="Threshold Crossed: 1 datapoint [0.0 (20/09/26 10:00:00)] was less than the threshold (1.0)."
    )
    
    final_alarm = cloudwatch.describe_alarms(AlarmNames=["epf-sentinel-sweep-silent-stop-dev"])["MetricAlarms"][0]
    print("Final Alarm State:", final_alarm["StateValue"])
    print("State Reason:", final_alarm["StateReason"])

if __name__ == "__main__":
    simulate()
