"""Policy validation tool for coverage verification.

Validates that a given policy covers the reported loss type, checks policy
effective dates, verifies deductible amounts, and confirms coverage limits.
This is a critical step before adjudication can proceed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
import structlog
from strands import tool

from src.config import AppConfig, ClaimType, get_config

logger = structlog.get_logger(__name__)


def _get_policies_table(config: AppConfig | None = None):
    """Get DynamoDB Table resource for policies."""
    cfg = config or get_config()
    dynamodb = boto3.resource(
        "dynamodb",
        region_name=cfg.dynamodb.region,
        endpoint_url=cfg.dynamodb.endpoint_url,
    )
    return dynamodb.Table(cfg.dynamodb.policies_table)


# Coverage mapping: which policy types cover which claim types
COVERAGE_MATRIX: dict[str, set[str]] = {
    "auto_standard": {
        ClaimType.AUTO_COLLISION.value,
        ClaimType.AUTO_COMPREHENSIVE.value,
        ClaimType.UNINSURED_MOTORIST.value,
        ClaimType.PERSONAL_INJURY_PROTECTION.value,
    },
    "auto_liability_only": {
        ClaimType.BODILY_INJURY.value,
        ClaimType.PROPERTY_DAMAGE.value,
    },
    "homeowners_standard": {
        ClaimType.HOMEOWNERS.value,
        ClaimType.PROPERTY_DAMAGE.value,
    },
    "commercial_property": {
        ClaimType.COMMERCIAL_PROPERTY.value,
        ClaimType.PROPERTY_DAMAGE.value,
    },
}


@tool
def validate_policy_coverage(
    policy_number: str,
    claim_type: str,
    loss_date: str,
    claimed_amount: float,
) -> dict[str, Any]:
    """Validate that a policy provides coverage for the reported claim.

    Performs comprehensive coverage verification including:
    - Policy existence and active status check
    - Coverage type applicability for the claim type
    - Policy effective date range validation against loss date
    - Coverage limit sufficiency
    - Deductible calculation
    - Exclusion screening

    Args:
        policy_number: The policy number to validate (e.g., "POL-AUTO-2024-5678").
        claim_type: The type of claim being filed (must match ClaimType enum values).
        loss_date: Date of loss in ISO format for policy period validation.
        claimed_amount: The claimed/estimated loss amount in USD.

    Returns:
        Dictionary containing validation results with coverage determination,
        applicable limits, deductible amount, and any exclusions found.
    """
    logger.info(
        "validating_policy_coverage",
        policy_number=policy_number,
        claim_type=claim_type,
        loss_date=loss_date,
    )

    try:
        table = _get_policies_table()
        response = table.get_item(Key={"policy_number": policy_number})

        if "Item" not in response:
            logger.warning("policy_not_found", policy_number=policy_number)
            return {
                "status": "invalid",
                "coverage_valid": False,
                "reason": f"Policy {policy_number} not found in system.",
                "recommendation": "Verify policy number with claimant. Check for typos or expired policies.",
            }

        policy = response["Item"]

    except Exception as e:
        logger.error("policy_lookup_failed", policy_number=policy_number, error=str(e))
        return {
            "status": "error",
            "coverage_valid": False,
            "reason": f"Unable to retrieve policy: {str(e)}",
        }

    # Check policy status
    if policy.get("status") != "active":
        return {
            "status": "invalid",
            "coverage_valid": False,
            "reason": f"Policy {policy_number} is not active. Current status: {policy.get('status')}",
            "recommendation": "Check for lapsed premium payments or policy cancellation.",
        }

    # Validate loss date within policy period
    try:
        loss_dt = datetime.fromisoformat(loss_date).replace(tzinfo=timezone.utc)
        effective_date = datetime.fromisoformat(policy["effective_date"]).replace(tzinfo=timezone.utc)
        expiration_date = datetime.fromisoformat(policy["expiration_date"]).replace(tzinfo=timezone.utc)

        if not (effective_date <= loss_dt <= expiration_date):
            return {
                "status": "invalid",
                "coverage_valid": False,
                "reason": (
                    f"Loss date {loss_date} falls outside policy period "
                    f"({policy['effective_date']} to {policy['expiration_date']})."
                ),
                "recommendation": "Check if prior policy was in effect on the loss date.",
            }
    except (ValueError, KeyError) as e:
        logger.warning("date_validation_issue", error=str(e))

    # Check coverage type applicability
    policy_type = policy.get("policy_type", "")
    covered_claim_types = COVERAGE_MATRIX.get(policy_type, set())

    if claim_type not in covered_claim_types:
        return {
            "status": "invalid",
            "coverage_valid": False,
            "reason": (
                f"Policy type '{policy_type}' does not cover claim type '{claim_type}'. "
                f"Covered types: {sorted(covered_claim_types)}"
            ),
            "recommendation": "Review policy endorsements or check for umbrella coverage.",
        }

    # Check coverage limits
    coverage_limit = float(policy.get("coverage_limit", 0))
    deductible = float(policy.get("deductible", 0))
    aggregate_used = float(policy.get("aggregate_claims_paid", 0))
    aggregate_limit = float(policy.get("aggregate_limit", coverage_limit * 3))

    remaining_aggregate = aggregate_limit - aggregate_used
    net_claimable = min(claimed_amount - deductible, coverage_limit, remaining_aggregate)

    # Check exclusions
    exclusions = policy.get("exclusions", [])
    applicable_exclusions = [
        exc for exc in exclusions
        if exc.get("claim_type") == claim_type or exc.get("applies_to_all", False)
    ]

    # Build validation result
    coverage_sufficient = net_claimable > 0 and not applicable_exclusions

    result = {
        "status": "valid" if coverage_sufficient else "insufficient",
        "coverage_valid": coverage_sufficient,
        "policy_number": policy_number,
        "policy_type": policy_type,
        "policyholder_name": policy.get("policyholder_name", "Unknown"),
        "coverage_details": {
            "per_occurrence_limit": coverage_limit,
            "deductible": deductible,
            "aggregate_limit": aggregate_limit,
            "aggregate_used": aggregate_used,
            "remaining_aggregate": remaining_aggregate,
            "net_claimable_amount": max(net_claimable, 0),
            "claimant_responsibility": min(deductible, claimed_amount),
        },
        "exclusions_found": applicable_exclusions,
        "policy_period": {
            "effective_date": policy.get("effective_date"),
            "expiration_date": policy.get("expiration_date"),
        },
    }

    if coverage_sufficient:
        result["recommendation"] = (
            f"Coverage verified. Maximum payable: ${net_claimable:,.2f} "
            f"(after ${deductible:,.2f} deductible). Proceed to adjudication."
        )
    elif applicable_exclusions:
        result["recommendation"] = (
            f"Coverage excluded. {len(applicable_exclusions)} exclusion(s) apply. "
            "Review exclusion language and consult with coverage counsel if disputed."
        )
    else:
        result["recommendation"] = (
            f"Insufficient coverage. Net claimable: ${max(net_claimable, 0):,.2f}. "
            "Check for excess/umbrella policies or negotiate partial settlement."
        )

    logger.info(
        "policy_validation_complete",
        policy_number=policy_number,
        coverage_valid=coverage_sufficient,
    )
    return result
