"""Isolated Graphiti compatibility spike; telemetry is disabled before Graphiti import."""

from .contracts import KnowledgeScope, SourceEnvelope, OntologySpec, SearchQuery

__all__ = ["KnowledgeScope", "SourceEnvelope", "OntologySpec", "SearchQuery", "GraphitiKnowledgeProvider"]


def __getattr__(name):
    if name == "GraphitiKnowledgeProvider":
        from .provider import GraphitiKnowledgeProvider
        globals()[name] = GraphitiKnowledgeProvider
        return GraphitiKnowledgeProvider
    raise AttributeError(name)
