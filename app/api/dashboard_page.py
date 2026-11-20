"""The Sovereign Dashboard page — Engine v1 Sprint 6.

The literal "first window into the engine": one HTML page with
six sections, rendered from the SAME dashboard service data as
the JSON endpoints. No JS framework, no external assets, no
direct database access, no new scoring. Rendered from a plain
template string (Jinja2 is not a project dependency and the
brief allows this approach) with html.escape on every
user-controlled string.

Sections: Executive Overview, Complete Ledger, State Vector
Explorer, Opportunity Center, Knowledge Vault, Validation
Center.
"""

from __future__ import annotations

from html import escape as _esc

from app.schemas.dashboard import (
    ExecutiveOverviewOut,
    LedgerOut,
    OpportunityCenterOut,
    StateVectorExplorerOut,
    ValidationCenterOut,
    VaultOut,
)
from app.services.dashboard_service import COMPONENTS


def _fmt(value: object) -> str:
    """A number or value for display; unmeasured reads as —."""
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:,.2f}"
    return _esc(str(value))


def _section(title: str, section_id: str, body: str) -> str:
    return (
        f'<section id="{section_id}">'
        f"<h2>{_esc(title)}</h2>{body}</section>"
    )


def _table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def render_dashboard_page(
    overview: ExecutiveOverviewOut,
    ledger: LedgerOut,
    explorer: StateVectorExplorerOut,
    opportunities: OpportunityCenterOut,
    vault: VaultOut,
    validation: ValidationCenterOut,
) -> str:
    """Render the six dashboard modules as one HTML page."""
    entity = overview.entity
    sv = overview.state_vector

    # Module 1 — Executive Overview
    sv_line = (
        f"Latest state vector recorded "
        f"{_esc(str(sv.recorded_at))}."
        if sv is not None
        else "No state vector recorded yet."
    )
    validation_line = ", ".join(
        f"{_esc(level)}: {count}"
        for level, count in sorted(overview.validation_status.items())
    ) or "No models registered."
    overview_body = (
        f"<p><strong>Entity:</strong> {_esc(entity.name)} "
        f"({_esc(entity.type)})</p>"
        f"<p>{sv_line}</p>"
        f"<p><strong>Open opportunities:</strong> "
        f"{overview.opportunity_count}</p>"
        f"<p><strong>Knowledge assets:</strong> "
        f"{overview.knowledge_assets_count} · "
        f"<strong>Legacy assets:</strong> "
        f"{overview.legacy_assets_count} · "
        f"<strong>Skills:</strong> {overview.skills_count} · "
        f"<strong>Certifications:</strong> "
        f"{overview.certifications_count}</p>"
        f"<p><strong>Validation status:</strong> {validation_line}</p>"
    )

    # Module 2 — Complete Ledger
    ledger_rows = [
        [
            _esc(cap.name),
            _fmt(cap.value),
            _esc(cap.trend),
            _fmt(cap.delta),
        ]
        for cap in ledger.capitals
    ]
    ledger_body = _table(
        ["Capital", "Value", "Trend", "Change"],
        ledger_rows,
    ) + (
        "<p>Trend indicators only — read from Ω(t) history, "
        "not scored.</p>"
    )

    # Module 3 — State Vector Explorer
    if explorer.current is not None:
        current_rows = [
            [
                _esc(letter),
                _esc(name),
                _fmt(getattr(explorer.current, key)),
            ]
            for key, letter, name in COMPONENTS
        ]
        explorer_body = _table(
            ["Ω(t)", "Component", "Value"], current_rows
        )
        if explorer.changes:
            change_rows = [
                [
                    _esc(c.component),
                    _esc(c.name),
                    _fmt(c.previous),
                    _fmt(c.current),
                    _fmt(c.delta),
                ]
                for c in explorer.changes
            ]
            explorer_body += (
                "<h3>Latest changes</h3>"
                + _table(
                    ["Ω(t)", "Component", "Previous", "Current",
                     "Change"],
                    change_rows,
                )
            )
        else:
            explorer_body += (
                "<p>No changes since the previous snapshot.</p>"
            )
        explorer_body += (
            f"<p>{len(explorer.history)} snapshot(s) recorded.</p>"
        )
    else:
        explorer_body = "<p>No state vector recorded yet.</p>"

    # Module 4 — Opportunity Center
    def _opp_rows(opps) -> list[list[str]]:
        return [
            [
                _esc(o.title),
                _esc(o.opportunity_type),
                _fmt(o.confidence),
                _esc(o.detector),
                _esc(o.source_type or "—"),
                _esc(o.description or ""),
            ]
            for o in opps
        ]

    opp_body = (
        f"<h3>Open ({opportunities.open_count})</h3>"
        + (
            _table(
                ["Opportunity", "Type", "Confidence", "Detector",
                 "Source", "Reason"],
                _opp_rows(opportunities.open),
            )
            if opportunities.open
            else "<p>No open opportunities.</p>"
        )
        + f"<h3>Dismissed ({opportunities.dismissed_count})</h3>"
        + (
            _table(
                ["Opportunity", "Type", "Confidence", "Detector",
                 "Source", "Reason"],
                _opp_rows(opportunities.dismissed),
            )
            if opportunities.dismissed
            else "<p>No dismissed opportunities.</p>"
        )
        + f"<h3>Resolved ({opportunities.resolved_count})</h3>"
        + (
            _table(
                ["Opportunity", "Type", "Confidence", "Detector",
                 "Source", "Reason"],
                _opp_rows(opportunities.resolved),
            )
            if opportunities.resolved
            else "<p>No resolved opportunities.</p>"
        )
    )

    # Module 5 — Knowledge Vault
    vault_rows = [
        [_esc(a.title), _esc(a.category), _esc(a.status)]
        for a in vault.knowledge_assets
    ]
    vault_body = (
        f"<p>Knowledge assets: {vault.knowledge_assets_count} · "
        f"Skills: {vault.skills_count} · Certifications: "
        f"{vault.certifications_count} · Family records: "
        f"{vault.family_records_count} · Legacy assets: "
        f"{vault.legacy_assets_count} · Evidence: "
        f"{vault.evidence_count}</p>"
    )
    if vault_rows:
        vault_body += _table(
            ["Knowledge asset", "Category", "Status"], vault_rows
        )
    if vault.skills:
        vault_body += "<h3>Skills</h3>" + _table(
            ["Skill", "Proficiency", "Verified"],
            [
                [
                    _esc(s.name),
                    _esc(s.proficiency or "—"),
                    "yes" if s.verified else "no",
                ]
                for s in vault.skills
            ],
        )
    if vault.certifications:
        vault_body += "<h3>Certifications</h3>" + _table(
            ["Certification", "Issuer"],
            [
                [_esc(c.title), _esc(c.issuer or "—")]
                for c in vault.certifications
            ],
        )

    # Module 6 — Validation Center
    model_rows = [
        [_esc(m.name), _esc(m.version), _esc(m.validation_level)]
        for m in validation.models
    ]
    validation_body = (
        f"<p>Models: {len(validation.models)} · Predictions: "
        f"{len(validation.predictions)} · Outcomes: "
        f"{len(validation.outcomes)} · Calibration records: "
        f"{len(validation.calibration_records)} · Drift records: "
        f"{len(validation.drift_records)}</p>"
        "<p>Most apps hide uncertainty. Freedom GP$ exposes it: "
        "every model below carries its validation level.</p>"
    )
    if model_rows:
        validation_body += _table(
            ["Model", "Version", "Validation level"], model_rows
        )
    if validation.calibration_records:
        validation_body += "<h3>Calibration errors</h3>" + _table(
            ["Record", "Model", "Observed", "Predicted", "Error"],
            [
                [
                    str(r.record_id),
                    str(r.model_id),
                    _fmt(r.observed_value),
                    _fmt(r.predicted_value),
                    _fmt(r.error),
                ]
                for r in validation.calibration_records
            ],
        )
    if validation.drift_records:
        validation_body += "<h3>Drift</h3>" + _table(
            ["Record", "Metric", "Baseline", "Current", "Status"],
            [
                [
                    str(d.id),
                    _esc(d.metric),
                    _fmt(d.baseline),
                    _fmt(d.current),
                    _esc(d.status),
                ]
                for d in validation.drift_records
            ],
        )

    sections = "".join(
        [
            _section(
                "Executive Overview", "executive-overview",
                overview_body,
            ),
            _section(
                "Complete Ledger", "complete-ledger", ledger_body
            ),
            _section(
                "State Vector Explorer", "state-vector-explorer",
                explorer_body,
            ),
            _section(
                "Opportunity Center", "opportunity-center", opp_body
            ),
            _section(
                "Knowledge Vault", "knowledge-vault", vault_body
            ),
            _section(
                "Validation Center", "validation-center",
                validation_body,
            ),
        ]
    )

    return (
        "<!DOCTYPE html><html lang=\"en\"><head>"
        "<meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, "
        "initial-scale=1\">"
        f"<title>Freedom GP$ — Sovereign Dashboard — "
        f"{_esc(entity.name)}</title>"
        "<style>"
        "body{font-family:system-ui,sans-serif;max-width:960px;"
        "margin:0 auto;padding:24px;color:#1a1a1a}"
        "h1{font-size:28px}h2{font-size:20px;border-bottom:1px "
        "solid #ccc;padding-bottom:4px}h3{font-size:16px}"
        "table{border-collapse:collapse;width:100%;margin:8px 0}"
        "th,td{border:1px solid #ddd;padding:6px 8px;text-align:left;"
        "font-size:14px}th{background:#f2f2f2}"
        "section{margin-bottom:32px}"
        "</style></head><body>"
        f"<h1>Freedom GP$ — Sovereign Dashboard</h1>"
        f"<p>{_esc(entity.name)} · {_esc(entity.type)}</p>"
        f"{sections}"
        "<footer><p>Freedom GP$ Engine v1 MVP — the first window "
        "into the engine. No new scoring: every number here is "
        "read from the engine's services.</p></footer>"
        "</body></html>"
    )
