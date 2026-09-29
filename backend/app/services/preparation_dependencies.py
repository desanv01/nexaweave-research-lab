"""Trusted graph-bound dependencies for inherited simulation preparation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .oasis_profile_generator import OasisProfileGenerator
from .simulation_config_generator import SimulationConfigGenerator


@dataclass(frozen=True)
class PreparationDependencies:
    graph_id: str
    reader: object
    grounding: dict
    profile_generator: Callable[[], OasisProfileGenerator]
    config_generator: Callable[[], SimulationConfigGenerator]


@dataclass(frozen=True)
class _ProjectionReader:
    """The manager filters the one accepted projection used for context/evidence."""
    accepted_reader: object
    graph: dict

    def filter_defined_entities(self, graph_id, defined_entity_types=None,
                                enrich_with_edges=True):
        return self.accepted_reader._filter_projected_graph(
            graph_id, self.graph, defined_entity_types, enrich_with_edges)


def create_knowledge_preparation(facade, graph_id: str, *, chat_client,
                                 model_name: str, base_url: str) -> PreparationDependencies:
    """Bind one accepted reader and real generators to the host's display graph.

    Only trusted service code should call this helper. The supplied chat client
    owns any model budget and transport policy; this helper constructs no SDK.
    """
    if graph_id != facade._settings.display_graph_id:
        raise ValueError("graph binding mismatch")
    completions = getattr(getattr(chat_client, "chat", None), "completions", None)
    if not callable(getattr(completions, "create", None)):
        raise ValueError("invalid chat client")
    if (type(model_name) is not str or not model_name.strip()
            or type(base_url) is not str or not base_url.strip()):
        raise ValueError("missing injected model metadata")
    reader = facade._reader()
    graph = reader.get_graph_data(graph_id)
    nodes = {node["uuid"]: node for node in graph["nodes"]}
    neighbors = {uuid: [] for uuid in nodes}
    incident = {uuid: [] for uuid in nodes}
    for edge in graph["edges"]:
        source, target = edge["source_node_uuid"], edge["target_node_uuid"]
        incident[source].append(edge)
        neighbors[source].append(nodes[target])
        if target != source:
            incident[target].append(edge)
            neighbors[target].append(nodes[source])
    grounding = {
        uuid: {"source_entity_uuid": uuid, "labels": list(node["labels"]),
               "summary": node["summary"], "attributes": dict(node["attributes"]),
               "episode_ids": list(node["episodes"]),
               "evidence_ids": list(node["evidence_ids"]),
               "facts": [{"edge_uuid": edge["uuid"], "fact": edge["fact"],
                          "episode_ids": list(edge["episodes"]),
                          "evidence_ids": list(edge["evidence_ids"])}
                         for edge in incident[uuid]]}
        for uuid, node in nodes.items()
    }

    def context(entity):
        if entity.uuid not in nodes:
            raise ValueError("entity outside bound graph")
        return {
            "facts": [edge["fact"] for edge in incident[entity.uuid] if edge["fact"]],
            "node_summaries": [node["summary"] for node in neighbors[entity.uuid]
                               if node["summary"]],
            "context": "",
        }

    return PreparationDependencies(
        graph_id=graph_id, reader=_ProjectionReader(reader, graph), grounding=grounding,
        profile_generator=lambda: OasisProfileGenerator(
            graph_id=graph_id, chat_client=chat_client, context_callback=context,
            model_name=model_name, base_url=base_url, strict_generation=True),
        config_generator=lambda: SimulationConfigGenerator(
            chat_client=chat_client, model_name=model_name, base_url=base_url,
            strict_generation=True),
    )
