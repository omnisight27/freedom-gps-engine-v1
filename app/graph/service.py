"""GraphService — the business service behind the Knowledge Graph
(Sprint 3).

The service depends ONLY on IGraphRepository (AT-308): it never
imports PostgresGraphRepository or Neo4jGraphRepository. Give it
any backend that honors the interface and every method below
works unchanged — the acceptance test proves it with an in-memory
stub.

What the service owns:
- node / edge get-or-create (idempotent sync),
- automatic graph maintenance: every record's node and edges are
  created when the record is created (the API layer calls the
  sync_* methods from the create paths) and removed when the
  record is deleted,
- the sync_all backfill for records that predate the graph,
- traversal and path finding, composed from the repository's
  neighbor primitive so they work against any backend,
- the four inventory queries, answered by traversing HAS_EVIDENCE
  edges (or their absence) instead of joining source tables,
- manual relationship creation / deletion with validation and an
  audit entry (AT-306).

Access rule (the Sprint 1 ownership rule, unchanged): a graph
node is readable when its source record's entity is readable;
writing a relationship requires owning the from-node's entity.
The service resolves a node's entity through its source record.

Auto-created edges carry source "auto". Manual edges carry the
caller's source (or None). The Sprint 1 entity-to-entity
relationship types map into the graph as: owns -> OWNS,
member_of -> MEMBER_OF, contributes_to -> CONTRIBUTES_TO,
works_with -> RELATED_TO, inherits -> RELATED_TO.

Out of scope for Sprint 3 (do not add): Opportunity Engine,
Trust Engine, Prosperity Engine, Forecasting, Recommendations,
AI reasoning, graph visualization, new scores.
"""

from __future__ import annotations

import json
from collections import deque

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.graph.interface import IGraphRepository
from app.models.entity import Entity, EntityRelationship
from app.models.graph import (
    GraphEdge,
    GraphNode,
    GraphNodeType,
    GraphRelationshipType,
)
from app.models.user import User
from app.models.vault import (
    AuditLog,
    Certification,
    EvidenceRecord,
    FamilyRecord,
    KnowledgeAsset,
    LegacyAsset,
    Skill,
)

# New audit event types for manual graph operations (AT-306).
EVENT_GRAPH_RELATIONSHIP_CREATED = "graph_relationship_created"
EVENT_GRAPH_RELATIONSHIP_DELETED = "graph_relationship_deleted"

# Auto-created edges carry this source.
AUTO_SOURCE = "auto"

# Sprint 1 entity-relationship types -> Sprint 3 graph types.
ENTITY_RELATIONSHIP_TO_GRAPH: dict[str, str] = {
    "owns": GraphRelationshipType.OWNS.value,
    "member_of": GraphRelationshipType.MEMBER_OF.value,
    "contributes_to": GraphRelationshipType.CONTRIBUTES_TO.value,
    "works_with": GraphRelationshipType.RELATED_TO.value,
    "inherits": GraphRelationshipType.RELATED_TO.value,
}

# Node type -> source model (for entity resolution and sync).
_NODE_MODELS = {
    GraphNodeType.ENTITY.value: Entity,
    GraphNodeType.KNOWLEDGE_ASSET.value: KnowledgeAsset,
    GraphNodeType.SKILL.value: Skill,
    GraphNodeType.CERTIFICATION.value: Certification,
    GraphNodeType.EVIDENCE_RECORD.value: EvidenceRecord,
    GraphNodeType.FAMILY_RECORD.value: FamilyRecord,
    GraphNodeType.LEGACY_ASSET.value: LegacyAsset,
}


def _entity_metadata(entity: Entity) -> dict:
    return {"name": entity.name, "entity_type": entity.type}


def _asset_metadata(asset: KnowledgeAsset) -> dict:
    return {"title": asset.title, "category": asset.category}


def _skill_metadata(skill: Skill) -> dict:
    return {"name": skill.name, "proficiency": skill.proficiency}


def _certification_metadata(cert: Certification) -> dict:
    return {"title": cert.title, "issuer": cert.issuer}


def _evidence_metadata(evidence: EvidenceRecord) -> dict:
    return {
        "source_type": evidence.source_type,
        "confidence": evidence.confidence,
    }


