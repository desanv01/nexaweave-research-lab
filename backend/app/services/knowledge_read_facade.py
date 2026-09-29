"""Trusted native settings and lazy private-pipe graph-read facade."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .knowledge_reader import KnowledgeGraphReader, ReadLimits, _pairs, _scope
from .knowledge_transport import KnowledgeProcessClient


_CHILD_KEYS = ("KNOWLEDGE_PRINCIPAL", "KNOWLEDGE_DISPLAY_GRAPH_ID", "KNOWLEDGE_BOUND_SCOPE_JSON",
               "KNOWLEDGE_PG_HOST", "KNOWLEDGE_PG_PORT", "KNOWLEDGE_PG_DATABASE",
               "KNOWLEDGE_PG_USER", "KNOWLEDGE_PG_PASSWORD", "KNOWLEDGE_NEO4J_URI",
               "KNOWLEDGE_NEO4J_USER", "KNOWLEDGE_NEO4J_PASSWORD")


def _required(value, maximum=1024):
    if type(value) is not str or not 1 <= len(value) <= maximum or "\x00" in value:
        raise ValueError("invalid knowledge read configuration")
    return value


def _identifier(value, maximum=128):
    value = _required(value, maximum)
    if any(not 33 <= ord(char) <= 126 for char in value):
        raise ValueError("invalid knowledge read configuration")
    return value


def _password(value):
    value = _required(value)
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("invalid knowledge read configuration")
    return value


@dataclass(frozen=True, repr=False)
class ReadHostSettings:
    python: str
    bootstrap: str
    token: str
    principal: str
    display_graph_id: str
    scope: dict
    child_environment: dict[str, str]

    @classmethod
    def from_config(cls, config):
        try:
            python = _required(os.environ["KNOWLEDGE_PYTHON"], 1024)
            bootstrap = _required(os.environ["KNOWLEDGE_BOOTSTRAP_SCRIPT"], 1024)
            script = Path(bootstrap)
            if (not Path(python).is_absolute() or not Path(python).is_file()
                    or not script.is_absolute() or not script.is_file()
                    or script.is_symlink() or not os.access(python, os.X_OK)
                    or script.name != "read_bootstrap.py" or script.parent.name != "mirofish_knowledge"
                    or "site-packages" not in script.parts):
                raise ValueError
            token = _required(os.environ["KNOWLEDGE_READ_TOKEN"], 256)
            if (not 32 <= len(token) <= 256 or len(set(token)) < 8
                    or any(not 33 <= ord(char) <= 126 for char in token)):
                raise ValueError
            child = {key: _required(os.environ[key], 32768 if key == "KNOWLEDGE_BOUND_SCOPE_JSON" else 1024)
                     for key in _CHILD_KEYS}
            principal = child["KNOWLEDGE_PRINCIPAL"]
            display = child["KNOWLEDGE_DISPLAY_GRAPH_ID"]
            if (not 1 <= len(principal) <= 128 or not principal.strip()
                    or any(not 32 <= ord(char) <= 126 for char in principal)):
                raise ValueError
            from .knowledge_reader import _DISPLAY_ID
            if not _DISPLAY_ID.fullmatch(display):
                raise ValueError
            raw_scope = json.loads(child["KNOWLEDGE_BOUND_SCOPE_JSON"], object_pairs_hook=_pairs)
            scope = _scope(raw_scope)
            child["KNOWLEDGE_BOUND_SCOPE_JSON"] = json.dumps(scope, separators=(",", ":"))
            port = child["KNOWLEDGE_PG_PORT"]
            if not port.isascii() or not port.isdecimal() or not 1 <= int(port) <= 65535 or str(int(port)) != port:
                raise ValueError
            for key in ("KNOWLEDGE_PG_HOST", "KNOWLEDGE_PG_DATABASE", "KNOWLEDGE_PG_USER",
                        "KNOWLEDGE_NEO4J_USER"):
                _identifier(child[key], 255 if key == "KNOWLEDGE_PG_HOST" else 128)
            for key in ("KNOWLEDGE_PG_PASSWORD", "KNOWLEDGE_NEO4J_PASSWORD"):
                _password(child[key])
            neo4j = urlsplit(_identifier(child["KNOWLEDGE_NEO4J_URI"], 512))
            if (neo4j.scheme not in {"bolt", "neo4j"} or not neo4j.hostname
                    or neo4j.username or neo4j.password or neo4j.path or neo4j.query or neo4j.fragment):
                raise ValueError
            if neo4j.port is not None and not 1 <= neo4j.port <= 65535:
                raise ValueError
            if any(key in os.environ for key in ("PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE", "PGPASSFILE", "PGOPTIONS")):
                raise ValueError
            return cls(python, bootstrap, token, principal, display, scope, child)
        except (KeyError, ValueError, TypeError, OSError, UnicodeError):
            raise ValueError("invalid knowledge read configuration") from None


class KnowledgeReadFacade:
    def __init__(self, settings: ReadHostSettings, *, client_factory=None):
        self._settings = settings
        self._client_factory = client_factory

    def _reader(self):
        client = (self._client_factory() if self._client_factory is not None else
                  KnowledgeProcessClient(self._settings.python, self._settings.bootstrap,
                                         timeout_seconds=120,
                                         child_environment=dict(self._settings.child_environment)))
        return KnowledgeGraphReader(client, scope=dict(self._settings.scope),
                                    graph_id=self._settings.display_graph_id, limits=ReadLimits())

    def graph_data(self, graph_id):
        return self._reader().get_graph_data(graph_id)

    def entities(self, graph_id, types=None, enrich=True):
        return self._reader().filter_defined_entities(graph_id, types, enrich)

    def entity(self, graph_id, entity_uuid):
        return self._reader().get_entity_with_context(graph_id, entity_uuid)

    def population_preview(self, graph_id, *, types=None, max_agents=10, seed=0):
        from .knowledge_population import KnowledgePopulation
        return KnowledgePopulation(self.graph_data(graph_id)).build(
            types=types, max_agents=max_agents, seed=seed).preview

    def population_export(self, graph_id, *, platform, types=None, max_agents=10, seed=0):
        from .knowledge_population import KnowledgePopulation
        return KnowledgePopulation(self.graph_data(graph_id)).export(
            platform=platform, types=types, max_agents=max_agents, seed=seed)

    def preparation_dependencies(self, graph_id, *, chat_client, model_name, base_url):
        from .preparation_dependencies import create_knowledge_preparation
        return create_knowledge_preparation(
            self, graph_id, chat_client=chat_client, model_name=model_name, base_url=base_url)

    def report_agent(self, graph_id, simulation_id, simulation_requirement, *, model_client,
                     search_selector=None, interview_capability=None):
        from .report_dependencies import create_knowledge_report_agent
        return create_knowledge_report_agent(
            self, graph_id, simulation_id, simulation_requirement, model_client=model_client,
            search_selector=search_selector, interview_capability=interview_capability)
