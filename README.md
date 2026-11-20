# Freedom GP$ API — v1

**Freedom GP$** is a personal financial operating system: it organizes and
navigates money, shows what happens next, and recommends the best next
move — so a person always knows what they own, what they owe, when they
will be debt free, and what to pay next.

This repository is the **v1 production backend**: a FastAPI application
with a single scoring engine and API ecosystem that powers the web app,
mobile app, advisor dashboard, sovereign analysis mode, and nonprofit
mode — and eventually the full OmniSight intelligence platform.

> **Pricing is NOT final.** No billing, subscription, or payment code is
> wired into this repository. Prices are still being decided; do not treat
> any figure in docs or examples as a price.

## Stack

- **FastAPI** (OpenAPI 3.1) + **Pydantic v2**
- **SQLAlchemy 2.0 async** + **Alembic** migrations
- **PostgreSQL** (asyncpg)
- **Redis** + **Celery** (cache / background jobs)
- **JWT** authentication (python-jose, bcrypt via passlib)
- **Docker** + **docker-compose** (api + postgres + redis, behind nginx)
- **pytest** + **GitHub Actions** CI

## Quick start (Docker)

```bash
cp .env.example .env        # fill in real values; never commit a real .env
docker compose up --build
```

- API: http://localhost:8000
- Docs (OpenAPI 3.1): http://localhost:8000/docs
- Through nginx: http://localhost/docs

Apply migrations (inside the api container or locally):

```bash
docker compose exec api alembic upgrade head
```

