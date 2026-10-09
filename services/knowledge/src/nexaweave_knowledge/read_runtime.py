"""Read-only page host: persisted owner check, ledger guard, direct Neo4j reads."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

import psycopg

from .bindings import ScopeBindingStore
from .commands import KnowledgeCommandDispatcher, _validated_facts
from .contracts import GraphPage, KnowledgeScope
from .operations import Conflict, Ledger
from .graph_page_provider import GraphPageProvider


def _pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            raise ValueError
        value[key] = item
    return value


def _scope_json(raw: str) -> KnowledgeScope:
    try:
        value = json.loads(raw, object_pairs_hook=_pairs)
        if (type(value) is not dict or set(value) != {"schema_version", "workspace_id", "project_id",
                                                   "graph_id", "run_id", "branch_id", "layer"}
                or type(value["schema_version"]) is not int or value["schema_version"] != 1):
            raise ValueError
        scope = KnowledgeScope.model_validate_json(json.dumps(value, separators=(",", ":")))
        if scope.model_dump(mode="json") != value:
            raise ValueError
        return scope
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise ValueError("invalid knowledge read scope") from None


def _text(value: object, *, maximum: int = 1024) -> str:
    if (type(value) is not str or not 1 <= len(value) <= maximum
            or any(ord(char) < 33 or ord(char) > 126 for char in value)):
        raise ValueError("invalid knowledge read configuration")
    return value


def _secret(value: object) -> str:
    if (type(value) is not str or not 1 <= len(value) <= 1024
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError("invalid knowledge read configuration")
    return value


@dataclass(frozen=True, repr=False)
class ReadSettings:
    principal: str
    display_graph_id: str
    scope: KnowledgeScope
    pg_host: str
    pg_port: int
    pg_database: str
    pg_user: str
    pg_password: str
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str

    @classmethod
    def from_environment(cls):
        env = os.environ
        try:
            principal = env["KNOWLEDGE_PRINCIPAL"]
            display = env["KNOWLEDGE_DISPLAY_GRAPH_ID"]
            from .bindings import _identity
            _identity(principal, display)
            scope = _scope_json(env["KNOWLEDGE_BOUND_SCOPE_JSON"])
            pg_host = _text(env["KNOWLEDGE_PG_HOST"], maximum=255)
            pg_port = int(env["KNOWLEDGE_PG_PORT"])
            if not 1 <= pg_port <= 65535 or str(pg_port) != env["KNOWLEDGE_PG_PORT"]:
                raise ValueError
            pg_database = _text(env["KNOWLEDGE_PG_DATABASE"], maximum=128)
            pg_user = _text(env["KNOWLEDGE_PG_USER"], maximum=128)
            pg_password = _secret(env["KNOWLEDGE_PG_PASSWORD"])
            neo4j_uri = _text(env["KNOWLEDGE_NEO4J_URI"], maximum=512)
            parsed = urlsplit(neo4j_uri)
            if (parsed.scheme not in {"bolt", "neo4j"} or not parsed.hostname or parsed.username
                    or parsed.password or parsed.path or parsed.query or parsed.fragment):
                raise ValueError
            neo4j_user = _text(env["KNOWLEDGE_NEO4J_USER"], maximum=128)
            neo4j_password = _secret(env["KNOWLEDGE_NEO4J_PASSWORD"])
            return cls(principal, display, scope, pg_host, pg_port, pg_database, pg_user,
                       pg_password, neo4j_uri, neo4j_user, neo4j_password)
        except (KeyError, ValueError, TypeError):
            raise ValueError("invalid knowledge read configuration") from None

    def connection(self):
        return psycopg.connect(host=self.pg_host, port=self.pg_port, dbname=self.pg_database,
                               user=self.pg_user, password=self.pg_password, connect_timeout=3)

    def driver(self):
        from neo4j import AsyncGraphDatabase
        return AsyncGraphDatabase.driver(self.neo4j_uri, auth=(self.neo4j_user, self.neo4j_password),
                                         connection_timeout=3, connection_acquisition_timeout=3)


class _DirectPageProvider(GraphPageProvider):
    def __init__(self, driver):
        self._read_driver = driver

    @property
    def _driver(self):
        return self._read_driver

    async def _rows(self, cypher: str, **params):
        async def query():
            records, _, _ = await self._read_driver.execute_query(
                cypher, parameters_=params, routing_="r")
            return [dict(record) for record in records]

        return await asyncio.wait_for(query(), timeout=20)


class ReadRuntimeProvider:
    def __init__(self, settings: ReadSettings, *, connection_factory: Callable | None = None,
                 driver_factory: Callable | None = None):
        self.settings = settings
        connect = connection_factory or settings.connection
        self._binding = ScopeBindingStore(connect)
        self._ledger = Ledger(connect)
        self._driver_factory = driver_factory or settings.driver

    async def authorize(self, principal, method, scope):
        return principal == self.settings.principal and method == "page" and scope == self.settings.scope

    async def page(self, scope, request):
        def work():
            with self._ledger.read_scope(scope):
                record = self._binding.resolve(self.settings.principal, self.settings.display_graph_id)
                if record.scope != scope:
                    raise Conflict("knowledge binding conflict")

                async def fetch():
                    driver = self._driver_factory()
                    try:
                        result = await _DirectPageProvider(driver).page(scope, request)
                        _validated_facts(result, scope, expected_type=GraphPage, maximum=100)
                        return result
                    finally:
                        closing = driver.close()
                        if inspect.isawaitable(closing):
                            await closing

                return asyncio.run(fetch())

        return await asyncio.to_thread(work)


def dispatcher(settings: ReadSettings, *, connection_factory=None, driver_factory=None):
    provider = ReadRuntimeProvider(settings, connection_factory=connection_factory,
                                   driver_factory=driver_factory)
    return KnowledgeCommandDispatcher(provider, object(), provider.authorize,
                                      allow_model_calls=False, read_timeout_seconds=30)
