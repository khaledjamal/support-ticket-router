"""The three questions asked about every ticket, in one Jev round trip."""

from typesafe_sdk import Choice, Noul, Score

CATEGORY = Choice(
    instructions="Which team should handle this support ticket?",
    criteria={
        "billing": "Charges, invoices, refunds, payments, pricing, subscription plan or credit card problems",
        "technical": "Bugs, errors, crashes, outages, slow performance, or integration problems with the product",
        "account": "Account access, login, password reset, two-factor, profile settings, users or permissions",
        "shipping": "Orders, delivery, tracking, shipment, package, damaged or missing items",
        "feedback": "Feature requests, suggestions, compliments or general product feedback",
    },
)

URGENCY_LEVELS = ("can wait", "low", "normal", "high", "critical")
URGENCY = Score(
    instructions="How urgent is this ticket?",
    criteria=[
        "Can wait: a question, suggestion or feedback with no impact on the customer",
        "Low: a minor inconvenience with a workaround available",
        "Normal: a problem affecting a single user that should be fixed this week",
        "High: blocks the customer's work, or money was charged incorrectly; needs attention today",
        "Critical: outage, data loss, security breach or many users down; needs attention immediately",
    ],
)

CHURN_RISK = Noul(
    instructions="Is this customer at risk of cancelling or leaving?",
    criteria={
        "true": "Angry or frustrated; threatens to cancel, leave, or switch to a competitor",
        "false": "Calm or satisfied; simply asking for help or giving feedback",
    },
)

QUESTIONS = {"category": CATEGORY, "urgency": URGENCY, "churn_risk": CHURN_RISK}

GATED = ("category", "urgency")
"""The answers acted on, so they must be present. Churn risk is never a reason to stop automation on its own:
threshold routing ignores it, and cost routing uses it only to raise the stakes of a mistake."""

AT_RISK_PROBABILITY = 0.7


def urgency_level(score: float) -> str:
    """Name the rubric level nearest to Jev's expected score, which can fall between levels."""
    return URGENCY_LEVELS[min(max(round(score), 0), len(URGENCY_LEVELS) - 1)]