## Run locally (without Docker)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# start PostgreSQL + Redis first (or point DATABASE_URL / REDIS_URL at yours)
alembic upgrade head
uvicorn app.main:app --reload
```

## Run tests

```bash
python -m pytest -q
```

The suite covers every scoring engine with hand-computed expected values
plus the public API endpoints (root, health, scores, OmniSight signals,
Shadow Ledger valuation, scenario stress test). The score endpoints need
no database and no auth, so the suite runs without a live PostgreSQL.

## Engine v1 — Sprint 1

Branch: **`freedom-gps-engine-v1`** (Engineering Order 001).

Sprint 1 adds the Identity foundation of Engine v1 to this repository:

- **Entity model** (`app/models/entity.py`) — Person, Family, Business,
  Nonprofit, Community. An entity is the node of the Knowledge Graph
  and the owner of a state vector; Identity is the root node of all
  value measurement.
- **EntityRelationship model** (same file) — owns, member_of, works_with,
  inherits, contributes_to. Edges carry a confidence score (0–1) and a
  source so every relationship traces to evidence.
- **State Vector Service** (`app/services/state_vector_service.py`) —
  record a snapshot for an entity, get its latest snapshot and history,
  and compute the GP$ Master Index for a snapshot. All math reuses the
  existing `app/engines/master_index.py`; nothing here re-implements a
  formula, and the Master Index coefficients are unchanged (uncalibrated
  placeholders, `calibrated: false`).
- **Entities API** (`app/api/entities.py`) — create/list/get/update
  entities, create/list relationships, list neighbors.
- **Entity state-vector endpoints** (in `app/api/state_vector.py`) —
  record a snapshot for an entity, list its history, fetch its latest,
  and compute its Master Index from the latest snapshot.
- **Migration** `alembic/versions/0003_entities.py` — creates the
  `entities` and `entity_relationships` tables and adds an optional
  `entity_id` to `state_vectors`.

Ownership rule: an entity's owner is its creator; owned entities are
visible only to their owner, while unowned entities are shared. Writes
require ownership. A self-relationship is rejected (400); an unknown
relationship type is rejected (422).

Run the Sprint 1 tests with the rest of the suite: `python -m pytest -q`
(77 tests: the original 52 plus 25 new entity / state-vector-service
tests in `tests/test_entities.py`).

## Engine v1 — Sprint 2

Branch: **`freedom-gps-engine-v1`** (Engineering Order 002 — the formal
Sprint 2 definition).

Sprint 2 adds the Knowledge Vault — the memory of Engine v1. It stores,
organizes, links, and retrieves knowledge, memory, legacy, and evidence
records associated with any entity. Every record belongs to an entity,
supports evidence, and supports lineage. No new scores, no new score
formulas, no Governance, no AI agents, no Opportunity Engine (deferred
to Sprint 4), and no Neo4j integration in this sprint.

### Models (`app/models/vault.py`, migration `alembic/versions/0004_vault.py`)

- **KnowledgeAsset** — the unified vault record: entity_id, title,
  description, category (Skill, Certification, Document, Research,
  Process, Lesson, Historical Record, Family Record, Legacy Asset),
  content_type, status (default `active`), confidence (0–1).
  Lineage: `created_by` (the creating user), `source`, `version`
  (starts at 1, increments on update), `last_updated` (the asset's
  `updated_at`, exposed under the lineage name), and a computed
  `evidence_count`.
- **EvidenceRecord** — attached to a KnowledgeAsset
  (`knowledge_asset_id`, not to an entity): source, source_type
  (Document, Transcript, Certificate, User Assertion, Government
  Record, Archive Record, Manual Entry), confidence (0–1),
  collected_at, notes. Multiple evidence records per asset.
- **Skill** — entity_id, name, proficiency, years_experience,
  verified (default false). A skill may have many evidence records:
  it carries an optional `knowledge_asset_id` FK to its
  KnowledgeAsset representation, so evidence attaches through the
  asset. There is no `monetized` field and no opportunity link in
  Sprint 2.
- **Certification** — entity_id, issuer, title, issue_date,
  expiration_date, credential_id. No `utilized` field in Sprint 2.
- **FamilyRecord** — entity_id, title, record_type (Biography,
  Genealogy, Historical Event, Oral History, Artifact, Letter),
  description, date_recorded. No `related_entity_id`, `relationship`,
  or `preservation_plan` field in Sprint 2.
- **LegacyAsset** — entity_id, title, asset_type (Book, Patent,
  Research, Archive, Training, Community Project, IP),
  estimated_impact (abstract units — never dollars),
  preservation_status. Preservation tracking is required.
- **AuditLog** (Event / Epic 2.8) — event_type, entity_id (nullable),
  record_type, record_id, actor (the user), timestamp, details.
  Creation writes `knowledge_created` (KnowledgeAsset), `skill_added`
  (Skill), `certification_added` (Certification), and `legacy_created`
  (LegacyAsset). FamilyRecord creation is also written to the audit
  log, but with no invented named event type — the entry carries
  `event_type` NULL and the record_type / record_id carry the record.
  Updating a LegacyAsset's `preservation_status` writes a
  `legacy_preservation_updated` entry.

Deleting a KnowledgeAsset deletes its EvidenceRecords (CASCADE);
Skills linked to it keep their row and lose the link (SET NULL).

### API (`app/api/vault.py`, schemas in `app/schemas/vault.py`)

All endpoints live under `/api/v1` and require auth; writes require
ownership of the entity, following the Sprint 1 pattern.

- Unified Knowledge APIs (Epic 2.7): `POST /knowledge`,
  `GET /knowledge/{id}`, `PUT /knowledge/{id}`,
  `DELETE /knowledge/{id}`, `GET /entities/{id}/knowledge`
  (paginated), `GET /knowledge/{id}/lineage`.
- Per-registry CRUD: Skills (`/skills`, `GET /entities/{id}/skills`),
  Certifications (`/certifications`), FamilyRecords
  (`/family-records`), LegacyAssets (`/legacy-assets`) — create, list
  with `?entity_id=`, get, PATCH, delete.
- Evidence: `POST /knowledge/{id}/evidence` (attach),
  `GET /knowledge/{id}/evidence` (list).
- Inventory: `GET /entities/{id}/knowledge-inventory` — counts and
  lists of skills, certifications, and knowledge assets.
- Audit log: `GET /vault/audit-log` (filter by `event_type`,
  `entity_id`, `record_type`, `record_id`).

### The four Sprint 2 missing-edge queries

`GET /api/v1/vault/queries/` — each scoped to the caller's
accessible entities, with an optional `?entity_id=` filter:

- `knowledge-no-evidence` — KnowledgeAssets with `evidence_count = 0`.
- `skills-no-evidence` — Skills with no linked KnowledgeAsset
  evidence (no asset, or an asset with no evidence).
- `certifications-no-evidence` — Certifications with no evidence.
  Sprint 3 gave Certification the same `knowledge_asset_id` link
  Skill has, so this is now evidenced through the real link; the
  (entity, category `"Certification"`, title) match is kept as a
  fallback for certifications created before the link existed.
  The graph query (`/graph/queries/...`) uses the link only.
- `legacy-no-preservation-status` — LegacyAssets with no
  `preservation_status` set (NULL or empty).

The five queries from the earlier Sprint 2 brief
(skills-not-linked-to-revenue, knowledge-not-linked-to-opportunities,
family-without-preservation-plans, certifications-not-utilized,
documents-without-graph-relationships) are **not** implemented as
stated: they need opportunity links, revenue links, utilization, and
graph relationships that are not in Sprint 2's scope. They become
fully answerable in Sprint 3 (graph) and Sprint 4 (opportunity); the
four endpoints above are their Sprint 2 equivalents.

### Tests

`tests/test_vault.py` — 37 tests: the eight formal acceptance tests
(AT-001 Knowledge Asset Lifecycle, AT-002 Evidence Attachment,
AT-003 Skill Verification, AT-004 Legacy Asset Preservation,
AT-005 Family Record Retrieval, AT-006 Entity Knowledge Inventory,
AT-007 State Vector Event Generation, AT-008 OpenAPI Contract) plus
per-model CRUD and validation, lineage, audit-log, the four queries,
and ownership / 404 / 422 / 401 coverage.

Run the full suite with `python -m pytest -q` (114 tests at the
time Sprint 2 landed — the current count is in the Sprint 3
section below). The endpoint tests use an
in-memory SQLite database via aiosqlite (test-only — production runs
PostgreSQL) with foreign keys enabled so the CASCADE / SET NULL
delete rules are exercised.

## Engine v1 — Sprint 3

Branch: **`freedom-gps-engine-v1`** (Engineering Order 003).

Sprint 3 adds the Knowledge Graph Layer — the relationships of
Engine v1. Every major object becomes a graph node, the links
between them become graph edges, and a PostgreSQL-backed
abstraction layer (`IGraphRepository`) stands between the business
services and the storage so Neo4j can replace PostgreSQL later
without rewriting the services. No Opportunity Engine, Trust
Engine, Prosperity Engine, Forecasting, Recommendations, AI
reasoning, or graph visualization in this sprint; no new scores.

### Models (`app/models/graph.py`, migration `alembic/versions/0005_graph.py`)

- **GraphNode** — one node per source record: `node_type`
  (Entity, KnowledgeAsset, Skill, Certification, EvidenceRecord,
  FamilyRecord, LegacyAsset), `source_id` (the source record's id),
  `node_metadata` (JSON copy of the record's identifying fields —
  name, title, category, …; the source record is the truth and
  sync refreshes it). `(node_type, source_id)` is unique.
- **GraphEdge** — one directed edge between two nodes:
  `from_node_id`, `to_node_id`, `relationship_type` (OWNS,
  HAS_SKILL, HAS_CERTIFICATION, HAS_EVIDENCE, HAS_RECORD,
  HAS_LEGACY, RELATED_TO, MEMBER_OF, CONTRIBUTES_TO), `confidence`
  (0–1, default 1.0), `source`, `created_at`. Lineage lives on the
  edge itself: confidence, source, and created_at (AT-307).
  `(from_node_id, to_node_id, relationship_type)` is unique.
  Deleting a node deletes its edges (CASCADE).

### The abstraction layer (`app/graph/`)

- **IGraphRepository** (`app/graph/interface.py`) — the storage
  contract: `create_node`, `get_node`, `get_node_by_source`,
  `list_nodes`, `update_node_metadata`, `delete_node`,
  `create_edge`, `get_edge`, `get_edge_by_triple`, `list_edges`,
  `delete_edge`, `neighbors`. Storage primitives only; traversal,
  path finding, sync, and queries live in the service so they work
  against any backend. Repository methods flush; they do not
  commit — the caller owns the transaction.
- **PostgresGraphRepository** (`app/graph/postgres.py`) — the real
  Engine v1 backend, backed by the `graph_nodes` / `graph_edges`
  tables.
- **Neo4jGraphRepository** (`app/graph/neo4j.py`) — a stub with the
  same interface. Constructing it raises `NotImplementedError`
  ("Neo4j is not configured for Engine v1"). It imports no neo4j
  driver and requires no Neo4j server.
- **GraphService** (`app/graph/service.py`) — the business service.
  It depends ONLY on `IGraphRepository` (AT-308: the service module
  imports no backend, and the acceptance test runs the service's
  node / edge / traversal / path logic against an in-memory stub
  repository). It owns idempotent node / edge get-or-create, the
  automatic sync, the backfill, traversal, path finding, the
  inventory queries, and audited manual relationships.

### Automatic graph maintenance

Node creation is automatic: when a record is created through the
API, its graph node and edges are created with it (the hooks live
in `app/api/vault.py` and `app/api/entities.py`).

- **Entity** → its node. **KnowledgeAsset** → its node + OWNS from
  its entity. **Skill** → its node + OWNS and HAS_SKILL from its
  entity. **Certification** → its node + OWNS and HAS_CERTIFICATION.
  **FamilyRecord** → its node + OWNS and HAS_RECORD. **LegacyAsset**
  → its node + OWNS and HAS_LEGACY.
- **EvidenceRecord** → its node + HAS_EVIDENCE from its asset, and
  from every Skill / Certification linked to that asset.
- **EntityRelationship** (Sprint 1) → one graph edge between the
  two entity nodes: owns → OWNS, member_of → MEMBER_OF,
  contributes_to → CONTRIBUTES_TO, works_with → RELATED_TO,
  inherits → RELATED_TO.
- Deleting a record deletes its graph node (edges go with it);
  deleting a KnowledgeAsset also deletes its evidence nodes.
  PATCHing a Skill / Certification's `knowledge_asset_id` resyncs
  its evidence edges; only auto-created edges are ever removed by
  a sync — a relationship a user made by hand is theirs to keep
  (or delete through the API).
- **POST /api/v1/graph/sync** backfills nodes and edges for every
  record on every entity the caller may read. Idempotent: a second
  run creates nothing. It returns `nodes_created`, `edges_created`,
  and the totals.

### The four graph inventory queries

`GET /api/v1/graph/queries/` — each scoped to the caller's
accessible entities, with an optional `?entity_id=` filter. The
graph answers them: a record is unevidenced when its node has no
outgoing HAS_EVIDENCE edge.

- `skills-no-evidence` — Skills with no evidence.
- `certifications-no-evidence` — Certifications with no evidence,
  through the real asset link.
- `knowledge-no-evidence` — KnowledgeAssets with no evidence.
- `legacy-no-preservation-status` — LegacyAssets with no
  `preservation_status` set. The node set comes from the graph;
  the status itself is a source-record attribute, so the filter
  reads the source rows.

The old `/api/v1/vault/queries/` endpoints remain as-is (backward
compatibility) — the graph queries are the canonical
implementation going forward, and both return the same result
sets for the four queries.

### The Certification link fix

Sprint 2's `certifications-no-evidence` matched by
(entity, category `"Certification"`, title) because Certification
had no asset link. Sprint 3 gives Certification an optional
`knowledge_asset_id` FK to its KnowledgeAsset representation
(migration `0005_graph.py`), the same pattern Skill has had since
Sprint 2. The graph query uses the real link; the Vault query uses
link OR title match so certifications created before the link
existed keep their old results.

### API (`app/api/graph.py`, schemas in `app/schemas/graph.py`)

All endpoints live under `/api/v1/graph` and require auth. A node
is readable when its source record's entity is readable; writing
a relationship requires owning the from-node's entity (the Sprint 1
ownership rule, unchanged). There is no POST `/graph/nodes` in
Sprint 3 — nodes are created automatically.

- Node API: `GET /graph/nodes` (filter with `?node_type=`),
  `GET /graph/nodes/{id}`, `GET /graph/entities/{id}/graph` (the
  entity's subgraph: its node, its 1-hop neighbors, and the edges
  between them), `GET /graph/nodes/{id}/traverse?depth=2`
  (breadth-first walk), `GET /graph/path?from_node_id=&to_node_id=`
  (shortest path, AT-305).
- Relationship API: `POST /graph/relationships` (validates both
  nodes exist, rejects a self-edge with 400 and a duplicate with
  409, writes a `graph_relationship_created` audit entry),
  `GET /graph/relationships` (filter with `?from_node_id=`,
  `?to_node_id=`, `?relationship_type=`),
  `DELETE /graph/relationships/{id}` (writes a
  `graph_relationship_deleted` audit entry).
- `POST /graph/sync` — the backfill (see above).
- The four inventory queries (see above).

### Tests

`tests/test_graph.py` — 26 tests: the eight formal acceptance
tests (AT-301 Node Creation, AT-302 Relationship Creation, AT-303
Evidence Connection, AT-304 Knowledge Inventory Query, AT-305
Graph Traversal, AT-306 Audit Verification, AT-307 Lineage
Verification, AT-308 Repository Abstraction) plus automatic nodes
and edges for every record type, entity-relationship graph edges,
manual relationship validation (400 / 404 / 409 / 422), subgraph,
the four graph queries, the Certification link fix, node deletion
with its record, ownership (403), the OpenAPI contract, and the
sync backfill with idempotency.

Run the full suite with `python -m pytest -q` (140 tests: the 114
from Sprints 1–2 plus 26 new graph tests).

## Engine v1 — Sprint 4

The Opportunity Engine — the first discovery capability. Mission:
What is missing? What should be connected? What creates value
next? The system now has Identity, State Vector, Memory, and
Relationships; the next logical capability is Discovery, and this
is where Freedom GP$ first starts producing value that users can
actually see.

### Model (`app/models/opportunity.py`, migration `alembic/versions/0006_opportunities.py`)

One row per discovered gap. `Opportunity` carries `entity_id`,
`title`, `description`, `opportunity_type` (Skill Opportunity,
Asset Opportunity, Knowledge Opportunity, Relationship
Opportunity, Legacy Opportunity), `confidence` (the detector's
certainty in the finding, 0–1 — not a value estimate), `status`
(detected, accepted, dismissed, realized), and its lineage:
`detector` (which detector found it, or "manual"), `source_type`
and `source_id` (the record the opportunity refers to — a Skill,
KnowledgeAsset, LegacyAsset, Certification, or "GraphCluster" for
a disconnected cluster, which has no single source record and
carries `source_id` None), and an optional `evidence_id` link.
Creation and status changes write AuditLog entries.

### The six detectors (`app/services/opportunity_service.py`)

All six read the graph — they use GraphService and the
IGraphRepository primitives, never raw table filters where a graph
query expresses the finding. D1 and D5 reuse the Sprint 3 graph
inventory queries.

| Detector | Finding | Type | Confidence |
|---|---|---|---|
| D1 `skills_no_evidence` | Skill with no outgoing HAS_EVIDENCE edge | Skill Opportunity | 0.9 |
| D2 `skills_evidence_no_utilization` | Skill with evidence but no edges beyond ownership and evidence | Skill Opportunity | 0.7 |
| D3 `certifications_evidence_no_utilization` | Certification with evidence but no edges beyond ownership and evidence | Knowledge Opportunity | 0.7 |
| D4 `knowledge_assets_no_relationships` | KnowledgeAsset whose only graph edge is the OWNS edge from its entity (zero edges counts too) | Knowledge Opportunity — Asset Opportunity when the asset's category is "Legacy Asset" | 0.85 |
| D5 `legacy_no_preservation_status` | LegacyAsset with no preservation_status set | Legacy Opportunity | 0.9 |
| D6 `disconnected_clusters` | A connected component with no entity node — nodes connected to each other but not to the ledger (BFS over the graph layer) | Relationship Opportunity | 0.8 |

Detector confidences are illustrative — the detector's certainty
in the finding, not a value estimate.

> **The honest limit on D2 and D3:** the system has no
> utilization data — Sprint 2 deliberately has no `utilized`
> field on Skill or Certification, and there is no revenue
> linkage — so "no utilization" is operationalized as "verified
> capability that is not connected to any opportunity, asset, or
> other value-producing relationship". True utilization tracking
> (revenue linkage) is a later concern, not Sprint 4.

> **A D4 caveat:** an asset that serves as a Skill /
> Certification's KnowledgeAsset representation has no graph edge
> to its holder (Sprint 3 does not record that link as an edge),
> so it can still be flagged here. The representation link is a
> source-table link, not a graph relationship.

### Detection run + idempotency

`POST /api/v1/opportunities/detect` runs all six detectors for
one entity. It is idempotent: a finding does not duplicate an
open (detected or accepted) opportunity for the same detector +
source record — D6 is keyed by its canonical cluster description
("Disconnected cluster of N nodes (...). Members: ..."). A gap
that closes dismisses its open opportunity automatically, with a
note appended to the description and an audit entry
(`opportunity_auto_dismissed`) — except a cluster opportunity
whose description the user rewrote, which is left alone (the
description is the cluster fingerprint and can no longer be
matched). An accepted opportunity whose gap closes is dismissed
the same way; it is never auto-marked realized, because the
system cannot know why the gap closed.

### API (`app/api/opportunities.py`, schemas in `app/schemas/opportunity.py`)

- `POST /api/v1/opportunities` — create by hand (201; detector
  defaults to "manual"; audited).
- `GET /api/v1/opportunities` — list; filter with `?entity_id=`,
  `?status=`, `?opportunity_type=`.
- `GET /api/v1/opportunities/{id}` — one opportunity, with its
  lineage (detector, source record, timestamps).
- `PATCH /api/v1/opportunities/{id}` — update; a status change
  writes an audit entry.
- `DELETE /api/v1/opportunities/{id}` — delete (204).
- `GET /api/v1/entities/{entity_id}/opportunities` — one entity's
  opportunities.
- `POST /api/v1/opportunities/detect` — run all six detectors for
  one entity; returns the opportunities created and the ones
  auto-dismissed.

### Tests

`tests/test_opportunities.py` — 21 tests: the eight formal
acceptance tests (AT-401 Hidden Skill Detection, AT-402 Knowledge
Gap Detection, AT-403 Legacy Preservation Detection, AT-404
Opportunity CRUD, AT-405 Opportunity Lineage, AT-406 Opportunity
Confidence, AT-407 Opportunity Audit, AT-408 Graph Gap Query)
plus D2/D3 (evidence but no utilization), the D4 Legacy Asset
category mapping, idempotency, auto-dismiss on gap close (with
the D1 → D2 detector transition), open gaps surviving a rerun,
accepted opportunities not duplicating, validation and ownership
(422 / 404 / 403), list filters, and the OpenAPI contract.

Run the full suite with `python -m pytest -q` (161 tests: the 140
from Sprints 1–3 plus 21 new opportunity tests).

## Endpoints

### Meta

| Method | Path | Description |
|---|---|---|
| GET | `/` | System + version (`{"system": "Freedom GP$", "version": "1.0"}`) |
| GET | `/health` | Liveness probe (`{"status": "ok"}`) |

### Scores (public — pure computation, no auth)

| Method | Path | Description |
|---|---|---|
| POST | `/scores/stinnes` | Stinnes Score — is debt creating wealth? |
| POST | `/scores/asset-flipper` | Asset Flipper Score — how hard are assets working? |
| POST | `/scores/antifragility` | Anti-Fragility Index — how do you perform under stress? |
| POST | `/scores/faustian` | Faustian Ratio — future pain vs current gain |
| POST | `/scores/broken-safe-haven` | Broken Safe Haven Index — trust vs actual protection |
| POST | `/scores/regulatory-friction` | Regulatory Friction Score — how trapped is value? |
| POST | `/scores/credit-health` | Credit Health Score — can you borrow? |
| POST | `/scores/sovereignty` | Sovereignty Score — can you live independent of a paycheck? |
| POST | `/scores/revaluation-gap` | Revaluation Gap — how much hidden upside exists? |
| POST | `/scores/wealth-index` | Freedom GP$ Wealth Index (weighted master formula) |

### Users (v1)

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/users` | Create a user account (name, email, password) |
| POST | `/api/v1/users/login` | Log in, receive a JWT access token |
| GET | `/api/v1/users/{user_id}` | Fetch a user (own record only, auth required) |

