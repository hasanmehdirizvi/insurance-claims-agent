"""Supervisor agent for multi-agent claims processing orchestration.

The supervisor acts as a router/coordinator that receives incoming claims
requests and delegates to the appropriate specialist agent based on the
current stage of the claims lifecycle:

- Claims Intake Agent: FNOL registration and initial triage
- Fraud Detection Agent: Risk scoring and SIU referral decisions
- Adjudication Agent: Coverage determination and settlement calculation

The supervisor maintains claim state context across agent handoffs and
enforces processing guardrails (max iterations, approval thresholds).
"""

from __future__ import annotations

from typing import Any

import structlog
from strands import Agent
from strands.models.bedrock import BedrockModel

from src.agents.adjudication import create_adjudication_agent
from src.agents.claims_intake import create_claims_intake_agent
from src.agents.fraud_detection import create_fraud_detection_agent
from src.config import AppConfig, ClaimStatus, get_bedrock_model_kwargs, get_config
from src.tools.claims_db import lookup_claim, update_claim_status

logger = structlog.get_logger(__name__)

SUPERVISOR_SYSTEM_PROMPT = """You are the Claims Processing Supervisor, an expert insurance claims
orchestration agent. You coordinate the end-to-end claims lifecycle by delegating to
specialist agents and making routing decisions.

Your responsibilities:
1. INTAKE: Route new First Notice of Loss (FNOL) reports to the Claims Intake Agent
2. FRAUD SCREENING: Route claims through the Fraud Detection Agent for risk scoring
3. ADJUDICATION: Route coverage-verified claims to the Adjudication Agent for settlement
4. ESCALATION: Flag claims requiring human adjuster review based on thresholds

Processing rules:
- Every claim MUST go through fraud screening before adjudication
- Claims with fraud score >= {fraud_threshold} are referred to SIU (do not adjudicate)
- Claims exceeding ${human_review_threshold:,.0f} require human review flag
- Maintain audit trail by updating claim status at each transition
- Never approve claims without verified coverage

When you receive a request:
1. Determine the current claim stage (new FNOL, existing claim needing next step)
2. Look up existing claim data if a claim_id is provided
3. Route to the appropriate specialist agent
4. Collect the specialist's output and determine the next step
5. Update claim status and return a structured summary

Always respond with clear, structured JSON summaries of actions taken and next steps."""


def create_supervisor_agent(config: AppConfig | None = None) -> Agent:
    """Create and configure the supervisor/router agent.

    The supervisor has access to claims database tools for state management
    and delegates specialist work to sub-agents.

    Args:
        config: Application configuration. Uses defaults if not provided.

    Returns:
        Configured Strands Agent instance for claims supervision.
    """
    cfg = config or get_config()

    model = BedrockModel(**get_bedrock_model_kwargs(cfg.model))

    system_prompt = SUPERVISOR_SYSTEM_PROMPT.format(
        fraud_threshold=cfg.guardrails.fraud_score_threshold,
        human_review_threshold=cfg.guardrails.require_human_review_above,
    )

    agent = Agent(
        model=model,
        system_prompt=system_prompt,
        tools=[lookup_claim, update_claim_status, route_to_intake, route_to_fraud, route_to_adjudication],
    )

    logger.info("supervisor_agent_created", model_id=cfg.model.model_id)
    return agent


