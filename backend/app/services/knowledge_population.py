"""Basic, synthetic OASIS population from one complete bounded graph read."""

from __future__ import annotations

import csv
import io
import json
import random
import re
from dataclasses import dataclass
from datetime import date

from .knowledge_reader import EntityNode, KnowledgeReadError
from .oasis_profile_generator import OasisProfileGenerator


_TYPE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}\Z")
_MAX_BYTES = 2 * 1024 * 1024
_SYNTHETIC_FIELDS = ["user_name", "bio", "persona", "age", "gender", "mbti", "country",
                     "profession", "interested_topics", "karma", "friend_count",
                     "follower_count", "statuses_count"]


class PopulationError(KnowledgeReadError):
    pass


@dataclass
class _Population:
    preview: dict
    profiles: list


def _bounded(value) -> bytes:
    try:
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False,
                         separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise PopulationError("invalid_reply") from None
    if len(raw) > _MAX_BYTES:
        raise PopulationError("result_too_large")
    return raw


class KnowledgePopulation:
    def __init__(self, graph_data):
        self._graph_data = graph_data

    def build(self, *, types=None, max_agents=10, seed=0) -> _Population:
        if (type(max_agents) is not int or not 1 <= max_agents <= 100
                or type(seed) is not int or not 0 <= seed <= 0xFFFFFFFF
                or (types is not None and (type(types) is not list or len(types) > 50
                    or any(type(item) is not str or not _TYPE.fullmatch(item) for item in types)
                    or len(set(types)) != len(types)))):
            raise PopulationError("invalid_request")
        graph = self._graph_data
        allowed = set(types or ())
        eligible = []
        for node in graph["nodes"]:
            custom = [label for label in node["labels"] if label not in {"Entity", "Node"}]
            if custom and (not allowed or any(label in allowed for label in custom)):
                eligible.append(node)
        eligible.sort(key=lambda node: node["uuid"])
        if not eligible:
            raise PopulationError("empty_selection")
        selected = eligible[:max_agents]
        incident = {node["uuid"]: [] for node in selected}
        for edge in graph["edges"]:
            source, target = edge["source_node_uuid"], edge["target_node_uuid"]
            if source in incident:
                incident[source].append({"edge_uuid": edge["uuid"], "direction": "outgoing",
                                         "edge_name": edge["name"], "source_node_uuid": source,
                                         "target_node_uuid": target, "fact": edge["fact"],
                                         "episode_ids": edge["episodes"],
                                         "evidence_ids": edge["evidence_ids"]})
            if target in incident and target != source:
                incident[target].append({"edge_uuid": edge["uuid"], "direction": "incoming",
                                         "edge_name": edge["name"], "source_node_uuid": source,
                                         "target_node_uuid": target, "fact": edge["fact"],
                                         "episode_ids": edge["episodes"],
                                         "evidence_ids": edge["evidence_ids"]})
        generator = OasisProfileGenerator(basic_only=True, rng=random.Random(seed))
        generated_on = date.today().isoformat()
        profiles = []
        grounding = {}
        usernames = set()
        for index, node in enumerate(selected):
            entity = EntityNode(node["uuid"], node["name"], list(node["labels"]),
                                node["summary"], dict(node["attributes"]))
            profile = generator.generate_profile_from_entity(entity, index, use_llm=False)
            original_username = profile.user_name
            if profile.user_name in usernames:
                suffix = 2
                while f"{original_username}_{suffix}" in usernames:
                    suffix += 1
                profile.user_name = f"{original_username}_{suffix}"
            usernames.add(profile.user_name)
            profile.created_at = generated_on
            profiles.append(profile)
            grounding[node["uuid"]] = {
                "source_entity_uuid": node["uuid"], "labels": list(node["labels"]),
                "summary": node["summary"], "attributes": node["attributes"],
                "episode_ids": list(node["episodes"]), "evidence_ids": list(node["evidence_ids"]),
                "facts": sorted(incident[node["uuid"]], key=lambda item: item["edge_uuid"]),
            }
        preview = {"graph_id": graph["graph_id"], "generator": "inherited_rule_based_v1",
                   "enrichment": "none", "llm_used": False, "simulation_executed": False,
                   "snapshot_consistent": False, "profile_date": generated_on,
                   "eligible_count": len(eligible), "selected_count": len(selected),
                   "synthetic_fields": list(_SYNTHETIC_FIELDS),
                   "profiles": [profile.to_dict() for profile in profiles], "grounding": grounding}
        _bounded(preview)
        return _Population(preview, profiles)

    def export(self, *, platform, types=None, max_agents=10, seed=0):
        if type(platform) is not str or platform not in {"twitter", "reddit"}:
            raise PopulationError("invalid_request")
        result = self.build(types=types, max_agents=max_agents, seed=seed)
        if platform == "reddit":
            body = _bounded([profile.to_reddit_format() for profile in result.profiles])
            return body, "application/json; charset=utf-8", "oasis-reddit-profiles.json"
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["user_id", "name", "username", "user_char", "description"])
        for index, profile in enumerate(result.profiles):
            writer.writerow(OasisProfileGenerator.twitter_loader_row(profile, index))
        body = output.getvalue().encode("utf-8")
        if len(body) > _MAX_BYTES:
            raise PopulationError("result_too_large")
        return body, "text/csv; charset=utf-8", "oasis-twitter-profiles.csv"
