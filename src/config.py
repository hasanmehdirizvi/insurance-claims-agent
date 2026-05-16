"""Configuration for the insurance claims multi-agent system.

Centralizes model configuration, AWS resource settings, and operational
parameters. Uses pydantic-settings for environment variable overrides.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings


class ClaimStatus(str, Enum):
    """Lifecycle states for an insurance claim."""

    FNOL_RECEIVED = "fnol_received"
    UNDER_REVIEW = "under_review"
    FRAUD_CHECK = "fraud_check"
    COVERAGE_VERIFIED = "coverage_verified"
    ADJUDICATION = "adjudication"
    APPROVED = "approved"
    DENIED = "denied"
    SUBROGATION = "subrogation"
    CLOSED = "closed"


class ClaimType(str, Enum):
    """Types of insurance claims supported."""

    AUTO_COLLISION = "auto_collision"
    AUTO_COMPREHENSIVE = "auto_comprehensive"
    PROPERTY_DAMAGE = "property_damage"
    BODILY_INJURY = "bodily_injury"
    UNINSURED_MOTORIST = "uninsured_motorist"
    PERSONAL_INJURY_PROTECTION = "pip"
    HOMEOWNERS = "homeowners"
    COMMERCIAL_PROPERTY = "commercial_property"


class ModelConfig(BaseSettings):
    """Bedrock model configuration for agents."""

    model_id: str = Field(
        default="us.anthropic.claude-sonnet-4-20250514",
        description="Bedrock model ID for agent inference",
    )
    region: str = Field(
        default="us-west-2",
        description="AWS region for Bedrock API calls",
    )
    max_tokens: int = Field(
        default=4096,
        description="Maximum tokens in model response",
    )
    temperature: float = Field(
        default=0.1,
        description="Sampling temperature (low for deterministic claim decisions)",
    )
    top_p: float = Field(
        default=0.9,
        description="Nucleus sampling parameter",
    )

    class Config:
        env_prefix = "CLAIMS_MODEL_"


class DynamoDBConfig(BaseSettings):
    """DynamoDB table configuration."""

    claims_table: str = Field(
        default="insurance-claims",
        description="DynamoDB table for claims data",
    )
    policies_table: str = Field(
        default="insurance-policies",
        description="DynamoDB table for policy data",
    )
    fraud_signals_table: str = Field(
        default="fraud-signals",
        description="DynamoDB table for fraud signal history",
    )
    region: str = Field(
        default="us-east-1",
        description="AWS region for DynamoDB tables",
    )
    endpoint_url: str | None = Field(
        default=None,
        description="Custom endpoint URL (for local development with DynamoDB Local)",
    )

    class Config:
        env_prefix = "CLAIMS_DYNAMO_"


class GuardrailsConfig(BaseSettings):
    """Guardrails and safety configuration."""

    max_claim_amount_auto_approve: float = Field(
        default=10_000.00,
        description="Maximum claim amount for automatic approval without human review",
    )
    fraud_score_threshold: float = Field(
        default=0.7,
        description="Fraud score above which claims are flagged for SIU review",
    )
    total_loss_threshold_percent: float = Field(
        default=75.0,
        description="Repair cost as % of ACV that triggers total loss declaration",
    )
    max_agent_iterations: int = Field(
        default=10,
        description="Maximum reasoning iterations before forcing a decision",
    )
    require_human_review_above: float = Field(
        default=50_000.00,
        description="Claims above this amount always require human adjuster review",
    )

    class Config:
        env_prefix = "CLAIMS_GUARDRAILS_"


class ObservabilityConfig(BaseSettings):
    """Observability and logging configuration."""

    log_level: str = Field(default="INFO", description="Logging level")
    enable_tracing: bool = Field(
        default=True,
        description="Enable distributed tracing via AWS X-Ray",
    )
    metrics_namespace: str = Field(
        default="InsuranceClaimsAgent",
        description="CloudWatch metrics namespace",
    )

    class Config:
        env_prefix = "CLAIMS_OBSERVABILITY_"


class AppConfig(BaseSettings):
    """Root application configuration aggregating all sub-configs."""

    model: ModelConfig = Field(default_factory=ModelConfig)
    dynamodb: DynamoDBConfig = Field(default_factory=DynamoDBConfig)
    guardrails: GuardrailsConfig = Field(default_factory=GuardrailsConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)

    class Config:
        env_prefix = "CLAIMS_"


def get_config() -> AppConfig:
    """Load and return the application configuration singleton."""
    return AppConfig()


def get_bedrock_model_kwargs(config: ModelConfig | None = None) -> dict[str, Any]:
    """Build kwargs dict for BedrockModel instantiation.

    Returns:
        Dictionary compatible with strands_agents.models.bedrock.BedrockModel constructor.
    """
    cfg = config or ModelConfig()
    return {
        "model_id": cfg.model_id,
        "region_name": cfg.region,
        "additional_request_fields": {
            "inferenceConfig": {
                "maxTokens": cfg.max_tokens,
                "temperature": cfg.temperature,
                "topP": cfg.top_p,
            }
        },
    }
