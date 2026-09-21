"""Compact, auditable inputs for Arthur and the Kerekasztal."""

from .fact_packet import (
    FactPacketError,
    build_fact_packet,
    canonical_sha256,
    write_fact_packet,
)
from .kerekasztal import (
    KerekasztalError,
    KerekasztalOrchestrator,
    Role,
    RoleOpinion,
    RoleRunner,
    Verdict,
)
from .master_prompt import MasterPromptArtifact, load_master_prompt
from .openai_runner import OpenAIResponsesRoleRunner, PromptRegistry, PromptTemplate

__all__ = [
    "FactPacketError",
    "KerekasztalError",
    "KerekasztalOrchestrator",
    "MasterPromptArtifact",
    "OpenAIResponsesRoleRunner",
    "PromptRegistry",
    "PromptTemplate",
    "Role",
    "RoleOpinion",
    "RoleRunner",
    "Verdict",
    "build_fact_packet",
    "canonical_sha256",
    "load_master_prompt",
    "write_fact_packet",
]
