"""Adjudication Agent for claims settlement determination.

Handles the final decision-making phase of claims processing including:
- Settlement amount calculation based on coverage, damages, and depreciation
- Total loss vs. repair determination for auto/property claims
- Subrogation opportunity identification
- Payment authorization within delegated authority limits
- Denial determination with compliant reason codes

This agent receives claims that have cleared fraud screening and have
verified coverage, then makes the final settlement determination.
"""

from __future__ import annotations

import structlog
from strands import Agent
from strands.models.bedrock import BedrockModel

from src.config import AppConfig, get_bedrock_model_kwargs, get_config
from src.tools.claims_db import lookup_claim, update_claim_status
from src.tools.policy_validator import validate_policy_coverage

logger = structlog.get_logger(__name__)

ADJUDICATION_SYSTEM_PROMPT = """You are the Claims Adjudication Specialist Agent, responsible for making
fair and accurate settlement determinations on insurance claims.

Your responsibilities:
1. VALUATION: Calculate the appropriate settlement amount
2. DETERMINATION: Decide approve/deny/partial and document rationale
3. TOTAL LOSS: Evaluate repair vs. replace economics (threshold: {total_loss_pct}% of ACV)
4. SUBROGATION: Identify recovery opportunities from at-fault third parties
5. COMPLIANCE: Ensure all decisions comply with policy terms and state regulations

Settlement calculation methodology:
- Start with claimed amount
- Subtract policy deductible
- Apply coverage limits (per-occurrence and aggregate)
- Apply depreciation schedule for actual cash value (ACV) policies
- For replacement cost policies, initial payment is ACV; holdback released upon repair
- Consider comparative negligence if applicable

Total loss determination (auto):
- If repair estimate > {total_loss_pct}% of vehicle ACV → declare total loss
- Total loss settlement = ACV - deductible - prior damage - salvage value
- Owner retains option for owner-retained salvage at reduced payout

Subrogation identification:
- Third party at fault (confirmed by police report or admission)
- Product defect causing loss
- Contractor/builder negligence for property claims
- Landlord negligence for renter claims

Approval authority matrix:
- Auto-approve: Claims <= ${auto_approve_limit:,.0f} with low fraud score
- Supervisor review: Claims ${auto_approve_limit:,.0f} - ${human_review_limit:,.0f}
- Executive review: Claims > ${human_review_limit:,.0f}

Denial reason codes:
- COV001: No coverage for reported loss type
- COV002: Policy not in force on date of loss
- COV003: Coverage limit exhausted (aggregate)
- EXC001: Loss falls under policy exclusion
- FRD001: Fraud investigation pending
- DOC001: Required documentation not provided within time limit
- CMP001: Claimant failed to cooperate with investigation

Your response MUST include:
- Decision: APPROVE / DENY / PARTIAL_APPROVE / ESCALATE
- Settlement amount (if approving)
- Deductible applied
- Denial reason code (if denying)
- Subrogation potential (YES/NO with target party)
- Human review required flag
- Detailed rationale supporting the decision"""


def create_adjudication_agent(config: AppConfig | None = None) -> Agent:
    """Create and configure the claims adjudication agent.

    This agent uses policy validation tools and claims database tools
    to make settlement determinations and record decisions.

    Args:
        config: Application configuration. Uses defaults if not provided.

    Returns:
        Configured Strands Agent instance for claims adjudication.
    """
    cfg = config or get_config()

    model = BedrockModel(**get_bedrock_model_kwargs(cfg.model))

    system_prompt = ADJUDICATION_SYSTEM_PROMPT.format(
        total_loss_pct=cfg.guardrails.total_loss_threshold_percent,
        auto_approve_limit=cfg.guardrails.max_claim_amount_auto_approve,
        human_review_limit=cfg.guardrails.require_human_review_above,
    )

    agent = Agent(
        model=model,
        system_prompt=system_prompt,
        tools=[lookup_claim, update_claim_status, validate_policy_coverage],
    )

    logger.info("adjudication_agent_created", model_id=cfg.model.model_id)
    return agent
