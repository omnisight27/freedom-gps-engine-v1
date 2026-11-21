"""OmniSight -> Freedom GP$ connector, Phase A (EO 007A / Integration
Sprint A): NexIntel history -> Validation Registry.

The missing wire, first span: the OmniSight NexIntel loop already
publishes a complete Prediction -> Outcome -> Calibration loop as
public JSON (data/history.json in the omnisight27/index.html repo).
This module loads that history into the engine through the existing
write paths only:

- one KnowledgeAsset per pick (category "Prediction") via the vault
  endpoint's create path — creating the asset also creates its graph
  node (type KnowledgeAsset) plus the OWNS edge from the entity, by
  the Sprint 3 auto-sync. No parallel graph path is built.
- one Prediction per pick via registry_service.create_prediction,
  on a dedicated Model Registry row, "NexIntel Pick Predictive
  Accuracy" (get-or-created by name, like the seed).
- one Outcome per graded pick via registry_service.create_outcome.
- one CalibrationRecord per graded pick via
  registry_service.create_calibration_from_prediction — CE =
  Observed - Predicted is computed server-side, never here.

What the file actually contains (verified 2026-11-21 against the
live history.json): 90 runs (2026-10-23 -> 2026-11-20), 1,549
individual asset picks. Of those, 1,409 are graded (692 wins /
717 losses), 134 are ungraded (recent runs, exit window not yet
closed), and 6 are excluded (known-bad symbol-collision grades —
the CVX/BTC misgrades the loop itself removed; they are never
imported). The file's aggregate stats claim 1,750 graded picks
(856W / 894L, 48.9% win rate) — 341 graded picks exist only as
aggregates: the picks list keeps the last 90 runs, and the
data/snapshots/ files are market-data dumps, not pick records,
so those 341 individual records are not recoverable from the
repo. The aggregates are recorded losslessly on one Historical
Record asset (see _aggregate_description); they are NOT turned
into CalibrationRecords, because a band summary has no
prediction/outcome pair and would need an invented band-center
convention. The per-pick CalibrationRecords (1,409) are the
evidence; the aggregate asset preserves the loop's own summary,
including the 341 picks that have no individual record.

The loop's published error aggregate (MAE 14.03, n = 1,192,
metric return_pct_v1) does not reproduce from the individual
records: mean |return_pct - expected_return_pct| over the 1,409
graded picks is 15.94. The selection rule for the loop's 1,192
population is not in the file. The engine-side mean |CE| over
the imported calibration records is therefore 15.94, not 14.03
— the two numbers measure different populations, and the
aggregate asset says so.

Idempotency: every pick carries a natural key in the asset's
`source` field — "nexintel:{run_iso}:{direction}:{asset}:{class}".
The class is part of the key because one run graded two different
CVX shorts (crypto Convex Finance and equity Chevron). Re-running
the import never duplicates anything: an existing asset is found
by its source key, an existing prediction by the same source key
embedded in its predicted_outcome (see below), an existing outcome
by its prediction, an existing calibration by its prediction. A
re-run with a fresher history.json resolves picks that were
ungraded on the first run (Outcome + Calibration created, no
duplicate Prediction or asset).

Why the predicted_outcome is an object: the Prediction row has
no source-key column, and matching on (run date, expected return,
confidence) is not unique — the loop repeats the same expected
return (4.0) and confidence (78) across different assets in one
run, so value-matching silently merged distinct picks into one
prediction (verified against the live file: 436 of 1,543 picks
collided on the first implementation). The outcome is therefore
stored as {"value": expected, "asset": ..., "direction": ...,
"class": ..., "source_key": ...}, which is inside the registry's
documented contract: the from-prediction calibration path reads
a top-level number or an object with a numeric "value" key, and
CE is computed from that value. The identity rides along; the
number stays the number.

Deliberately NOT in Phase A (EO 007A: do not add features, scores,
or engines): the Architect's new graph node types (Prediction,
Contract, Grant, Alert, Market Observation, Economic Indicator)
and relationships (PREDICTS, RESOLVES_TO, MATCHES, QUALIFIES_FOR,
GENERATES, INFLUENCES, OBSERVED_AS) are a graph-taxonomy
extension for a later phase — Phase A uses the existing
KnowledgeAsset node + OWNS edge, which is the verified
auto-sync behavior. No Score Registry row is created for the
NexIntel model: there is no Freedom GP$ score formula to quote,
and inventing one would violate the registry's honesty rule.

Read-only on the live data: the import fetches the public JSON
output files. It never calls the loop's upstream APIs and never
touches the loop's keys (they stay in GitHub Secrets).
"""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.vault import (
    create_knowledge_asset as create_knowledge_asset_endpoint,
)
from app.api.vault import (
    update_knowledge_asset as update_knowledge_asset_endpoint,
)
from app.models.entity import Entity
from app.models.registry import CalibrationRecord, Outcome, Prediction, RegistryModel
from app.models.user import User
from app.models.vault import KnowledgeAsset, KnowledgeCategory
from app.schemas.registry import (
    CalibrationFromPredictionRequest,
    ModelCreate,
    OutcomeCreate,
    PredictionCreate,
)
from app.schemas.vault import KnowledgeAssetCreate, KnowledgeAssetUpdate
from app.services.registry_service import (
    create_calibration_from_prediction,
    create_model,
    create_outcome,
    create_prediction,
)

