"""Unit tests for the Claims Intake agent and FNOL processing tools.

Uses moto to mock DynamoDB and validates the claim creation workflow,
policy validation, and intake triage logic.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from src.config import AppConfig, ClaimStatus, ClaimType, DynamoDBConfig
from src.tools.claims_db import create_claim, lookup_claim, update_claim_status
from src.tools.policy_validator import validate_policy_coverage


@pytest.fixture
def dynamodb_setup():
    """Create mock DynamoDB tables for testing."""
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")

        # Create claims table
        dynamodb.create_table(
            TableName="insurance-claims",
            KeySchema=[{"AttributeName": "claim_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "claim_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        # Create policies table
        policies_table = dynamodb.create_table(
            TableName="insurance-policies",
            KeySchema=[{"AttributeName": "policy_number", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "policy_number", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        # Seed a test policy
        policies_table.put_item(
            Item={
                "policy_number": "POL-AUTO-2024-7821",
                "policyholder_name": "Sarah Mitchell",
                "policy_type": "auto_standard",
                "status": "active",
                "effective_date": "2024-01-15T00:00:00+00:00",
                "expiration_date": "2025-07-15T00:00:00+00:00",
                "coverage_limit": Decimal("50000"),
                "deductible": Decimal("500"),
                "aggregate_limit": Decimal("150000"),
                "aggregate_claims_paid": Decimal("0"),
                "exclusions": [],
            }
        )

        # Seed an expired policy
        policies_table.put_item(
            Item={
                "policy_number": "POL-AUTO-2023-EXPIRED",
                "policyholder_name": "John Doe",
                "policy_type": "auto_standard",
                "status": "expired",
                "effective_date": "2023-01-01T00:00:00+00:00",
                "expiration_date": "2024-01-01T00:00:00+00:00",
                "coverage_limit": Decimal("25000"),
                "deductible": Decimal("1000"),
                "aggregate_limit": Decimal("75000"),
                "aggregate_claims_paid": Decimal("0"),
                "exclusions": [],
            }
        )

        yield dynamodb


@pytest.fixture
def mock_config():
    """Provide a test configuration pointing to mocked resources."""
    return AppConfig(
        dynamodb=DynamoDBConfig(
            claims_table="insurance-claims",
            policies_table="insurance-policies",
            region="us-east-1",
        )
    )


class TestCreateClaim:
    """Tests for the create_claim tool."""

    @mock_aws
    def test_create_claim_success(self, dynamodb_setup, mock_config):
        """Test successful FNOL claim creation."""
        with patch("src.tools.claims_db.get_config", return_value=mock_config):
            result = create_claim(
                policy_number="POL-AUTO-2024-7821",
                claimant_name="Sarah Mitchell",
                loss_date="2025-05-10",
                loss_description="Rear-ended at intersection",
                claim_type="auto_collision",
                loss_location="Hartford, CT",
                estimated_amount=8500.00,
                contact_phone="(860) 555-0142",
                contact_email="sarah@example.com",
            )

            assert result["status"] == "success"
            assert result["claim_id"].startswith("CLM-")
            assert "POL-AUTO-2024-7821" in result["message"]

    @mock_aws
    def test_create_claim_generates_unique_ids(self, dynamodb_setup, mock_config):
        """Test that multiple claims get unique IDs."""
        with patch("src.tools.claims_db.get_config", return_value=mock_config):
            result1 = create_claim(
                policy_number="POL-AUTO-2024-7821",
                claimant_name="Sarah Mitchell",
                loss_date="2025-05-10",
                loss_description="First claim",
                claim_type="auto_collision",
                loss_location="Hartford, CT",
                estimated_amount=5000.00,
                contact_phone="(860) 555-0142",
                contact_email="sarah@example.com",
            )
            result2 = create_claim(
                policy_number="POL-AUTO-2024-7821",
                claimant_name="Sarah Mitchell",
                loss_date="2025-05-12",
                loss_description="Second claim",
                claim_type="auto_comprehensive",
                loss_location="Hartford, CT",
                estimated_amount=3000.00,
                contact_phone="(860) 555-0142",
                contact_email="sarah@example.com",
            )

            assert result1["claim_id"] != result2["claim_id"]


class TestLookupClaim:
    """Tests for the lookup_claim tool."""

    @mock_aws
    def test_lookup_nonexistent_claim(self, dynamodb_setup, mock_config):
        """Test looking up a claim that doesn't exist."""
        with patch("src.tools.claims_db.get_config", return_value=mock_config):
            result = lookup_claim(claim_id="CLM-DOES-NOT-EXIST")

            assert result["status"] == "not_found"
            assert "CLM-DOES-NOT-EXIST" in result["message"]

    @mock_aws
    def test_lookup_existing_claim(self, dynamodb_setup, mock_config):
        """Test looking up a claim that was just created."""
        with patch("src.tools.claims_db.get_config", return_value=mock_config):
            # Create a claim first
            create_result = create_claim(
                policy_number="POL-AUTO-2024-7821",
                claimant_name="Sarah Mitchell",
                loss_date="2025-05-10",
                loss_description="Test collision",
                claim_type="auto_collision",
                loss_location="Hartford, CT",
                estimated_amount=8500.00,
                contact_phone="(860) 555-0142",
                contact_email="sarah@example.com",
            )

            # Look it up
            claim_id = create_result["claim_id"]
            result = lookup_claim(claim_id=claim_id)

            assert result["status"] == "success"
            assert result["claim"]["claim_id"] == claim_id
            assert result["claim"]["claimant_name"] == "Sarah Mitchell"
            assert result["claim"]["status"] == ClaimStatus.FNOL_RECEIVED.value


