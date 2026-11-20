"""Freedom GP$ scoring engines.

Each engine is a pure function: numbers in, score out. No database
access, no network calls, so every engine is unit-testable in isolation.
"""

from app.engines.anti_fragility import anti_fragility_index, anti_fragility_rating
from app.engines.asset_flipper import asset_flipper_rating, asset_flipper_score
from app.engines.broken_safe_haven import (
    broken_safe_haven_index,
    broken_safe_haven_rating,
)
from app.engines.credit_health import credit_health_rating, credit_health_score
from app.engines.faustian import faustian_rating, faustian_ratio
from app.engines.regulatory_friction import (
    regulatory_friction_rating,
    regulatory_friction_score,
)
from app.engines.revaluation_gap import revaluation_gap, revaluation_gap_rating
from app.engines.shadow_ledger import hidden_asset_multiplier, hidden_asset_value
from app.engines.sovereignty import sovereignty_rating, sovereignty_score
from app.engines.stinnes import stinnes_rating, stinnes_score
from app.engines.wealth import wealth_index

__all__ = [
    "anti_fragility_index",
    "anti_fragility_rating",
    "asset_flipper_rating",
    "asset_flipper_score",
    "broken_safe_haven_index",
    "broken_safe_haven_rating",
    "credit_health_rating",
    "credit_health_score",
    "faustian_rating",
    "faustian_ratio",
    "hidden_asset_multiplier",
    "hidden_asset_value",
    "regulatory_friction_rating",
    "regulatory_friction_score",
    "revaluation_gap",
    "revaluation_gap_rating",
    "sovereignty_rating",
    "sovereignty_score",
    "stinnes_rating",
    "stinnes_score",
    "wealth_index",
]