HISTORY_URL = (
    "https://raw.githubusercontent.com/omnisight27/index.html/main/"
    "data/history.json"
)

NEXINTEL_MODEL_NAME = "NexIntel Pick Predictive Accuracy"
NEXINTEL_MODEL_DESCRIPTION = (
    "NexIntel market picks (LONG/SHORT) imported from the OmniSight "
    "NexIntel loop's public history.json. The predicted outcome is "
    "the thesis expected return %; the outcome is the realized "
    "return %. Experimental — validation evidence is being "
    "collected, not yet validated."
)

# Every pick in the history file carries the same horizon:
# "1-5 days (swing)". The registry records durations as "5d".
FORECAST_HORIZON = "5d"

AGGREGATE_SOURCE_KEY = "nexintel:history.json:aggregates"
AGGREGATE_TITLE = "NexIntel graded history — aggregate statistics"


def pick_source_key(
    run_iso: str, direction: str, asset: str, asset_class: str
) -> str:
    """The natural key of one pick, stored in the asset's `source`
    field. The class is part of the key: one run graded two
    different CVX shorts (crypto Convex Finance, equity Chevron)."""
    return f"nexintel:{run_iso}:{direction}:{asset}:{asset_class}"


def parse_run_date(value: str) -> datetime:
    """Parse a run timestamp (ISO 8601 with offset) to an
    aware UTC datetime."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _num(value) -> float | None:
    """A JSON number, or None when the value is not a number
    (booleans are not numbers here)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


async def ensure_nexintel_model(
    db: AsyncSession, user: User
) -> RegistryModel:
    """Get-or-create the Model Registry row the NexIntel
    predictions belong to. Idempotent by name, like the seed:
    a second call returns the existing row, never a duplicate."""
    result = await db.execute(
        select(RegistryModel).where(
            RegistryModel.name == NEXINTEL_MODEL_NAME
        )
    )
    existing = result.scalar_one_or_none()
    if existing is not None:
        return existing
    model = await create_model(
        db,
        user,
        ModelCreate(
            name=NEXINTEL_MODEL_NAME,
            version="1.0",
            description=NEXINTEL_MODEL_DESCRIPTION,
            validation_level="Experimental",
            status="active",
            owner="NexIntel",
        ),
    )
    await db.flush()
    return model


async def find_asset_by_source(
    db: AsyncSession, entity_id: int, source_key: str
) -> KnowledgeAsset | None:
    result = await db.execute(
        select(KnowledgeAsset).where(
            KnowledgeAsset.entity_id == entity_id,
            KnowledgeAsset.source == source_key,
        )
    )
    return result.scalar_one_or_none()


