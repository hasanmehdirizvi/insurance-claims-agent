"""DynamoDB-backed claims data access tools.

Provides CRUD operations for insurance claims stored in DynamoDB. These tools
are invoked by agents to look up existing claims, create new FNOL records,
and update claim status throughout the adjudication lifecycle.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import boto3
import structlog
from strands import tool

from src.config import AppConfig, ClaimStatus, get_config

logger = structlog.get_logger(__name__)


def _get_claims_table(config: AppConfig | None = None):
    """Get DynamoDB Table resource for claims."""
    cfg = config or get_config()
    dynamodb = boto3.resource(
        "dynamodb",
        region_name=cfg.dynamodb.region,
        endpoint_url=cfg.dynamodb.endpoint_url,
    )
    return dynamodb.Table(cfg.dynamodb.claims_table)


def _serialize_decimals(obj: Any) -> Any:
    """Convert DynamoDB Decimal types to float for JSON serialization."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, dict):
        return {k: _serialize_decimals(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize_decimals(i) for i in obj]
    return obj


@tool
def lookup_claim(claim_id: str) -> dict[str, Any]:
    """Look up an insurance claim by its unique claim ID.

    Retrieves the full claim record from DynamoDB including claimant information,
    loss details, coverage data, and current processing status.

    Args:
        claim_id: The unique claim identifier (e.g., "CLM-2024-001234").

    Returns:
        Dictionary containing the full claim record, or an error message if not found.
    """
    logger.info("looking_up_claim", claim_id=claim_id)

    try:
        table = _get_claims_table()
        response = table.get_item(Key={"claim_id": claim_id})

        if "Item" not in response:
            logger.warning("claim_not_found", claim_id=claim_id)
            return {
                "status": "not_found",
                "message": f"No claim found with ID: {claim_id}",
            }

        claim = _serialize_decimals(response["Item"])
        logger.info("claim_retrieved", claim_id=claim_id, claim_status=claim.get("status"))
        return {"status": "success", "claim": claim}

    except Exception as e:
        logger.error("claim_lookup_failed", claim_id=claim_id, error=str(e))
        return {
            "status": "error",
            "message": f"Failed to retrieve claim {claim_id}: {str(e)}",
        }


@tool
def create_claim(
    policy_number: str,
    claimant_name: str,
    loss_date: str,
    loss_description: str,
    claim_type: str,
    loss_location: str,
    estimated_amount: float,
    contact_phone: str,
    contact_email: str,
) -> dict[str, Any]:
    """Create a new First Notice of Loss (FNOL) claim record.

    Registers an initial claim in the system after intake processing. Generates
    a unique claim ID and sets the initial status to FNOL_RECEIVED.

    Args:
        policy_number: The insured's policy number (e.g., "POL-AUTO-2024-5678").
        claimant_name: Full name of the person filing the claim.
        loss_date: Date of loss in ISO format (YYYY-MM-DD).
        loss_description: Detailed description of the loss event.
        claim_type: Type of claim (auto_collision, property_damage, etc.).
        loss_location: Address or description of where the loss occurred.
        estimated_amount: Initial estimated claim amount in USD.
        contact_phone: Claimant's phone number for follow-up.
        contact_email: Claimant's email address.

    Returns:
        Dictionary with the new claim ID and creation confirmation.
    """
    claim_id = f"CLM-{datetime.now(tz=timezone.utc).strftime('%Y')}-{uuid.uuid4().hex[:6].upper()}"
    now = datetime.now(tz=timezone.utc).isoformat()

    logger.info(
        "creating_claim",
        claim_id=claim_id,
        policy_number=policy_number,
        claim_type=claim_type,
    )

    claim_record = {
        "claim_id": claim_id,
        "policy_number": policy_number,
        "claimant_name": claimant_name,
        "claim_type": claim_type,
        "status": ClaimStatus.FNOL_RECEIVED.value,
        "loss_date": loss_date,
        "loss_description": loss_description,
        "loss_location": loss_location,
        "estimated_amount": Decimal(str(estimated_amount)),
        "contact_phone": contact_phone,
        "contact_email": contact_email,
        "created_at": now,
        "updated_at": now,
        "adjuster_assigned": None,
        "fraud_score": None,
        "coverage_verified": False,
        "payment_amount": None,
        "denial_reason": None,
        "notes": [],
    }

    try:
        table = _get_claims_table()
        table.put_item(
            Item=claim_record,
            ConditionExpression="attribute_not_exists(claim_id)",
        )

        logger.info("claim_created", claim_id=claim_id)
        return {
            "status": "success",
            "claim_id": claim_id,
            "message": f"FNOL claim {claim_id} created successfully for policy {policy_number}.",
        }

    except Exception as e:
        logger.error("claim_creation_failed", claim_id=claim_id, error=str(e))
        return {
            "status": "error",
            "message": f"Failed to create claim: {str(e)}",
        }


@tool
def update_claim_status(
    claim_id: str,
    new_status: str,
    notes: str = "",
    additional_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Update the status of an existing claim and optionally add notes or fields.

    Used throughout the claims lifecycle to transition claims between states
    (e.g., from fraud_check to coverage_verified to adjudication).

    Args:
        claim_id: The claim identifier to update.
        new_status: The new status value (must be a valid ClaimStatus).
        notes: Optional note to append to the claim's notes history.
        additional_fields: Optional dict of additional fields to update
            (e.g., {"fraud_score": 0.85, "adjuster_assigned": "ADJ-101"}).

    Returns:
        Dictionary confirming the update or describing any error.
    """
    logger.info("updating_claim_status", claim_id=claim_id, new_status=new_status)

    # Validate status transition
    try:
        validated_status = ClaimStatus(new_status)
    except ValueError:
        valid_statuses = [s.value for s in ClaimStatus]
        return {
            "status": "error",
            "message": f"Invalid status '{new_status}'. Valid statuses: {valid_statuses}",
        }

    now = datetime.now(tz=timezone.utc).isoformat()

    update_expr_parts = [
        "#status = :new_status",
        "updated_at = :now",
    ]
    expr_attr_names = {"#status": "status"}
    expr_attr_values: dict[str, Any] = {
        ":new_status": validated_status.value,
        ":now": now,
    }

    if notes:
        update_expr_parts.append("notes = list_append(if_not_exists(notes, :empty_list), :note)")
        expr_attr_values[":note"] = [{"timestamp": now, "content": notes}]
        expr_attr_values[":empty_list"] = []

    if additional_fields:
        for key, value in additional_fields.items():
            safe_key = f"#field_{key}"
            safe_val = f":val_{key}"
            update_expr_parts.append(f"{safe_key} = {safe_val}")
            expr_attr_names[safe_key] = key
            if isinstance(value, float):
                expr_attr_values[safe_val] = Decimal(str(value))
            else:
                expr_attr_values[safe_val] = value

    try:
        table = _get_claims_table()
        table.update_item(
            Key={"claim_id": claim_id},
            UpdateExpression="SET " + ", ".join(update_expr_parts),
            ExpressionAttributeNames=expr_attr_names,
            ExpressionAttributeValues=expr_attr_values,
            ConditionExpression="attribute_exists(claim_id)",
        )

        logger.info("claim_status_updated", claim_id=claim_id, new_status=validated_status.value)
        return {
            "status": "success",
            "message": f"Claim {claim_id} updated to status: {validated_status.value}",
        }

    except Exception as e:
        logger.error("claim_update_failed", claim_id=claim_id, error=str(e))
        return {
            "status": "error",
            "message": f"Failed to update claim {claim_id}: {str(e)}",
        }