def route_to_intake(
    claimant_name: str,
    policy_number: str,
    loss_date: str,
    loss_description: str,
    claim_type: str,
    loss_location: str,
    estimated_amount: float,
    contact_phone: str,
    contact_email: str,
) -> dict[str, Any]:
    """Route a new FNOL to the Claims Intake Agent for registration and triage.

    Args:
        claimant_name: Full name of the person filing the claim.
        policy_number: The insured's policy number.
        loss_date: Date of loss in ISO format.
        loss_description: Description of what happened.
        claim_type: Type of claim being filed.
        loss_location: Where the loss occurred.
        estimated_amount: Estimated claim amount.
        contact_phone: Claimant phone number.
        contact_email: Claimant email.

    Returns:
        Intake agent's response with claim ID and triage results.
    """
    logger.info("routing_to_intake", policy_number=policy_number, claim_type=claim_type)

    intake_agent = create_claims_intake_agent()

    prompt = f"""Process this new FNOL (First Notice of Loss):

Claimant: {claimant_name}
Policy Number: {policy_number}
Loss Date: {loss_date}
Loss Location: {loss_location}
Claim Type: {claim_type}
Estimated Amount: ${estimated_amount:,.2f}
Contact Phone: {contact_phone}
Contact Email: {contact_email}

Loss Description:
{loss_description}

Please register this claim, validate the policy coverage, and provide initial triage."""

    response = intake_agent(prompt)

    return {
        "agent": "claims_intake",
        "status": "completed",
        "response": str(response),
    }


def route_to_fraud(
    claim_id: str,
    claim_type: str,
    claimed_amount: float,
    loss_date: str,
    report_date: str,
    loss_description: str,
    loss_location_zip: str,
    policy_inception_date: str,
    prior_claims_count: int,
    police_report_filed: bool,
    claimant_name: str,
) -> dict[str, Any]:
    """Route a claim to the Fraud Detection Agent for risk assessment.

    Args:
        claim_id: The claim identifier to score.
        claim_type: Type of claim.
        claimed_amount: Dollar amount claimed.
        loss_date: Date of loss (ISO format).
        report_date: Date claim was reported (ISO format).
        loss_description: Loss narrative.
        loss_location_zip: ZIP code of loss location.
        policy_inception_date: When the policy started (ISO format).
        prior_claims_count: Number of prior claims in 24 months.
        police_report_filed: Whether police report exists.
        claimant_name: Name for network analysis.

    Returns:
        Fraud detection agent's response with risk score and recommendation.
    """
    logger.info("routing_to_fraud", claim_id=claim_id)

    fraud_agent = create_fraud_detection_agent()

    prompt = f"""Perform fraud risk assessment for claim {claim_id}:

Claim Type: {claim_type}
Claimed Amount: ${claimed_amount:,.2f}
Loss Date: {loss_date}
Report Date: {report_date}
Loss Location ZIP: {loss_location_zip}
Policy Inception: {policy_inception_date}
Prior Claims (24 months): {prior_claims_count}
Police Report Filed: {police_report_filed}
Claimant: {claimant_name}

Loss Description:
{loss_description}

Score this claim for fraud risk and provide your recommendation."""

    response = fraud_agent(prompt)

    return {
        "agent": "fraud_detection",
        "status": "completed",
        "response": str(response),
    }


def route_to_adjudication(
    claim_id: str,
    policy_number: str,
    claim_type: str,
    claimed_amount: float,
    fraud_score: float,
    coverage_limit: float,
    deductible: float,
    loss_description: str,
) -> dict[str, Any]:
    """Route a claim to the Adjudication Agent for settlement determination.

    Args:
        claim_id: The claim identifier.
        policy_number: Associated policy number.
        claim_type: Type of claim.
        claimed_amount: Requested amount.
        fraud_score: Score from fraud detection (0-1).
        coverage_limit: Maximum coverage amount from policy.
        deductible: Policy deductible amount.
        loss_description: Description of the loss.

    Returns:
        Adjudication agent's response with settlement decision.
    """
    logger.info("routing_to_adjudication", claim_id=claim_id, fraud_score=fraud_score)

    adjudication_agent = create_adjudication_agent()

    prompt = f"""Adjudicate claim {claim_id}:

Policy Number: {policy_number}
Claim Type: {claim_type}
Claimed Amount: ${claimed_amount:,.2f}
Fraud Risk Score: {fraud_score}
Coverage Limit: ${coverage_limit:,.2f}
Deductible: ${deductible:,.2f}

Loss Description:
{loss_description}

Determine the appropriate settlement amount and provide your adjudication decision."""

    response = adjudication_agent(prompt)

    return {
        "agent": "adjudication",
        "status": "completed",
        "response": str(response),
    }