async def find_prediction_for_pick(
    db: AsyncSession,
    entity_id: int,
    model_id: int,
    source_key: str,
) -> Prediction | None:
    """Find the prediction for one pick by the source key
    embedded in its predicted_outcome.

    The Prediction row has no source-key column, and matching
    on (run date, expected return, confidence) is NOT unique:
    the loop repeats the same expected return (4.0) and
    confidence (78) across different assets in one run, so
    value-matching silently merged distinct picks into one
    prediction (verified against the live file: 436 of 1,543
    picks collided). The predicted_outcome is therefore stored
    as an object — {"value": expected, "asset": ..., "direction":
    ..., "class": ..., "source_key": ...} — which is inside the
    registry's documented contract: the from-prediction
    calibration path reads a top-level number or an object with
    a numeric "value" key, and CE is computed from that value.
    The identity rides along; the number stays the number."""
    result = await db.execute(
        select(Prediction).where(
            Prediction.entity_id == entity_id,
            Prediction.model_id == model_id,
        )
    )
    for pred in result.scalars().all():
        outcome = pred.predicted_outcome
        if isinstance(outcome, dict) and outcome.get("source_key") == source_key:
            return pred
    return None


async def find_outcome_for_prediction(
    db: AsyncSession, prediction_id: int
) -> Outcome | None:
    result = await db.execute(
        select(Outcome).where(Outcome.prediction_id == prediction_id)
    )
    return result.scalar_one_or_none()


async def find_calibration_for_prediction(
    db: AsyncSession, prediction_id: int
) -> CalibrationRecord | None:
    result = await db.execute(
        select(CalibrationRecord).where(
            CalibrationRecord.prediction_id == prediction_id
        )
    )
    return result.scalar_one_or_none()


def _pick_description(
    pick: dict,
    direction: str,
    expected_return: float,
    graded: bool,
) -> str:
    """The asset's description: the loop's own reason plus the
    entry/exit facts, so the record stands alone without the
    source file."""
    thesis = pick.get("thesis") or {}
    parts = [str(pick.get("reason") or "").strip()]
    entry = _num(pick.get("price"))
    stop = _num(thesis.get("stop_loss"))
    target = _num(thesis.get("take_profit"))
    facts = (
        f"Entry ${entry} ({pick.get('class')}); "
        f"stop ${stop}; target ${target}; "
        f"expected return {expected_return}%; "
        f"horizon {thesis.get('time_horizon') or '1-5 days (swing)'}; "
        f"NexIntel score {pick.get('score')}; "
        f"regime {pick.get('regime')}; "
        f"calibration band {pick.get('calib_band')}."
    )
    parts.append(facts)
    if graded:
        exit_price = _num(pick.get("exit"))
        parts.append(
            f"Graded: win={str(pick.get('win')).lower()}, "
            f"realized return {pick.get('return_pct')}%, "
            f"exit ${exit_price}."
        )
    return " ".join(p for p in parts if p)


def _aggregate_description(history: dict, counts: dict) -> str:
    """The aggregate asset's description: the loop's own summary
    numbers, verbatim, plus the reconciliation against the
    individual records this import actually created. The
    individual-record counts come from this run, so a re-run
    with a fresher file refreshes them (and the asset's version
    increments through the existing update path)."""
    stats = history.get("stats") or {}
    error = history.get("error") or {}
    calibration = history.get("calibration") or []
    calibration_v2 = history.get("calibration_v2") or []
    corrections = history.get("corrections") or []
    lines = [
        "NexIntel graded-history aggregates as published by the "
        "OmniSight NexIntel loop in data/history.json. These are "
        "the loop's own summary numbers — they are NOT computed "
        "by Freedom GP$.",
        "",
        f"Graded picks (loop total): {stats.get('total')} "
        f"({stats.get('wins')} wins / {stats.get('losses')} losses, "
        f"{stats.get('win_rate')}% win rate).",
        f"Model error ({error.get('metric')}): MAE "
        f"{error.get('mae')} over n={error.get('n')} "
        f"(sum |error| {error.get('sum_abs_err')}, "
        f"within 5%: {error.get('within_5pct')} "
        f"({error.get('within_5pct_rate')}%)).",
        "",
        "Calibration bands (v1, confidence band -> observed win "
        "rate): "
        + "; ".join(
            f"{b.get('band')}: n={b.get('n')}, {b.get('win_rate')}%"
            for b in calibration
        )
        + ".",
        "Calibration bands (v2, direction x band): "
        + "; ".join(
            f"{b.get('direction') or 'ALL'} {b.get('band')}: "
            f"n={b.get('n')}, {b.get('win_rate')}%"
            for b in calibration_v2
        )
        + ".",
        "",
        "Corrections applied by the loop: "
        + "; ".join(
            f"{c.get('date', '')[:10]}: {c.get('action')} "
            f"(picks removed: {c.get('picks_removed')})"
            for c in corrections
        )
        + ".",
        "",
        "Reconciliation with the individual records in this file: "
        f"the picks list holds {counts.get('picks_seen', 0)} asset "
        f"picks across {counts.get('runs_seen', 0)} runs, of which "
        f"{counts.get('graded_seen', 0)} graded, "
        f"{counts.get('ungraded_seen', 0)} ungraded, and "
        f"{counts.get('skipped_excluded', 0)} excluded "
        "(known-bad symbol-collision grades — never imported). "
        "The loop total (1,750 graded) exceeds the individual "
        "graded records in the file (1,409): the picks list keeps "
        "the last 90 runs only, and data/snapshots/ are "
        "market-data dumps, not pick records, so the remaining "
        "~341 graded picks exist only as aggregates here. "
        "The loop's error population (n=1,192, MAE 14.03) does "
        "not reproduce from the individual records — mean "
        "|return - expected| over the 1,409 graded picks is "
        "15.94 — so the engine-side mean |CE| over the imported "
        "calibration records is 15.94, and the two numbers "
        "measure different populations.",
    ]
    return "\n".join(lines)


