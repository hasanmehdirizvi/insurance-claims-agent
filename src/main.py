"""Entry point for the insurance claims multi-agent processing system.

Demonstrates the end-to-end claims lifecycle from FNOL intake through
fraud detection and adjudication using coordinated specialist agents.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

import structlog

from src.agents.supervisor import create_supervisor_agent
from src.config import get_config

# Configure structured logging
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO level
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger(__name__)


# Example claim scenarios for demonstration
SAMPLE_CLAIMS = {
    "standard_auto_collision": {
        "description": "Standard auto collision claim - low complexity, clear liability",
        "prompt": """Process this new auto collision claim:

Claimant: Sarah Mitchell
Policy Number: POL-AUTO-2024-7821
Loss Date: 2025-05-10
Loss Location: Intersection of Main St and Oak Ave, Hartford, CT 06103
Claim Type: auto_collision
Estimated Amount: $8,500.00
Contact Phone: (860) 555-0142
Contact Email: sarah.mitchell@email.com

Loss Description:
I was driving northbound on Main Street when another vehicle ran a red light
at the Oak Avenue intersection and struck the front passenger side of my 2022
Honda Accord. Police report #HPD-2025-04521 filed. The other driver was cited
for running the red light. No injuries, but significant front-end damage
requiring bumper, fender, and headlight assembly replacement. Two witnesses
at the scene provided statements to police.

Please process this FNOL, run fraud screening, and adjudicate if appropriate.""",
    },
    "suspicious_total_loss": {
        "description": "Suspicious claim with multiple fraud indicators",
        "prompt": """Process this new comprehensive claim:

Claimant: James Rodriguez
Policy Number: POL-AUTO-2024-9103
Loss Date: 2025-05-03
Loss Location: 450 Industrial Blvd, Newark, NJ 07102
Claim Type: auto_comprehensive
Estimated Amount: $45,000.00
Contact Phone: (973) 555-0298
Contact Email: j.rodriguez99@freemail.com

Loss Description:
My 2023 BMW X5 was completely destroyed by fire while parked overnight in
the industrial district. No witnesses. I discovered it the next morning when
I came to pick it up. The vehicle is a total loss - everything was completely
destroyed. I've been having some financial hardship lately and really need
this resolved quickly. I would prefer a cash settlement. No fire report was
filed as the fire had already burned out by the time I found it.

Note: Policy was purchased 45 days ago. Claimant has 3 prior claims in 24 months.

Please process this FNOL, run fraud screening, and adjudicate if appropriate.""",
    },
    "complex_property_damage": {
        "description": "Homeowners claim with subrogation potential",
        "prompt": """Process this new homeowners property damage claim:

Claimant: Margaret Chen
Policy Number: POL-HOME-2023-4456
Loss Date: 2025-05-08
Loss Location: 22 Maple Lane, Westport, CT 06880
Claim Type: homeowners
Estimated Amount: $62,000.00
Contact Phone: (203) 555-0187
Contact Email: m.chen@lawfirm.com

Loss Description:
A licensed plumber hired to replace the water heater in my basement caused
a pipe burst that flooded the entire lower level of my home. The flooding
destroyed finished basement walls, flooring, a home theater system, stored
personal property, and caused mold growth that required professional
remediation. The plumber's company, QuickFix Plumbing LLC, has acknowledged
the error but their liability insurance is only $25,000 which is insufficient.
I have photos, the plumber's invoice, and a remediation estimate from
ServiceMaster.

Please process this FNOL, run fraud screening, and adjudicate if appropriate.
Note the subrogation potential against QuickFix Plumbing LLC.""",
    },
}


def run_claim_scenario(scenario_key: str) -> None:
    """Run a specific claim processing scenario end-to-end.

    Args:
        scenario_key: Key from SAMPLE_CLAIMS dictionary.
    """
    if scenario_key not in SAMPLE_CLAIMS:
        logger.error("unknown_scenario", scenario_key=scenario_key)
        print(f"Unknown scenario: {scenario_key}")
        print(f"Available scenarios: {list(SAMPLE_CLAIMS.keys())}")
        return

    scenario = SAMPLE_CLAIMS[scenario_key]
    config = get_config()

    print("=" * 80)
    print(f"INSURANCE CLAIMS PROCESSING SYSTEM")
    print(f"Scenario: {scenario['description']}")
    print(f"Model: {config.model.model_id}")
    print(f"Timestamp: {datetime.now(tz=timezone.utc).isoformat()}")
    print("=" * 80)
    print()

    logger.info(
        "starting_claim_scenario",
        scenario=scenario_key,
        model=config.model.model_id,
    )

    # Create the supervisor agent
    supervisor = create_supervisor_agent(config)

    # Process the claim
    print("Submitting claim to Supervisor Agent...")
    print("-" * 40)
    print()

    response = supervisor(scenario["prompt"])

    print()
    print("-" * 40)
    print("PROCESSING COMPLETE")
    print("-" * 40)
    print()
    print(str(response))
    print()

    logger.info("scenario_complete", scenario=scenario_key)


def interactive_mode() -> None:
    """Run the system in interactive mode for custom claim input."""
    config = get_config()

    print("=" * 80)
    print("INSURANCE CLAIMS PROCESSING SYSTEM - Interactive Mode")
    print(f"Model: {config.model.model_id}")
    print("Type 'quit' to exit, 'scenarios' to list demo scenarios")
    print("=" * 80)
    print()

    supervisor = create_supervisor_agent(config)

    while True:
        try:
            user_input = input("\nClaims Supervisor > ").strip()

            if not user_input:
                continue
            if user_input.lower() == "quit":
                print("Exiting claims processing system.")
                break
            if user_input.lower() == "scenarios":
                print("\nAvailable demo scenarios:")
                for key, scenario in SAMPLE_CLAIMS.items():
                    print(f"  - {key}: {scenario['description']}")
                print("\nRun with: python -m src.main <scenario_name>")
                continue

            response = supervisor(user_input)
            print(f"\n{response}")

        except KeyboardInterrupt:
            print("\nExiting claims processing system.")
            break
        except Exception as e:
            logger.error("interactive_error", error=str(e))
            print(f"\nError: {str(e)}")


def main() -> None:
    """Main entry point - handles CLI arguments for scenario or interactive mode."""
    if len(sys.argv) > 1:
        scenario = sys.argv[1]
        if scenario == "--list":
            print("Available scenarios:")
            for key, data in SAMPLE_CLAIMS.items():
                print(f"  {key}: {data['description']}")
        elif scenario == "--interactive":
            interactive_mode()
        else:
            run_claim_scenario(scenario)
    else:
        # Default: run the standard auto collision scenario
        print("Usage:")
        print(f"  python -m src.main <scenario>     Run a specific scenario")
        print(f"  python -m src.main --list         List available scenarios")
        print(f"  python -m src.main --interactive  Interactive mode")
        print()
        print("Running default scenario: standard_auto_collision")
        print()
        run_claim_scenario("standard_auto_collision")


if __name__ == "__main__":
    main()