class TestUpdateClaimStatus:
    """Tests for the update_claim_status tool."""

    @mock_aws
    def test_update_status_valid_transition(self, dynamodb_setup, mock_config):
        """Test updating claim status with a valid status value."""
        with patch("src.tools.claims_db.get_config", return_value=mock_config):
            # Create a claim
            create_result = create_claim(
                policy_number="POL-AUTO-2024-7821",
                claimant_name="Sarah Mitchell",
                loss_date="2025-05-10",
                loss_description="Test",
                claim_type="auto_collision",
                loss_location="Hartford, CT",
                estimated_amount=8500.00,
                contact_phone="(860) 555-0142",
                contact_email="sarah@example.com",
            )
            claim_id = create_result["claim_id"]

            # Update status
            result = update_claim_status(
                claim_id=claim_id,
                new_status="fraud_check",
                notes="Routing to fraud detection agent",
            )

            assert result["status"] == "success"
            assert "fraud_check" in result["message"]

    @mock_aws
    def test_update_status_invalid_status(self, dynamodb_setup, mock_config):
        """Test that invalid status values are rejected."""
        with patch("src.tools.claims_db.get_config", return_value=mock_config):
            result = update_claim_status(
                claim_id="CLM-2025-TEST01",
                new_status="invalid_status",
                notes="This should fail",
            )

            assert result["status"] == "error"
            assert "Invalid status" in result["message"]


class TestValidatePolicyCoverage:
    """Tests for the validate_policy_coverage tool."""

    @mock_aws
    def test_valid_coverage(self, dynamodb_setup, mock_config):
        """Test coverage validation for a valid active policy."""
        with patch("src.tools.policy_validator.get_config", return_value=mock_config):
            result = validate_policy_coverage(
                policy_number="POL-AUTO-2024-7821",
                claim_type="auto_collision",
                loss_date="2025-05-10T00:00:00+00:00",
                claimed_amount=8500.00,
            )

            assert result["coverage_valid"] is True
            assert result["status"] == "valid"
            assert result["coverage_details"]["deductible"] == 500.0
            assert result["coverage_details"]["per_occurrence_limit"] == 50000.0

    @mock_aws
    def test_expired_policy(self, dynamodb_setup, mock_config):
        """Test coverage validation for an expired policy."""
        with patch("src.tools.policy_validator.get_config", return_value=mock_config):
            result = validate_policy_coverage(
                policy_number="POL-AUTO-2023-EXPIRED",
                claim_type="auto_collision",
                loss_date="2025-05-10T00:00:00+00:00",
                claimed_amount=5000.00,
            )

            assert result["coverage_valid"] is False
            assert "not active" in result["reason"]

    @mock_aws
    def test_policy_not_found(self, dynamodb_setup, mock_config):
        """Test coverage validation for a nonexistent policy."""
        with patch("src.tools.policy_validator.get_config", return_value=mock_config):
            result = validate_policy_coverage(
                policy_number="POL-DOES-NOT-EXIST",
                claim_type="auto_collision",
                loss_date="2025-05-10T00:00:00+00:00",
                claimed_amount=5000.00,
            )

            assert result["coverage_valid"] is False
            assert result["status"] == "invalid"
            assert "not found" in result["reason"]

    @mock_aws
    def test_uncovered_claim_type(self, dynamodb_setup, mock_config):
        """Test that a claim type not covered by the policy is rejected."""
        with patch("src.tools.policy_validator.get_config", return_value=mock_config):
            result = validate_policy_coverage(
                policy_number="POL-AUTO-2024-7821",
                claim_type="homeowners",
                loss_date="2025-05-10T00:00:00+00:00",
                claimed_amount=50000.00,
            )

            assert result["coverage_valid"] is False
            assert "does not cover" in result["reason"]


class TestFraudScorerIntegration:
    """Tests for the fraud scoring tool."""

    def test_low_risk_claim(self):
        """Test that a straightforward claim scores low risk."""
        from src.tools.fraud_scorer import score_fraud_risk

        result = score_fraud_risk(
            claim_id="CLM-2025-TEST01",
            claim_type="auto_collision",
            claimed_amount=8500.00,
            loss_date="2025-05-10T00:00:00+00:00",
            report_date="2025-05-10T00:00:00+00:00",
            loss_description="Rear-ended at red light. Police report filed. Two witnesses.",
            loss_location_zip="06103",
            policy_inception_date="2024-01-15T00:00:00+00:00",
            prior_claims_count=0,
            police_report_filed=True,
            claimant_name="Sarah Mitchell",
        )

        assert result["status"] == "success"
        assert result["fraud_score"] < 0.4
        assert result["risk_level"] == "LOW"

    def test_high_risk_claim(self):
        """Test that a suspicious claim scores high risk."""
        from src.tools.fraud_scorer import score_fraud_risk

        result = score_fraud_risk(
            claim_id="CLM-2025-TEST02",
            claim_type="auto_comprehensive",
            claimed_amount=45000.00,
            loss_date="2025-05-03T00:00:00+00:00",
            report_date="2025-06-05T00:00:00+00:00",  # 33 day delay
            loss_description=(
                "Vehicle was completely destroyed by fire. No witnesses. "
                "Total loss. Financial hardship. Everything stolen."
            ),
            loss_location_zip="10012",  # High fraud ZIP
            policy_inception_date="2025-03-20T00:00:00+00:00",  # 44 days before loss
            prior_claims_count=3,
            police_report_filed=False,
            claimant_name="James Rodriguez",
        )

        assert result["status"] == "success"
        assert result["fraud_score"] >= 0.7
        assert result["risk_level"] == "HIGH"
        assert result["indicators_triggered"] >= 3
        assert "SIU" in result["recommendation"]