### Ledger (v1, auth required)

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/assets` | Add an asset |
| GET | `/api/v1/assets/{user_id}` | List a user's assets |
| POST | `/api/v1/liabilities` | Add a liability |
| GET | `/api/v1/liabilities/{user_id}` | List a user's liabilities |
| POST | `/api/v1/shadow-ledger/assets` | Add a hidden asset |
| POST | `/api/v1/shadow-ledger/value` | Value hidden assets + Hidden Asset Multiplier |
| POST | `/api/v1/reports` | Store a report |
| GET | `/api/v1/reports` | List the current user's reports |

### OmniSight + Scenarios

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/omnisight/economic-signals` | Economic signals + Economic Stress Score (illustrative values; labelled `source: "illustrative"`) |
| POST | `/api/v1/scenarios/stress-test` | Stress-test a wealth index against a named scenario (`2008`, `covid`, `inflation`) |

### State Vector (auth required, except the master-index computation)

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/state-vector` | Record one snapshot of the caller's own state vector Ω(t) |
| GET | `/api/v1/state-vector/{user_id}` | List the caller's snapshots, latest first |
| GET | `/api/v1/state-vector/{user_id}/latest` | The caller's latest snapshot (404 when none recorded) |
| POST | `/api/v1/state-vector/master-index` | Pure computation (no DB, no auth): GP$ Master Index + Unified Prosperity Equation for one supplied state vector |
| POST | `/api/v1/state-vector/entities/{entity_id}` | Record one snapshot of an entity's state vector Ω(t) |
| GET | `/api/v1/state-vector/entities/{entity_id}` | List an entity's snapshots, latest first |
| GET | `/api/v1/state-vector/entities/{entity_id}/latest` | An entity's latest snapshot (404 when none recorded) |
| GET | `/api/v1/state-vector/entities/{entity_id}/master-index` | GP$ Master Index + Unified Prosperity Equation from an entity's latest snapshot |

### Entities (Engine v1 Sprint 1, auth required)

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/entities` | Create an entity (Person, Family, Business, Nonprofit, Community); caller becomes owner |
| GET | `/api/v1/entities` | List the caller's entities plus shared (unowned) ones; filter with `?type=` |
| GET | `/api/v1/entities/{entity_id}` | One entity, with every relationship it participates in |
| PATCH | `/api/v1/entities/{entity_id}` | Update name / description (owner only; type never changes) |
| POST | `/api/v1/entities/{entity_id}/relationships` | Create a directed relationship from this entity (owns, member_of, works_with, inherits, contributes_to) |
| GET | `/api/v1/entities/{entity_id}/relationships` | Every relationship the entity participates in, with direction |
| GET | `/api/v1/entities/{entity_id}/neighbors` | Neighboring entities with the relationship that connects them |

