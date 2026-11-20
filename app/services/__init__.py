"""Engine v1 service layer — database-backed services.

`app/engines/` holds pure functions (numbers in, score out). Services
live here because they do async database work: loading entities,
enforcing ownership, and recording state vectors.
"""

from app.services.entity_service import (
    assert_entity_access,
    create_relationship,
    get_entity_or_404,
    list_neighbors,
    list_relationships,
)
from app.services.state_vector_service import (
    compute_master_index_for_snapshot,
    get_entity_state_vector_history,
    get_latest_entity_state_vector,
    record_entity_state_vector,
)

__all__ = [
    "assert_entity_access",
    "compute_master_index_for_snapshot",
    "create_relationship",
    "get_entity_or_404",
    "get_entity_state_vector_history",
    "get_latest_entity_state_vector",
    "list_neighbors",
    "list_relationships",
    "record_entity_state_vector",
]