def _family_metadata(record: FamilyRecord) -> dict:
    return {"title": record.title, "record_type": record.record_type}


def _legacy_metadata(asset: LegacyAsset) -> dict:
    return {"title": asset.title, "asset_type": asset.asset_type}


class GraphService:
    """Graph business logic over any IGraphRepository backend.

    `db` is the session used for source-record lookups (sync,
    access checks, audit). It is optional only so the pure-graph
    methods (nodes, edges, traversal, path) can be exercised
    against a stub repository with no database (AT-308); any
    method that touches source records raises when db is None.
    """

    def __init__(
        self, repo: IGraphRepository, db: AsyncSession | None = None
    ) -> None:
        self.repo = repo
        self.db = db

    # --- Session guard ---

    def _require_db(self) -> AsyncSession:
        if self.db is None:
            raise RuntimeError(
                "GraphService requires a database session for this "
                "operation"
            )
        return self.db

    # --- Idempotent node / edge primitives ---

    async def ensure_node(
        self,
        node_type: str,
        source_id: int,
        metadata: dict | None = None,
    ) -> GraphNode:
        """Get the node for a source record, creating it when it
        does not exist. When it exists and the metadata changed,
        refresh it — sync is self-healing."""
        node = await self.repo.get_node_by_source(node_type, source_id)
        if node is None:
            return await self.repo.create_node(
                node_type=node_type, source_id=source_id, metadata=metadata
            )
        if metadata is not None and node.node_metadata != metadata:
            node = await self.repo.update_node_metadata(node, metadata)
        return node

    async def ensure_edge(
        self,
        from_node: GraphNode,
        to_node: GraphNode,
        relationship_type: str,
        confidence: float = 1.0,
        source: str | None = None,
    ) -> GraphEdge:
        """Get the edge for a (from, to, type) triple, creating it
        when it does not exist."""
        edge = await self.repo.get_edge_by_triple(
            from_node.id, to_node.id, relationship_type
        )
        if edge is not None:
            return edge
        return await self.repo.create_edge(
            from_node_id=from_node.id,
            to_node_id=to_node.id,
            relationship_type=relationship_type,
            confidence=confidence,
            source=source,
        )

    async def delete_node_for_source(
        self, node_type: str, source_id: int
    ) -> bool:
        """Delete a source record's node (its edges go with it).
        True when a node existed."""
        node = await self.repo.get_node_by_source(node_type, source_id)
        if node is None:
            return False
        return await self.repo.delete_node(node.id)

    # --- Node -> entity resolution (access scoping) ---

    async def node_entity_id(self, node: GraphNode) -> int | None:
        """The entity a node belongs to, or None when its source
        record is gone."""
        db = self._require_db()
        if node.node_type == GraphNodeType.ENTITY.value:
            return node.source_id
        model = _NODE_MODELS.get(node.node_type)
        if model is None:
            return None
        row = await db.get(model, node.source_id)
        if row is None:
            return None
        if isinstance(row, EvidenceRecord):
            asset = await db.get(KnowledgeAsset, row.knowledge_asset_id)
            return asset.entity_id if asset is not None else None
        return row.entity_id

    async def _accessible_entity_ids(self, user: User) -> set[int]:
        """Ids of entities the caller may read: their own plus
        shared (unowned) ones — the Sprint 1 list rule."""
        db = self._require_db()
        result = await db.execute(
            select(Entity.id).where(
                or_(Entity.owner_id == user.id, Entity.owner_id.is_(None))
            )
        )
        return set(result.scalars().all())

    async def _assert_node_access(
        self, node: GraphNode, user: User
    ) -> int:
        """Raise 403 unless the caller may read the node's entity.
        Returns the entity id."""
        db = self._require_db()
        entity_id = await self.node_entity_id(node)
        if entity_id is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Graph node has no source record",
            )
        entity = await db.get(Entity, entity_id)
        if entity is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Entity not found",
            )
        if (
            entity.owner_id is not None
            and entity.owner_id != user.id
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You may only access your own entities",
            )
        return entity_id

    async def _assert_node_owner(
        self, node: GraphNode, user: User
    ) -> int:
        """Raise 403 unless the caller owns the node's entity.
        Returns the entity id."""
        db = self._require_db()
        entity_id = await self.node_entity_id(node)
        if entity_id is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Graph node has no source record",
            )
        entity = await db.get(Entity, entity_id)
        if entity is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Entity not found",
            )
        if entity.owner_id != user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You may only change your own entities",
            )
        return entity_id

    async def _accessible_node_ids(
        self, user: User, node_type: str | None = None
    ) -> set[int]:
        """Ids of graph nodes whose source record belongs to an
        entity the caller may read. The access filter is computed
        from the source tables (not by loading nodes and checking
        them one by one), so list endpoints page the visible set
        correctly."""
        db = self._require_db()
        ids = await self._accessible_entity_ids(user)
        allowed: dict[str, set[int]] = {}
        # Entity nodes: the entity itself must be accessible.
        allowed[GraphNodeType.ENTITY.value] = set(ids)
        for node_type_value, model in _NODE_MODELS.items():
            if node_type_value == GraphNodeType.ENTITY.value:
                continue
            if node_type_value == GraphNodeType.EVIDENCE_RECORD.value:
                stmt = (
                    select(EvidenceRecord.id)
                    .join(
                        KnowledgeAsset,
                        KnowledgeAsset.id
                        == EvidenceRecord.knowledge_asset_id,
                    )
                    .where(KnowledgeAsset.entity_id.in_(ids))
                )
            else:
                stmt = select(model.id).where(model.entity_id.in_(ids))
            allowed[node_type_value] = set(
                (await db.execute(stmt)).scalars().all()
            )
        types = (
            [node_type]
            if node_type is not None
            else list(_NODE_MODELS)
        )
        clauses = [
            (GraphNode.node_type == t)
            & (GraphNode.source_id.in_(allowed[t]))
            for t in types
            if allowed.get(t)
        ]
        if not clauses:
            return set()
        stmt = select(GraphNode.id).where(or_(*clauses))
        return set((await db.execute(stmt)).scalars().all())

    async def list_accessible_nodes(
        self,
        user: User,
        *,
        node_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[GraphNode]:
        """Nodes whose source record belongs to an entity the
        caller may read, optionally filtered by type."""
        db = self._require_db()
        node_ids = await self._accessible_node_ids(user, node_type)
        if not node_ids:
            return []
        stmt = (
            select(GraphNode)
            .where(GraphNode.id.in_(node_ids))
            .order_by(GraphNode.created_at, GraphNode.id)
            .limit(limit)
            .offset(offset)
        )
        return list((await db.execute(stmt)).scalars().all())

    async def list_accessible_edges(
        self,
        user: User,
        *,
        from_node_id: int | None = None,
        to_node_id: int | None = None,
        relationship_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[GraphEdge]:
        """Edges visible to the caller: edges whose from-node
        belongs to an entity they may read, optionally filtered."""
        db = self._require_db()
        node_ids = await self._accessible_node_ids(user)
        if not node_ids:
            return []
        stmt = select(GraphEdge).where(
            GraphEdge.from_node_id.in_(node_ids)
        )
        if from_node_id is not None:
            stmt = stmt.where(GraphEdge.from_node_id == from_node_id)
        if to_node_id is not None:
            stmt = stmt.where(GraphEdge.to_node_id == to_node_id)
        if relationship_type is not None:
            stmt = stmt.where(
                GraphEdge.relationship_type == relationship_type
            )
        stmt = (
            stmt.order_by(GraphEdge.created_at, GraphEdge.id)
            .limit(limit)
            .offset(offset)
        )
        return list((await db.execute(stmt)).scalars().all())

    async def get_node_for_read(
        self, node_id: int, user: User
    ) -> GraphNode:
        """Load one node by id, raising 404 when unknown and 403
        unless the caller may read its entity."""
        node = await self.repo.get_node(node_id)
        if node is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Graph node not found",
            )
        await self._assert_node_access(node, user)
        return node

    # --- Automatic sync: one source record -> node + edges ---

    async def sync_entity(self, entity: Entity) -> GraphNode:
        """An entity's node (metadata refreshed on every sync)."""
        return await self.ensure_node(
            GraphNodeType.ENTITY.value,
            entity.id,
            _entity_metadata(entity),
        )

    async def _entity_node_for(self, entity_id: int) -> GraphNode:
        """The node for an entity, creating it from the source
        record when it does not exist yet."""
        db = self._require_db()
        node = await self.repo.get_node_by_source(
            GraphNodeType.ENTITY.value, entity_id
        )
        if node is not None:
            return node
        entity = await db.get(Entity, entity_id)
        if entity is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Entity not found",
            )
        return await self.sync_entity(entity)

    async def sync_knowledge_asset(
        self, asset: KnowledgeAsset
    ) -> GraphNode:
        """An asset's node plus the OWNS edge from its entity."""
        node = await self.ensure_node(
            GraphNodeType.KNOWLEDGE_ASSET.value,
            asset.id,
            _asset_metadata(asset),
        )
        entity_node = await self._entity_node_for(asset.entity_id)
        await self.ensure_edge(
            entity_node,
            node,
            GraphRelationshipType.OWNS.value,
            source=AUTO_SOURCE,
        )
        return node

    async def _sync_holder_evidence_edges(
        self, holder_node: GraphNode, asset_id: int | None
    ) -> None:
        """(Re)create the HAS_EVIDENCE edges from one holder node
        (a Skill or Certification) to the evidence on its linked
        asset. Only stale edges are removed — an edge to evidence
        that is still on the asset stays, and an edge a user made
        by hand to other evidence is left alone. A PATCH that
        changes — or removes — the link resyncs cleanly: with
        asset_id None, every edge to the old asset's evidence is
        stale and goes."""
        db = self._require_db()
        keep: set[int] = set()
        if asset_id is not None:
            result = await db.execute(
                select(EvidenceRecord).where(
                    EvidenceRecord.knowledge_asset_id == asset_id
                )
            )
            for evidence in result.scalars().all():
                evidence_node = await self.sync_evidence(evidence)
                keep.add(evidence_node.id)
                await self.ensure_edge(
                    holder_node,
                    evidence_node,
                    GraphRelationshipType.HAS_EVIDENCE.value,
                    source=AUTO_SOURCE,
                )
        existing = await self.repo.list_edges(
            from_node_id=holder_node.id,
            relationship_type=GraphRelationshipType.HAS_EVIDENCE.value,
            limit=1000,
        )
        for edge in existing:
            # Only auto-created edges are ever removed by a sync:
            # a relationship a user made by hand is theirs to keep
            # (or delete through the API).
            if (
                edge.source == AUTO_SOURCE
                and edge.to_node_id not in keep
            ):
                await self.repo.delete_edge(edge.id)

    async def resync_holder_evidence(
        self, node_type: str, holder_id: int, asset_id: int | None
    ) -> None:
        """Resync one Skill / Certification's evidence edges after
        its knowledge_asset_id changes (or is cleared). A no-op
        when the holder has no graph node yet."""
        node = await self.repo.get_node_by_source(node_type, holder_id)
        if node is None:
            return
        await self._sync_holder_evidence_edges(node, asset_id)

    async def sync_skill(self, skill: Skill) -> GraphNode:
        """A skill's node plus OWNS and HAS_SKILL from its entity,
        and HAS_EVIDENCE edges to the evidence on its linked
        asset (when it has one)."""
        node = await self.ensure_node(
            GraphNodeType.SKILL.value,
            skill.id,
            _skill_metadata(skill),
        )
        entity_node = await self._entity_node_for(skill.entity_id)
        await self.ensure_edge(
            entity_node,
            node,
            GraphRelationshipType.OWNS.value,
            source=AUTO_SOURCE,
        )
        await self.ensure_edge(
            entity_node,
            node,
            GraphRelationshipType.HAS_SKILL.value,
            source=AUTO_SOURCE,
        )
        if skill.knowledge_asset_id is not None:
            await self._sync_holder_evidence_edges(
                node, skill.knowledge_asset_id
            )
        return node

    async def sync_certification(
        self, cert: Certification
    ) -> GraphNode:
        """A certification's node plus OWNS and HAS_CERTIFICATION
        from its entity, and HAS_EVIDENCE edges to the evidence on
        its linked asset (when it has one). This is the Sprint 3
        fix for the Sprint 2 title-matching limitation: the
        certification now carries a real asset link, like Skill."""
        node = await self.ensure_node(
            GraphNodeType.CERTIFICATION.value,
            cert.id,
            _certification_metadata(cert),
        )
        entity_node = await self._entity_node_for(cert.entity_id)
        await self.ensure_edge(
            entity_node,
            node,
            GraphRelationshipType.OWNS.value,
            source=AUTO_SOURCE,
        )
        await self.ensure_edge(
            entity_node,
            node,
            GraphRelationshipType.HAS_CERTIFICATION.value,
            source=AUTO_SOURCE,
        )
        if cert.knowledge_asset_id is not None:
            await self._sync_holder_evidence_edges(
                node, cert.knowledge_asset_id
            )
        return node

    async def sync_evidence(
        self, evidence: EvidenceRecord
    ) -> GraphNode:
        """An evidence record's node plus the HAS_EVIDENCE edge
        from its asset — and from every Skill / Certification
        linked to that asset, so holders point at their evidence
        directly."""
        db = self._require_db()
        node = await self.ensure_node(
            GraphNodeType.EVIDENCE_RECORD.value,
            evidence.id,
            _evidence_metadata(evidence),
        )
        asset = await db.get(
            KnowledgeAsset, evidence.knowledge_asset_id
        )
        if asset is None:
            return node
        asset_node = await self.sync_knowledge_asset(asset)
        await self.ensure_edge(
            asset_node,
            node,
            GraphRelationshipType.HAS_EVIDENCE.value,
            source=AUTO_SOURCE,
        )
        for model, node_type in (
            (Skill, GraphNodeType.SKILL.value),
            (Certification, GraphNodeType.CERTIFICATION.value),
        ):
            result = await db.execute(
                select(model).where(
                    model.knowledge_asset_id == asset.id
                )
            )
            for holder in result.scalars().all():
                holder_node = await self.repo.get_node_by_source(
                    node_type, holder.id
                )
                if holder_node is not None:
                    await self.ensure_edge(
                        holder_node,
                        node,
                        GraphRelationshipType.HAS_EVIDENCE.value,
                        source=AUTO_SOURCE,
                    )
        return node

    async def sync_family_record(
        self, record: FamilyRecord
    ) -> GraphNode:
        """A family record's node plus OWNS and HAS_RECORD from
        its entity."""
        node = await self.ensure_node(
            GraphNodeType.FAMILY_RECORD.value,
            record.id,
            _family_metadata(record),
        )
        entity_node = await self._entity_node_for(record.entity_id)
        await self.ensure_edge(
            entity_node,
            node,
            GraphRelationshipType.OWNS.value,
            source=AUTO_SOURCE,
        )
        await self.ensure_edge(
            entity_node,
            node,
            GraphRelationshipType.HAS_RECORD.value,
            source=AUTO_SOURCE,
        )
        return node

    async def sync_legacy_asset(
        self, asset: LegacyAsset
    ) -> GraphNode:
        """A legacy asset's node plus OWNS and HAS_LEGACY from
        its entity."""
        node = await self.ensure_node(
            GraphNodeType.LEGACY_ASSET.value,
            asset.id,
            _legacy_metadata(asset),
        )
        entity_node = await self._entity_node_for(asset.entity_id)
        await self.ensure_edge(
            entity_node,
            node,
            GraphRelationshipType.OWNS.value,
            source=AUTO_SOURCE,
        )
        await self.ensure_edge(
            entity_node,
            node,
            GraphRelationshipType.HAS_LEGACY.value,
            source=AUTO_SOURCE,
        )
        return node

    async def sync_entity_relationship(
        self, rel: EntityRelationship
    ) -> GraphEdge | None:
        """One Sprint 1 entity relationship as a graph edge
        between the two entity nodes. Returns None when the type
        has no graph mapping (there is none in Sprint 3 — every
        Sprint 1 type maps)."""
        graph_type = ENTITY_RELATIONSHIP_TO_GRAPH.get(
            rel.relationship_type
        )
        if graph_type is None:
            return None
        from_node = await self._entity_node_for(rel.from_entity_id)
        to_node = await self._entity_node_for(rel.to_entity_id)
        return await self.ensure_edge(
            from_node,
            to_node,
            graph_type,
            confidence=rel.confidence,
            source=rel.source or AUTO_SOURCE,
        )

    async def sync_all(self, user: User) -> dict[str, int]:
        """Backfill: create nodes and edges for every record on
        every entity the caller may read — for records created
        before the graph existed, or any drift. Idempotent: run it
        twice and the second run creates nothing. Returns the
        counts created (nodes / edges) and the totals after."""
        db = self._require_db()

        async def _count(model) -> int:
            result = await db.execute(
                select(func.count()).select_from(model)
            )
            return int(result.scalar_one())

        before_nodes = await _count(GraphNode)
        before_edges = await _count(GraphEdge)
        ids = await self._accessible_entity_ids(user)
        result = await db.execute(
            select(Entity).where(Entity.id.in_(ids))
        )
        for entity in result.scalars().all():
            await self.sync_entity(entity)
            for model, sync in (
                (KnowledgeAsset, self.sync_knowledge_asset),
                (Skill, self.sync_skill),
                (Certification, self.sync_certification),
                (FamilyRecord, self.sync_family_record),
                (LegacyAsset, self.sync_legacy_asset),
            ):
                rows = await db.execute(
                    select(model).where(model.entity_id == entity.id)
                )
                for row in rows.scalars().all():
                    await sync(row)
            ev_rows = await db.execute(
                select(EvidenceRecord)
                .join(
                    KnowledgeAsset,
                    KnowledgeAsset.id
                    == EvidenceRecord.knowledge_asset_id,
                )
                .where(KnowledgeAsset.entity_id == entity.id)
            )
            for evidence in ev_rows.scalars().all():
                await self.sync_evidence(evidence)
            rel_rows = await db.execute(
                select(EntityRelationship).where(
                    EntityRelationship.from_entity_id == entity.id
                )
            )
            for rel in rel_rows.scalars().all():
                await self.sync_entity_relationship(rel)
        after_nodes = await _count(GraphNode)
        after_edges = await _count(GraphEdge)
        return {
            "nodes_created": after_nodes - before_nodes,
            "edges_created": after_edges - before_edges,
            "nodes_total": after_nodes,
            "edges_total": after_edges,
        }

    # --- Traversal and path (repository-agnostic) ---

    async def traverse(
        self, node_id: int, *, depth: int = 2
    ) -> tuple[list[GraphNode], list[GraphEdge]]:
        """Breadth-first walk from one node out to `depth` hops.
        Returns (nodes, edges) in visit order, the start node
        first. AT-305 uses find_path for a single path; this is
        the neighborhood walk."""
        start = await self.repo.get_node(node_id)
        if start is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Graph node not found",
            )
        seen_nodes: dict[int, GraphNode] = {start.id: start}
        seen_edges: dict[int, GraphEdge] = {}
        frontier = [start.id]
        for _ in range(max(depth, 0)):
            next_frontier: list[int] = []
            for current_id in frontier:
                for edge, neighbor in await self.repo.neighbors(
                    current_id
                ):
                    seen_edges.setdefault(edge.id, edge)
                    if neighbor.id not in seen_nodes:
                        seen_nodes[neighbor.id] = neighbor
                        next_frontier.append(neighbor.id)
            frontier = next_frontier
            if not frontier:
                break
        return list(seen_nodes.values()), list(seen_edges.values())

    async def find_path(
        self,
        from_node_id: int,
        to_node_id: int,
        *,
        max_depth: int = 5,
    ) -> tuple[list[GraphNode], list[GraphEdge]]:
        """Shortest path between two nodes (breadth-first), or
        ([], []) when no path exists within max_depth. AT-305."""
        start = await self.repo.get_node(from_node_id)
        target = await self.repo.get_node(to_node_id)
        if start is None or target is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Graph node not found",
            )
        if from_node_id == to_node_id:
            return [start], []
        # parent[node_id] = (previous node_id, edge used)
        parent: dict[int, tuple[int | None, GraphEdge | None]] = {
            from_node_id: (None, None)
        }
        queue: deque[int] = deque([from_node_id])
        found = False
        for _ in range(max_depth):
            level_size = len(queue)
            for _ in range(level_size):
                current_id = queue.popleft()
                for edge, neighbor in await self.repo.neighbors(
                    current_id
                ):
                    if neighbor.id in parent:
                        continue
                    parent[neighbor.id] = (current_id, edge)
                    if neighbor.id == to_node_id:
                        found = True
                        queue.clear()
                        break
                    queue.append(neighbor.id)
                if found:
                    break
            if found or not queue:
                break
        if not found:
            return [], []
        nodes: list[GraphNode] = []
        edges: list[GraphEdge] = []
        current_id: int | None = to_node_id
        while current_id is not None:
            node = await self.repo.get_node(current_id)
            if node is None:  # pragma: no cover - node existed a moment ago
                break
            nodes.append(node)
            prev_id, edge = parent[current_id]
            if edge is not None:
                edges.append(edge)
            current_id = prev_id
        nodes.reverse()
        edges.reverse()
        return nodes, edges

    async def subgraph_for_entity(
        self, entity: Entity
    ) -> tuple[GraphNode, list[GraphNode], list[GraphEdge]]:
        """An entity's subgraph: its node, its 1-hop neighbors,
        and the edges between them."""
        center = await self._entity_node_for(entity.id)
        pairs = await self.repo.neighbors(center.id)
        nodes = [center] + [neighbor for _, neighbor in pairs]
        edges = [edge for edge, _ in pairs]
        return center, nodes, edges

    # --- Manual relationships (API-driven, audited) ---

    def _log_graph_event(
        self,
        *,
        event_type: str,
        entity_id: int | None,
        edge: GraphEdge,
        actor: str | None,
    ) -> None:
        """Append one graph audit entry to the session (the
        caller commits)."""
        db = self._require_db()
        db.add(
            AuditLog(
                event_type=event_type,
                entity_id=entity_id,
                record_type="GraphEdge",
                record_id=edge.id,
                actor=actor,
                details=json.dumps(
                    {
                        "from_node_id": edge.from_node_id,
                        "to_node_id": edge.to_node_id,
                        "relationship_type": edge.relationship_type,
                        "confidence": edge.confidence,
                        "source": edge.source,
                    }
                ),
            )
        )

    async def create_relationship(
        self,
        *,
        user: User,
        from_node_id: int,
        to_node_id: int,
        relationship_type: str,
        confidence: float = 1.0,
        source: str | None = None,
    ) -> GraphEdge:
        """Create a relationship by hand: validate both nodes,
        reject a self-edge and a duplicate, require owning the
        from-node's entity, and write an audit entry (AT-306)."""
        from_node = await self.repo.get_node(from_node_id)
        if from_node is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="From node not found",
            )
        to_node = await self.repo.get_node(to_node_id)
        if to_node is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="To node not found",
            )
        if from_node_id == to_node_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A node cannot have a relationship with itself",
            )
        entity_id = await self._assert_node_owner(from_node, user)
        await self._assert_node_access(to_node, user)
        existing = await self.repo.get_edge_by_triple(
            from_node_id, to_node_id, relationship_type
        )
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Relationship already exists",
            )
        edge = await self.repo.create_edge(
            from_node_id=from_node_id,
            to_node_id=to_node_id,
            relationship_type=relationship_type,
            confidence=confidence,
            source=source,
        )
        self._log_graph_event(
            event_type=EVENT_GRAPH_RELATIONSHIP_CREATED,
            entity_id=entity_id,
            edge=edge,
            actor=user.email,
        )
        return edge

    async def delete_relationship(
        self, *, user: User, edge_id: int
    ) -> None:
        """Delete a relationship: require owning the from-node's
        entity, and write an audit entry."""
        edge = await self.repo.get_edge(edge_id)
        if edge is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Graph relationship not found",
            )
        from_node = await self.repo.get_node(edge.from_node_id)
        if from_node is None:  # pragma: no cover - edge implies a node
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Graph node not found",
            )
        entity_id = await self._assert_node_owner(from_node, user)
        deleted = await self.repo.delete_edge(edge_id)
        if deleted:
            self._log_graph_event(
                event_type=EVENT_GRAPH_RELATIONSHIP_DELETED,
                entity_id=entity_id,
                edge=edge,
                actor=user.email,
            )

    # --- Knowledge inventory queries (graph-based) ---

    async def _nodes_without_evidence(
        self,
        user: User,
        node_type: str,
        entity_id: int | None = None,
    ) -> list[GraphNode]:
        """Nodes of one type with no outgoing HAS_EVIDENCE edge.
        The graph answers the question — no join against the
        source tables decides it."""
        nodes = await self.list_accessible_nodes(
            user, node_type=node_type, limit=1000
        )
        if entity_id is not None:
            db = self._require_db()
            entity = await db.get(Entity, entity_id)
            if entity is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Entity not found",
                )
            if (
                entity.owner_id is not None
                and entity.owner_id != user.id
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You may only access your own entities",
                )
            nodes = [
                n
                for n in nodes
                if await self.node_entity_id(n) == entity_id
            ]
        unevidenced: list[GraphNode] = []
        for node in nodes:
            edges = await self.repo.list_edges(
                from_node_id=node.id,
                relationship_type=GraphRelationshipType.HAS_EVIDENCE.value,
                limit=1,
            )
            if not edges:
                unevidenced.append(node)
        return unevidenced

    async def query_knowledge_no_evidence(
        self, user: User, entity_id: int | None = None
    ) -> list[KnowledgeAsset]:
        """Knowledge assets with no supporting evidence — decided
        by the graph (no outgoing HAS_EVIDENCE edge)."""
        db = self._require_db()
        nodes = await self._nodes_without_evidence(
            user, GraphNodeType.KNOWLEDGE_ASSET.value, entity_id
        )
        if not nodes:
            return []
        result = await db.execute(
            select(KnowledgeAsset).where(
                KnowledgeAsset.id.in_([n.source_id for n in nodes])
            )
        )
        by_id = {row.id: row for row in result.scalars().all()}
        return [by_id[n.source_id] for n in nodes if n.source_id in by_id]

    async def query_skills_no_evidence(
        self, user: User, entity_id: int | None = None
    ) -> list[Skill]:
        """Skills with no evidence — decided by the graph."""
        db = self._require_db()
        nodes = await self._nodes_without_evidence(
            user, GraphNodeType.SKILL.value, entity_id
        )
        if not nodes:
            return []
        result = await db.execute(
            select(Skill).where(
                Skill.id.in_([n.source_id for n in nodes])
            )
        )
        by_id = {row.id: row for row in result.scalars().all()}
        return [by_id[n.source_id] for n in nodes if n.source_id in by_id]

    async def query_certifications_no_evidence(
        self, user: User, entity_id: int | None = None
    ) -> list[Certification]:
        """Certifications with no evidence — decided by the graph,
        through the real asset link (the Sprint 3 fix). A
        certification is evidenced when its node has a
        HAS_EVIDENCE edge, which exists exactly when its linked
        KnowledgeAsset holds evidence."""
        db = self._require_db()
        nodes = await self._nodes_without_evidence(
            user, GraphNodeType.CERTIFICATION.value, entity_id
        )
        if not nodes:
            return []
        result = await db.execute(
            select(Certification).where(
                Certification.id.in_([n.source_id for n in nodes])
            )
        )
        by_id = {row.id: row for row in result.scalars().all()}
        return [
            by_id[n.source_id] for n in nodes if n.source_id in by_id
        ]

    async def query_legacy_no_preservation_status(
        self, user: User, entity_id: int | None = None
    ) -> list[LegacyAsset]:
        """Legacy assets whose preservation status was never set.
        The node set comes from the graph; the status itself is a
        source-record attribute, so the filter reads the source
        rows — the graph finds the assets, the record says whether
        preservation is in place."""
        db = self._require_db()
        nodes = await self.list_accessible_nodes(
            user, node_type=GraphNodeType.LEGACY_ASSET.value, limit=1000
        )
        if entity_id is not None:
            entity = await db.get(Entity, entity_id)
            if entity is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Entity not found",
                )
            if (
                entity.owner_id is not None
                and entity.owner_id != user.id
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You may only access your own entities",
                )
            nodes = [
                n
                for n in nodes
                if await self.node_entity_id(n) == entity_id
            ]
        if not nodes:
            return []
        result = await db.execute(
            select(LegacyAsset).where(
                LegacyAsset.id.in_([n.source_id for n in nodes])
            )
        )
        return [
            row
            for row in result.scalars().all()
            if not row.preservation_status
        ]
