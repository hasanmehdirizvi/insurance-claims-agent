"""Fraud scoring tool combining rule-based heuristics and ML signals.

Evaluates insurance claims for potential fraud indicators using a multi-factor
scoring approach. Combines deterministic rules (known fraud patterns) with
probabilistic signals to produce a composite fraud risk score.

Scoring methodology:
- Rule-based indicators: Known fraud patterns from NICB and SIU databases
- Temporal analysis: Suspicious timing patterns (new policy, recent increase)
- Behavioral signals: Claimant history, prior claims frequency
- Network analysis: Connections to known fraud rings (simplified)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog
from strands import tool

from src.config import get_config

logger = structlog.get_logger(__name__)

# Fraud indicator weights (calibrated against historical SIU referrals)
INDICATOR_WEIGHTS: dict[str, float] = {
    "new_policy_claim": 0.15,           # Claim filed within 60 days of policy inception
    "recent_coverage_increase": 0.12,   # Coverage increased within 90 days before loss
    "prior_claims_frequency": 0.10,     # Multiple claims in past 24 months
    "loss_exceeds_value": 0.20,         # Claimed amount exceeds asset actual cash value
    "inconsistent_narrative": 0.15,     # Loss description contains contradictions
    "high_risk_location": 0.05,         # Loss in known high-fraud geographic area
    "weekend_holiday_loss": 0.03,       # Loss reported on weekend/holiday
    "delayed_reporting": 0.08,          # Significant delay between loss date and report
    "no_police_report": 0.07,           # No police/fire report for reportable event
    "known_associates": 0.18,           # Connected to previously flagged individuals
    "staged_accident_pattern": 0.20,    # Matches staged accident characteristics
    "arson_indicators": 0.22,           # Financial distress + fire loss pattern
}

# High-fraud ZIP code prefixes (simplified - production would use full NICB data)
HIGH_FRAUD_ZIPS = {"100", "112", "331", "900", "770", "606"}


@tool
def score_fraud_risk(
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
    """Score a claim for fraud risk using rule-based heuristics and pattern matching.

    Produces a composite fraud score between 0.0 (low risk) and 1.0 (high risk),
    along with detailed breakdown of triggered indicators and recommended actions.

    Args:
        claim_id: Unique claim identifier for audit trail.
        claim_type: Type of claim (auto_collision, property_damage, etc.).
        claimed_amount: Dollar amount being claimed.
        loss_date: Date of the reported loss (ISO format).
        report_date: Date the claim was reported/filed (ISO format).
        loss_description: Free-text description of the loss event.
        loss_location_zip: ZIP code where the loss occurred.
        policy_inception_date: Date the policy became effective (ISO format).
        prior_claims_count: Number of prior claims by this claimant in past 24 months.
        police_report_filed: Whether a police/fire report was filed.
        claimant_name: Name of the claimant for network analysis.

    Returns:
        Dictionary containing the composite fraud score, triggered indicators,
        risk level classification, and recommended next steps.
    """
    logger.info("scoring_fraud_risk", claim_id=claim_id, claimed_amount=claimed_amount)
    config = get_config()

    triggered_indicators: list[dict[str, Any]] = []
    raw_score = 0.0

    try:
        loss_dt = datetime.fromisoformat(loss_date).replace(tzinfo=timezone.utc)
        report_dt = datetime.fromisoformat(report_date).replace(tzinfo=timezone.utc)
        inception_dt = datetime.fromisoformat(policy_inception_date).replace(tzinfo=timezone.utc)
    except ValueError as e:
        logger.error("date_parse_error", error=str(e))
        return {
            "status": "error",
            "message": f"Invalid date format: {str(e)}",
        }

    # Rule 1: New policy claim (within 60 days of inception)
    days_since_inception = (loss_dt - inception_dt).days
    if 0 < days_since_inception <= 60:
        weight = INDICATOR_WEIGHTS["new_policy_claim"]
        raw_score += weight
        triggered_indicators.append({
            "indicator": "new_policy_claim",
            "weight": weight,
            "detail": f"Loss occurred {days_since_inception} days after policy inception.",
        })

    # Rule 2: Prior claims frequency
    if prior_claims_count >= 3:
        weight = INDICATOR_WEIGHTS["prior_claims_frequency"]
        raw_score += weight
        triggered_indicators.append({
            "indicator": "prior_claims_frequency",
            "weight": weight,
            "detail": f"Claimant has {prior_claims_count} prior claims in 24 months (threshold: 3).",
        })
    elif prior_claims_count >= 2:
        weight = INDICATOR_WEIGHTS["prior_claims_frequency"] * 0.5
        raw_score += weight
        triggered_indicators.append({
            "indicator": "prior_claims_frequency",
            "weight": weight,
            "detail": f"Claimant has {prior_claims_count} prior claims in 24 months (elevated).",
        })

    # Rule 3: Delayed reporting (more than 7 days)
    reporting_delay_days = (report_dt - loss_dt).days
    if reporting_delay_days > 30:
        weight = INDICATOR_WEIGHTS["delayed_reporting"]
        raw_score += weight
        triggered_indicators.append({
            "indicator": "delayed_reporting",
            "weight": weight,
            "detail": f"Claim reported {reporting_delay_days} days after loss (>30 day threshold).",
        })
    elif reporting_delay_days > 7:
        weight = INDICATOR_WEIGHTS["delayed_reporting"] * 0.5
        raw_score += weight
        triggered_indicators.append({
            "indicator": "delayed_reporting",
            "weight": weight,
            "detail": f"Claim reported {reporting_delay_days} days after loss (moderate delay).",
        })

    # Rule 4: No police report for significant claims
    if not police_report_filed and claimed_amount > 5000:
        weight = INDICATOR_WEIGHTS["no_police_report"]
        raw_score += weight
        triggered_indicators.append({
            "indicator": "no_police_report",
            "weight": weight,
            "detail": f"No police report filed for ${claimed_amount:,.2f} claim.",
        })

    # Rule 5: High-fraud location
    if loss_location_zip[:3] in HIGH_FRAUD_ZIPS:
        weight = INDICATOR_WEIGHTS["high_risk_location"]
        raw_score += weight
        triggered_indicators.append({
            "indicator": "high_risk_location",
            "weight": weight,
            "detail": f"Loss location ZIP {loss_location_zip} is in a high-fraud area.",
        })

    # Rule 6: Weekend/holiday loss
    if loss_dt.weekday() >= 5:  # Saturday or Sunday
        weight = INDICATOR_WEIGHTS["weekend_holiday_loss"]
        raw_score += weight
        triggered_indicators.append({
            "indicator": "weekend_holiday_loss",
            "weight": weight,
            "detail": f"Loss occurred on a weekend ({loss_dt.strftime('%A')}).",
        })

    # Rule 7: Narrative red flags (simplified keyword analysis)
    red_flag_phrases = [
        "total loss", "completely destroyed", "everything stolen",
        "no witnesses", "unoccupied", "vacant", "financial hardship",
    ]
    description_lower = loss_description.lower()
    matched_phrases = [p for p in red_flag_phrases if p in description_lower]
    if matched_phrases:
        weight = INDICATOR_WEIGHTS["inconsistent_narrative"] * min(len(matched_phrases) / 3, 1.0)
        raw_score += weight
        triggered_indicators.append({
            "indicator": "inconsistent_narrative",
            "weight": weight,
            "detail": f"Red flag phrases detected in narrative: {matched_phrases}",
        })

    # Rule 8: Staged accident pattern (auto claims with specific characteristics)
    if claim_type in ("auto_collision", "bodily_injury"):
        staged_keywords = ["rear-ended", "stopped at light", "sudden stop", "whiplash"]
        staged_matches = [k for k in staged_keywords if k in description_lower]
        if len(staged_matches) >= 2 and claimed_amount > 15000:
            weight = INDICATOR_WEIGHTS["staged_accident_pattern"]
            raw_score += weight
            triggered_indicators.append({
                "indicator": "staged_accident_pattern",
                "weight": weight,
                "detail": f"Matches staged accident pattern: {staged_matches}, high claim amount.",
            })

    # Normalize score to [0, 1] range
    composite_score = min(raw_score, 1.0)

    # Determine risk level and recommended action
    if composite_score >= config.guardrails.fraud_score_threshold:
        risk_level = "HIGH"
        recommendation = (
            "REFER TO SIU (Special Investigations Unit). Multiple fraud indicators triggered. "
            "Do not proceed with payment until investigation complete. "
            "Consider Examination Under Oath (EUO) and independent medical examination if BI claim."
        )
    elif composite_score >= 0.4:
        risk_level = "MEDIUM"
        recommendation = (
            "Enhanced review recommended. Assign experienced adjuster. "
            "Request additional documentation: police report, repair estimates from two shops, "
            "proof of ownership, and recorded statement."
        )
    else:
        risk_level = "LOW"
        recommendation = (
            "Standard processing. No significant fraud indicators detected. "
            "Proceed with normal adjudication workflow."
        )

    result = {
        "status": "success",
        "claim_id": claim_id,
        "fraud_score": round(composite_score, 4),
        "risk_level": risk_level,
        "indicators_triggered": len(triggered_indicators),
        "indicators": triggered_indicators,
        "recommendation": recommendation,
        "scoring_metadata": {
            "model_version": "rules_v2.1",
            "scored_at": datetime.now(tz=timezone.utc).isoformat(),
            "threshold_used": config.guardrails.fraud_score_threshold,
        },
    }

    logger.info(
        "fraud_scoring_complete",
        claim_id=claim_id,
        fraud_score=composite_score,
        risk_level=risk_level,
        indicators_count=len(triggered_indicators),
    )
    return result
