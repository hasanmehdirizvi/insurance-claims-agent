"""Claims Intake Agent for FNOL (First Notice of Loss) processing.

Handles the initial intake of insurance claims including:
- Collecting and validating claimant information
- Registering the claim in the claims management system
- Performing initial policy coverage verification
- Triaging claims by severity and type
- Assigning initial priority for downstream processing

This agent is the entry point for all new claims and ensures that
required information is captured before passing to fraud screening.
"""

from __future__ import annotations

import structlog
from strands import Agent
from strands.models.bedrock import BedrockModel

from src.config import AppConfig, get_bedrock_model_kwargs, get_config
from src.tools.claims_db import create_claim, lookup_claim, update_claim_status
from src.tools.policy_validator import validate_policy_coverage

logger = structlog.get_logger(__name__)

INTAKE_SYSTEM_PROMPT = """You are the Claims Intake Specialist Agent, responsible for processing
First Notice of Loss (FNOL) reports for an insurance company.

Your responsibilities:
1. REGISTRATION: Create new claim records with all required FNOL data
2. VALIDATION: Verify policy coverage exists and is active for the reported loss
3. TRIAGE: Assess initial severity and assign processing priority
4. COMPLETENESS: Ensure all required fields are captured before advancing

Required FNOL information:
- Claimant identification (name, contact information)
- Policy number and verification
- Date, time, and location of loss
- Detailed loss description
- Type of loss/damage
- Estimated loss amount
- Police/fire report number (if applicable)
- Witness information (if available)
- Photos/documentation status

Triage priority levels:
- CRITICAL: Bodily injury, fatality, large commercial loss (>$100K)
- HIGH: Total loss vehicle, significant property damage (>$25K)
- MEDIUM: Moderate damage, standard auto collision ($5K-$25K)
- LOW: Minor damage, cosmetic only (<$5K)

Processing rules:
- Always validate policy coverage before registering the claim
- If policy is invalid/expired, note the issue but still register the FNOL
- Flag any immediate red flags (inconsistencies, missing information)
- Set claim status to FNOL_RECEIVED after successful registration
- Provide clear next steps to the supervisor for routing

Respond with structured JSON containing:
- claim_id (if created)
- triage_priority
- coverage_status
- missing_information (if any)
- recommended_next_steps
- red_flags (if any)"""


def create_claims_intake_agent(config: AppConfig | None = None) -> Agent:
    """Create and configure the FNOL claims intake agent.

    This agent has access to claims database tools for registration
    and policy validation tools for coverage verification.

    Args:
        config: Application configuration. Uses defaults if not provided.

    Returns:
        Configured Strands Agent instance for claims intake.
    """
    cfg = config or get_config()

    model = BedrockModel(**get_bedrock_model_kwargs(cfg.model))

    agent = Agent(
        model=model,
        system_prompt=INTAKE_SYSTEM_PROMPT,
        tools=[create_claim, lookup_claim, update_claim_status, validate_policy_coverage],
    )

    logger.info("claims_intake_agent_created", model_id=cfg.model.model_id)
    return agent