def fetch_history(url: str = HISTORY_URL) -> dict:
    """Fetch the live history file (read-only). The loop's keys
    stay in GitHub Secrets — this reads the public output file,
    never the upstream APIs."""
    request = urllib.request.Request(
        url, headers={"User-Agent": "Freedom-GP$-nexintel-import/1.0"}
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def load_history(source: str) -> dict:
    """Load history.json from a URL or a local file path."""
    if source.startswith(("http://", "https://")):
        return fetch_history(source)
    with open(source, "r", encoding="utf-8") as handle:
        return json.load(handle)


async def import_nexintel_history(
    db: AsyncSession,
    user: User,
    entity: Entity,
    history: dict,
    *,
    source_url: str = HISTORY_URL,
) -> dict:
    """Import one NexIntel history file into the Validation
    Registry. Idempotent: re-running with the same (or a
    fresher) file never duplicates an asset, prediction,
    outcome, or calibration record, and resolves picks that
    were ungraded on the first run. Returns the counts."""
    counts = {
        "runs_seen": 0,
        "picks_seen": 0,
        "graded_seen": 0,
        "ungraded_seen": 0,
        "skipped_excluded": 0,
        "skipped_incomplete": 0,
        "skipped_duplicate": 0,
        "knowledge_assets_created": 0,
        "predictions_created": 0,
        "outcomes_created": 0,
        "calibrations_created": 0,
        "resolved_outcomes": 0,
        "resolved_calibrations": 0,
        "aggregate_created": 0,
        "aggregate_updated": 0,
    }
    model = await ensure_nexintel_model(db, user)
    await db.commit()

    runs = history.get("picks") or []
    for run in runs:
        counts["runs_seen"] += 1
        run_iso = str(run.get("date") or "")
        if not run_iso:
            continue
        run_date = parse_run_date(run_iso)
        for side in ("longs", "shorts"):
            direction = "LONG" if side == "longs" else "SHORT"
            for pick in run.get(side) or []:
                counts["picks_seen"] += 1
                if pick.get("excluded"):
                    # Known-bad grade (symbol collision). The
                    # loop removed it from its own record; the
                    # engine must not import it as evidence.
                    counts["skipped_excluded"] += 1
                    continue
                asset_symbol = pick.get("asset")
                thesis = pick.get("thesis") or {}
                expected_return = _num(thesis.get("expected_return_pct"))
                confidence_raw = _num(pick.get("confidence"))
                asset_class = str(pick.get("class") or "unknown")
                if (
                    not asset_symbol
                    or expected_return is None
                    or confidence_raw is None
                ):
                    # Missing a field the registries require.
                    # Skipped, counted, never guessed.
                    counts["skipped_incomplete"] += 1
                    continue
                confidence = min(max(confidence_raw / 100.0, 0.0), 1.0)
                graded = pick.get("win") is not None
                if graded:
                    counts["graded_seen"] += 1
                else:
                    counts["ungraded_seen"] += 1

                source_key = pick_source_key(
                    run_iso, direction, str(asset_symbol), asset_class
                )
                created_anything = False
                asset = await find_asset_by_source(
                    db, entity.id, source_key
                )
                asset_existed = asset is not None
                if asset is None:
                    asset = await create_knowledge_asset_endpoint(
                        KnowledgeAssetCreate(
                            entity_id=entity.id,
                            title=(
                                f"NexIntel {direction} {asset_symbol} "
                                f"({asset_class}, "
                                f"{run_date.date().isoformat()})"
                            ),
                            description=_pick_description(
                                pick,
                                direction,
                                expected_return,
                                graded,
                            ),
                            category=KnowledgeCategory.PREDICTION,
                            content_type="nexintel-pick",
                            confidence=confidence,
                            source=source_key,
                        ),
                        current_user=user,
                        db=db,
                    )
                    counts["knowledge_assets_created"] += 1
                    created_anything = True

                pred = await find_prediction_for_pick(
                    db,
                    entity.id,
                    model.model_id,
                    source_key,
                )
                if pred is None:
                    pred = await create_prediction(
                        db,
                        user,
                        PredictionCreate(
                            entity_id=entity.id,
                            model_id=model.model_id,
                            forecast_horizon=FORECAST_HORIZON,
                            predicted_outcome={
                                "value": expected_return,
                                "asset": str(asset_symbol),
                                "direction": direction,
                                "class": asset_class,
                                "source_key": source_key,
                            },
                            confidence=confidence,
                        ),
                    )
                    # The registry defaults prediction_date to
                    # now(); the evidence is dated when the loop
                    # made the pick, so the source timestamp is
                    # preserved on the existing column.
                    pred.prediction_date = run_date
                    await db.flush()
                    counts["predictions_created"] += 1
                    created_anything = True

                if graded:
                    return_pct = _num(pick.get("return_pct"))
                    outcome = await find_outcome_for_prediction(
                        db, pred.prediction_id
                    )
                    outcome_new = False
                    if outcome is None and return_pct is not None:
                        outcome = await create_outcome(
                            db,
                            user,
                            OutcomeCreate(
                                prediction_id=pred.prediction_id,
                                actual_result=return_pct,
                                notes=(
                                    f"NexIntel graded: "
                                    f"win={str(pick.get('win')).lower()}, "
                                    f"exit=${pick.get('exit')}, "
                                    f"{direction} {asset_symbol} "
                                    f"({asset_class})."
                                ),
                            ),
                        )
                        counts["outcomes_created"] += 1
                        created_anything = True
                        outcome_new = True
                    if outcome is not None:
                        calibration = (
                            await find_calibration_for_prediction(
                                db, pred.prediction_id
                            )
                        )
                        if calibration is None:
                            await create_calibration_from_prediction(
                                db,
                                user,
                                CalibrationFromPredictionRequest(
                                    prediction_id=pred.prediction_id,
                                    outcome_id=outcome.outcome_id,
                                ),
                            )
                            counts["calibrations_created"] += 1
                            created_anything = True
                            if asset_existed and outcome_new:
                                # Resolved on a re-run: the pick
                                # was ungraded when first
                                # imported, and is graded now.
                                counts["resolved_outcomes"] += 1
                                counts["resolved_calibrations"] += 1
                if not created_anything:
                    counts["skipped_duplicate"] += 1
                await db.commit()

    # The loop's own aggregates, recorded losslessly on one
    # Historical Record asset. A re-run with a fresher file
    # updates it through the existing update path (version
    # increments); an unchanged file leaves it alone.
    aggregate_text = _aggregate_description(history, counts)
    aggregate = await find_asset_by_source(
        db, entity.id, AGGREGATE_SOURCE_KEY
    )
    if aggregate is None:
        await create_knowledge_asset_endpoint(
            KnowledgeAssetCreate(
                entity_id=entity.id,
                title=AGGREGATE_TITLE,
                description=aggregate_text,
                category=KnowledgeCategory.HISTORICAL_RECORD,
                content_type="nexintel-history-aggregates",
                source=AGGREGATE_SOURCE_KEY,
            ),
            current_user=user,
            db=db,
        )
        counts["aggregate_created"] = 1
    elif aggregate.description != aggregate_text:
        await update_knowledge_asset_endpoint(
            aggregate.id,
            KnowledgeAssetUpdate(description=aggregate_text),
            current_user=user,
            db=db,
        )
        counts["aggregate_updated"] = 1

    return counts