### Knowledge Vault (Engine v1 Sprint 2, auth required)

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/knowledge` | Create a KnowledgeAsset (version 1; caller becomes `created_by`) |
| GET | `/api/v1/knowledge/{asset_id}` | One KnowledgeAsset |
| PUT | `/api/v1/knowledge/{asset_id}` | Replace a KnowledgeAsset (version increments) |
| DELETE | `/api/v1/knowledge/{asset_id}` | Delete a KnowledgeAsset (its evidence goes with it) |
| GET | `/api/v1/knowledge/{asset_id}/lineage` | Lineage: `created_by`, `source`, `version`, `last_updated`, `evidence_count` |
| POST | `/api/v1/knowledge/{asset_id}/evidence` | Attach an EvidenceRecord to the asset |
| GET | `/api/v1/knowledge/{asset_id}/evidence` | List the asset's evidence |
| GET | `/api/v1/entities/{entity_id}/knowledge` | The entity's KnowledgeAssets, paginated |
| GET | `/api/v1/entities/{entity_id}/knowledge-inventory` | Counts and lists of skills, certifications, knowledge assets |
| POST | `/api/v1/skills` | Create a Skill (optional `knowledge_asset_id` links its evidence) |
| GET | `/api/v1/skills` | List skills; filter with `?entity_id=` |
| GET | `/api/v1/skills/{skill_id}` | One Skill |
| PATCH | `/api/v1/skills/{skill_id}` | Update a Skill (e.g. `verified: true`) |
| DELETE | `/api/v1/skills/{skill_id}` | Delete a Skill |
| GET | `/api/v1/entities/{entity_id}/skills` | The entity's Skills, paginated |
| POST | `/api/v1/certifications` | Create a Certification |
| GET | `/api/v1/certifications` | List certifications; filter with `?entity_id=` |
| GET | `/api/v1/certifications/{certification_id}` | One Certification |
| PATCH | `/api/v1/certifications/{certification_id}` | Update a Certification |
| DELETE | `/api/v1/certifications/{certification_id}` | Delete a Certification |
| GET | `/api/v1/entities/{entity_id}/certifications` | The entity's Certifications, paginated |
| POST | `/api/v1/family-records` | Create a FamilyRecord |
| GET | `/api/v1/family-records` | List family records; filter with `?entity_id=` |
| GET | `/api/v1/family-records/{record_id}` | One FamilyRecord |
| PATCH | `/api/v1/family-records/{record_id}` | Update a FamilyRecord |
| DELETE | `/api/v1/family-records/{record_id}` | Delete a FamilyRecord |
| GET | `/api/v1/entities/{entity_id}/family-records` | The entity's FamilyRecords, paginated |
| POST | `/api/v1/legacy-assets` | Create a LegacyAsset |
| GET | `/api/v1/legacy-assets` | List legacy assets; filter with `?entity_id=` |
| GET | `/api/v1/legacy-assets/{asset_id}` | One LegacyAsset |
| PATCH | `/api/v1/legacy-assets/{asset_id}` | Update a LegacyAsset (setting `preservation_status` writes an audit entry) |
| DELETE | `/api/v1/legacy-assets/{asset_id}` | Delete a LegacyAsset |
| GET | `/api/v1/entities/{entity_id}/legacy-assets` | The entity's LegacyAssets, paginated |
| GET | `/api/v1/vault/queries/knowledge-no-evidence` | KnowledgeAssets with no evidence |
| GET | `/api/v1/vault/queries/skills-no-evidence` | Skills with no linked evidence |
| GET | `/api/v1/vault/queries/certifications-no-evidence` | Certifications with no evidence |
| GET | `/api/v1/vault/queries/legacy-no-preservation-status` | LegacyAssets with no preservation status |
| GET | `/api/v1/vault/audit-log` | Audit entries; filter with `?event_type=`, `?entity_id=`, `?record_type=`, `?record_id=` |

### Knowledge Graph (Engine v1 Sprint 3, auth required)

| Method | Path | Description |
|---|---|---|
| GET | `/api/v1/graph/nodes` | Nodes the caller may read; filter with `?node_type=` |
| GET | `/api/v1/graph/nodes/{node_id}` | One graph node |
| GET | `/api/v1/graph/entities/{entity_id}/graph` | The entity's subgraph: its node, 1-hop neighbors, edges |
| GET | `/api/v1/graph/nodes/{node_id}/traverse` | Breadth-first walk; `?depth=` (default 2, max 5) |
| GET | `/api/v1/graph/path` | Shortest path; `?from_node_id=`, `?to_node_id=`, `?max_depth=` |
| POST | `/api/v1/graph/relationships` | Create a relationship (self-edge 400, duplicate 409; audited) |
| GET | `/api/v1/graph/relationships` | List relationships; filter with `?from_node_id=`, `?to_node_id=`, `?relationship_type=` |
| DELETE | `/api/v1/graph/relationships/{edge_id}` | Delete a relationship (audited) |
| POST | `/api/v1/graph/sync` | Backfill nodes + edges for all accessible records (idempotent) |
| GET | `/api/v1/graph/queries/skills-no-evidence` | Skills with no evidence (graph) |
| GET | `/api/v1/graph/queries/certifications-no-evidence` | Certifications with no evidence (graph, real link) |
| GET | `/api/v1/graph/queries/knowledge-no-evidence` | KnowledgeAssets with no evidence (graph) |
| GET | `/api/v1/graph/queries/legacy-no-preservation-status` | LegacyAssets with no preservation status (graph) |

### Opportunity Engine (Engine v1 Sprint 4, auth required)

| Method | Path | Description |
|---|---|---|
| POST | `/api/v1/opportunities` | Create an opportunity by hand (detector "manual"; audited) |
| GET | `/api/v1/opportunities` | List; filter with `?entity_id=`, `?status=`, `?opportunity_type=` |
| POST | `/api/v1/opportunities/detect` | Run all six detectors for one entity (idempotent; auto-dismisses closed gaps) |
| GET | `/api/v1/opportunities/{opportunity_id}` | One opportunity, with lineage |
| PATCH | `/api/v1/opportunities/{opportunity_id}` | Update; a status change writes an audit entry |
| DELETE | `/api/v1/opportunities/{opportunity_id}` | Delete an opportunity |
| GET | `/api/v1/entities/{entity_id}/opportunities` | One entity's opportunities |

## Scoring engines

All engines live in `app/engines/` as pure functions (numbers in, score
out) so they are unit-testable in isolation:

- **Stinnes Score** — `(productive + hidden assets) / (debt principal +
  annual debt cost + risk factor)`; 999.0 when the denominator is 0.
  Bands: Debt Trap <1, Stable <2, Productive <4, Asset Arbitrage <8,
  Financial Sovereignty 8+.
- **Asset Flipper Score** — `(productive + hidden) / total assets * 100`.
  Bands: Hoarder 0-20, Underutilized 20-40, Stable 40-60, Productive
  60-80, Wealth Engine 80-100.
- **Anti-Fragility Index** — `stressed net worth / current net worth`.
  Bands: Fragile <0.50, Vulnerable 0.50-0.75, Resilient 0.75-1.00,
  Anti-Fragile 1.00+.
- **Faustian Ratio** — `future cost / present benefit`. Bands: Good <1,
  Caution 1-2, Dangerous 2-5, Faustian 5+.
- **Broken Safe Haven Index** — `expected protection - actual protection`.
- **Regulatory Friction Score** — average of tax, compliance, liquidity,
  ownership, and capital-control friction (0-100).
- **Sovereignty Score** — `passive income / required living expenses`.
  Bands: Dependent 0-0.5, Transitional 0.5-1, Sovereign 1-2, Fully
  Sovereign 2+.
- **Credit Health Score** — `(payment history * 0.35 + utilization * 0.30
  + age * 0.15 + mix * 0.10 + inquiry * 0.10) * 100`, scale 0-850.
- **Hidden Asset Multiplier** — `(IP + skills + licenses + network +
  brand + education + future royalties) / reported net worth`.
- **Revaluation Gap** — `potential value / current recognized value`.
- **Wealth Index** (FastAPI version) —
  `wealth = stinnes*20 + asset_flipper*2 + anti_fragility*100 +
  sovereignty*50`; `penalties = regulatory_friction +
  behavioral_friction + faustian_ratio*10`; `wealth_index = wealth -
  penalties`.
- **Economic Stress Score** (OmniSight) — `inflation*0.25 + rates*0.20 +
  unemployment*0.20 + debt_to_gdp*0.20 + money_supply_growth*0.15`.

### A note on the spec's example values

Two illustrative example outputs in the source spec do not reconcile
with their own formulas, so the tests assert the **formulas**, not the
examples:

- **Wealth Index 784**: the spec lists 784 for inputs (stinnes 4.88,
  asset flipper 82, anti-fragility 0.90, sovereignty 1.4, behavioral
  friction 22). The weighted formula yields ~335 for those inputs.
  `tests/test_wealth.py` asserts the formula value (346.86 for the
  test inputs) and documents the discrepancy.
- **Stinnes 5.61**: the spec lists 5.61 for inputs (productive 750,000;
  hidden 250,000; debt principal 150,000; annual carry cost 9,000; risk
  factor 20,000). The formula yields 5.59. The test asserts 5.59 and
  documents the discrepancy. The rating ("Asset Arbitrage") matches
  either way.

## Database entities (v1)

User, Profile, Asset, Liability, CashFlow, HiddenAsset, ScoreSnapshot,
Scenario, Report, OmniSightSignal, Recommendation, StateVector, Entity,
EntityRelationship, KnowledgeAsset, EvidenceRecord, Skill,
Certification, FamilyRecord, LegacyAsset, AuditLog, GraphNode,
GraphEdge, Opportunity. See
`alembic/versions/0001_initial.py` for the v1 schema,
`alembic/versions/0002_state_vector.py` for the state vector table,
`alembic/versions/0003_entities.py` for the Sprint 1 entity tables
(plus `entity_id` on `state_vectors`),
`alembic/versions/0004_vault.py` for the Sprint 2 Knowledge Vault
tables and the audit log, and
`alembic/versions/0005_graph.py` for the Sprint 3 graph tables (plus
`knowledge_asset_id` on `certifications`), and
`alembic/versions/0006_opportunities.py` for the Sprint 4
opportunities table.

## State Vector Ω(t)

Every person, family, business, nonprofit, city, or nation can be
described as a state vector:

```
Ω(t) = [F, H, S, R, L, T, M, I, E]
```

| Variable | Meaning | Formula |
|---|---|---|
| F | Financial Capital | Assets − Liabilities |
| H | Human Capital | Knowledge + Skill + Experience |
| S | Social Capital | Network Centrality + Trust Density |
| R | Resource Capital | Σ Productive Assets |
| L | Legacy Capital | Preserved Knowledge + Future Influence |
| T | Trust Capital | Successful Interactions / Total Interactions |
| M | Memory Capital | Stored Information × Accessibility |
| I | Intelligence Capital | Correct Decisions / Total Decisions |
| E | Entropy | Loss Rate + Risk Exposure + System Friction |

One row of the `state_vectors` table is one snapshot of one user's
nine variables at one time, plus the Opportunity Vector value
(Potential Value = Current Value + Opportunity). The time series of
snapshots is the ledger's trajectory — and what forecasting reads.
Every variable is indexed by time, X(t): past, present, future.

Two computations run on a state vector (`app/engines/master_index.py`):

- **Unified Prosperity Equation** — `P = (F+H+S+R+L) × (T+I+M) / E`.
  Capitals create value; trust, intelligence, and memory amplify and
  preserve it; entropy destroys it. Returns `null` when E is 0 — the
  equation is undefined there and no value is invented.
- **GP$ Master Index** —
  `GP$ = αF + βH + γS + δR + εL + ζT + ηM + θI − λE`.

> **The Master Index coefficients are UNCALIBRATED PLACEHOLDERS.**
> They default to equal weight 1.0 and must be **learned statistically**
> (regression analysis, optimization theory, machine learning, Bayesian
> inference, system dynamics) from real user data — not guessed,
> measured. Until calibration, every Master Index response reports
> `calibrated: false` and carries the warning. Never present a
> placeholder-coefficient index as calibrated.

## Project structure

```
freedom-gps-v1/
├── app/
│   ├── main.py            # FastAPI app ("Freedom GP$ API", v1.0)
│   ├── config.py          # settings from env
│   ├── database.py        # async SQLAlchemy engine + session
│   ├── auth.py            # JWT + bcrypt
│   ├── models/            # SQLAlchemy 2.0 models (v1 entities)
│   ├── schemas/           # Pydantic v2 request/response models
│   ├── engines/           # pure scoring functions
│   ├── services/          # database-backed services (Engine v1)
│   ├── graph/             # IGraphRepository, Postgres + Neo4j repos, GraphService
│   └── api/               # FastAPI routers
├── alembic/               # migrations (0001_initial creates all v1 tables)
├── tests/                 # pytest suite
├── .github/workflows/ci.yml
├── Dockerfile
├── docker-compose.yml     # api + postgres + redis + nginx
├── nginx.conf
├── requirements.txt
└── .env.example
```

## Security notes

- Never commit a real `.env`. Change `JWT_SECRET_KEY` in production.
- Passwords are bcrypt-hashed; tokens are HS256 JWTs.
- The OmniSight economic signals endpoint returns **illustrative demo
  values** until live feeds are wired in; the response always reports
  `"source": "illustrative"` so consumers never mistake them for live
  data.
- No real personal financial numbers and no pricing/billing code are in
  this repository.
