"""GP$ Master Index and Unified Prosperity Equation engines.

State vector Ω(t) = [F, H, S, R, L, T, M, I, E].

Unified Prosperity Equation:

    P = (F + H + S + R + L) × (T + I + M) / E

Capitals create value. Trust, intelligence, and memory amplify and
preserve it. Entropy destroys it. If E is 0 the equation is undefined,
so the function returns None and the caller must say so — it never
invents a prosperity value for a zero-entropy ledger.

GP$ Master Index:

    GP$ = αF + βH + γS + δR + εL + ζT + ηM + θI − λE

The coefficients are LEARNED statistically (regression analysis,
optimization theory, machine learning, Bayesian inference, system
dynamics) — not guessed, measured. Until real user data exists to learn
them, the defaults below are UNCALIBRATED PLACEHOLDERS (equal weight
1.0). They are not measured values and must never be presented as
calibrated.
"""

from __future__ import annotations

# UNCALIBRATED PLACEHOLDER coefficients. Equal weight 1.0 for every
# variable. These are NOT measured values. They exist only so the Master
# Index can be computed and tested before statistical calibration.
DEFAULT_COEFFICIENTS: dict[str, float] = {
    "alpha": 1.0,
    "beta": 1.0,
    "gamma": 1.0,
    "delta": 1.0,
    "epsilon": 1.0,
    "zeta": 1.0,
    "eta": 1.0,
    "theta": 1.0,
    "lam": 1.0,
}

UNCALIBRATED_NOTE = (
    "Coefficients are UNCALIBRATED PLACEHOLDERS (equal weight 1.0). "
    "They are not measured values. Calibration by regression, machine "
    "learning, Bayesian inference, or system dynamics on real user data "
    "is required before this index is presented as calibrated."
)


def unified_prosperity(
    financial: float,
    human: float,
    social: float,
    resource: float,
    legacy: float,
    trust: float,
    memory: float,
    intelligence: float,
    entropy: float,
) -> float | None:
    """Compute P = (F + H + S + R + L) × (T + I + M) / E, rounded to 2.

    Returns None when entropy is 0: the equation is undefined there,
    and no prosperity value is invented for a zero-entropy ledger.
    """
    if entropy == 0:
        return None
    capitals = financial + human + social + resource + legacy
    amplifiers = trust + memory + intelligence
    return round(capitals * amplifiers / entropy, 2)


def master_index(
    financial: float,
    human: float,
    social: float,
    resource: float,
    legacy: float,
    trust: float,
    memory: float,
    intelligence: float,
    entropy: float,
    coefficients: dict[str, float] | None = None,
) -> float:
    """Compute the GP$ Master Index, rounded to 2 decimals.

    Coefficients default to DEFAULT_COEFFICIENTS (uncalibrated
    placeholders). Pass explicit coefficients to compute with any
    other weights.
    """
    c = {**DEFAULT_COEFFICIENTS, **(coefficients or {})}
    value = (
        c["alpha"] * financial
        + c["beta"] * human
        + c["gamma"] * social
        + c["delta"] * resource
        + c["epsilon"] * legacy
        + c["zeta"] * trust
        + c["eta"] * memory
        + c["theta"] * intelligence
        - c["lam"] * entropy
    )
    return round(value, 2)


def calculate_master_index(
    financial: float,
    human: float,
    social: float,
    resource: float,
    legacy: float,
    trust: float,
    memory: float,
    intelligence: float,
    entropy: float,
    coefficients: dict[str, float] | None = None,
) -> dict:
    """Master Index with its prosperity value, components, and the
    calibration warning. `calibrated` is always False until real
    coefficients are learned and supplied by the caller."""
    c = {**DEFAULT_COEFFICIENTS, **(coefficients or {})}
    return {
        "master_index": master_index(
            financial, human, social, resource, legacy, trust, memory,
            intelligence, entropy, c,
        ),
        "prosperity": unified_prosperity(
            financial, human, social, resource, legacy, trust, memory,
            intelligence, entropy,
        ),
        "components": {
            "financial": round(c["alpha"] * financial, 2),
            "human": round(c["beta"] * human, 2),
            "social": round(c["gamma"] * social, 2),
            "resource": round(c["delta"] * resource, 2),
            "legacy": round(c["epsilon"] * legacy, 2),
            "trust": round(c["zeta"] * trust, 2),
            "memory": round(c["eta"] * memory, 2),
            "intelligence": round(c["theta"] * intelligence, 2),
            "entropy": round(-c["lam"] * entropy, 2),
        },
        "coefficients_used": dict(c),
        "calibrated": False,
        "note": UNCALIBRATED_NOTE,
    }
