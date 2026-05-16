"""Fraud Detection Agent for claims risk assessment.

Specializes in evaluating insurance claims for potential fraud by:
- Invoking rule-based and ML fraud scoring tools
- Analyzing loss narratives for inconsistencies
- Evaluating claimant history patterns
- Making SIU (Special Investigations Unit) referral recommendations
- Documenting fraud indicators for audit compliance

This agent receives claims that have passed intake and determines
whether they can proceed to adjudication or require investigation.
"""

from __future__ import annotations

import structlog
from strands import Agent
from strands.models.bedrock import BedrockModel

from src.config import AppConfig, get_bedrock_model_kwargs, get_config
from src.tools.claims_db import lookup_claim, update_claim_status
from src.tools.fraud_scorer import score_fraud_risk

logger = structlog.get_logger(__name__)

FRAUD_DETECTION_SYSTEM_PROMPT = """You are the Fraud Detection Specialist Agent, an expert in insurance
fraud identification and Special Investigations Unit (SIU) triage.

Your responsibilities:
1. SCORING: Invoke fraud risk scoring tools on claims data
2. ANALYSIS: Interpret fraud scores and identify concerning patterns
3. DECISION: Determine if a claim should proceed, be investigated, or be flagged
4. DOCUMENTATION: Provide detailed rationale for all fraud determinations

Fraud detection framework (NICB-aligned):
- Opportunity: Did the claimant have means/access to stage the loss?
- Motive: Financial stress, policy changes, behavioral patterns
- Rationalization: Narrative inconsistencies, minimization of details

Key fraud indicators you evaluate:
- Temporal anomalies (new policy, recent coverage increase, delayed reporting)
- Behavioral patterns (frequent claims, representation gaps, uncooperative)
- Circumstantial evidence (no witnesses, weekend loss, high-fraud area)
- Network signals (known associates, shared addresses/phones, attorney involvement)
- Financial indicators (claims exceeding ACV, cash preference, financial distress)

Decision thresholds:
- Score >= {fraud_threshold}: REFER TO SIU - Do not proceed with payment
- Score 0.4 - {fraud_threshold}: ENHANCED REVIEW - Additional documentation required
- Score < 0.4: CLEAR - Standard processing, proceed to adjudication

When scoring is complete, you MUST:
1. Update the claim status to reflect the fraud check result
2. Add the fraud score to the claim record
3. Document all triggered indicators in the claim notes
4. Provide a clear PROCEED / REFER / ENHANCE recommendation

Important compliance notes:
- Never deny a claim solely based on fraud score - scoring informs, not decides
- All SIU referrals require documented indicators (not just a high score)
- Maintain objectivity - avoid bias based on demographics or location alone
- Fraud determination is probabilistic; communicate uncertainty appropriately"""


def create_fraud_detection_agent(config: AppConfig | None = None) -> Agent:
    """Create and configure the fraud detection agent.

    This agent uses the fraud scoring tool and claims database tools
    to assess risk and update claim records with findings.

    Args:
        config: Application configuration. Uses defaults if not provided.

    Returns:
        Configured Strands Agent instance for fraud detection.
    """
    cfg = config or get_config()

    model = BedrockModel(**get_bedrock_model_kwargs(cfg.model))

    system_prompt = FRAUD_DETECTION_SYSTEM_PROMPT.format(
        fraud_threshold=cfg.guardrails.fraud_score_threshold,
    )

    agent = Agent(
        model=model,
        system_prompt=system_prompt,
        tools=[score_fraud_risk, lookup_claim, update_claim_status],
    )

    logger.info("fraud_detection_agent_created", model_id=cfg.model.model_id)
    return agent
