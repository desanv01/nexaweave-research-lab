"""Trusted, owned in-process lifecycle for prepared native OASIS simulations.

This module does not select a provider or construct a model. Callers supply
CAMEL-compatible model objects under a separate, approved budget policy.
"""

from __future__ import annotations

import asyncio
import csv
import json
import os
import random
import sqlite3
import uuid
from pathlib import Path

from .knowledge_report_tools import NeutralCapabilityError
from .native_controls import parse_execution_controls
from .native_reddit_profiles import (build_reddit_agent_graph,
                                     validate_reddit_profile)
from .zep_tools import AgentInterview, InterviewResult


def _bounded_json(path: Path):
    if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise NeutralCapabilityError("invalid_request")
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


class NativeSimulationSession:
    """Own one event loop and all real native environments until close()."""

    _claim_name = ".native_prepared_start_claim"

    def __init__(self, simulation_dir, *, graph_id, simulation_id, models,
                 seed: int, max_rounds: int, timeout_seconds: float = 120.0):
        if (type(graph_id) is not str or not graph_id or type(simulation_id) is not str
                or not simulation_id or type(seed) is not int
                or type(max_rounds) is not int or not 1 <= max_rounds <= 24
                or type(timeout_seconds) not in (int, float)
                or not 1 <= timeout_seconds <= 600 or type(models) is not dict):
            raise NeutralCapabilityError("invalid_request")
        try:
            self.simulation_dir = Path(simulation_dir).resolve(strict=True)
            if not self.simulation_dir.is_dir():
                raise ValueError
        except (OSError, ValueError, TypeError):
            raise NeutralCapabilityError("invalid_request") from None
        self.graph_id = graph_id
        self.simulation_id = simulation_id
        self.seed = seed
        self.max_rounds = max_rounds
        self.timeout_seconds = timeout_seconds
        self._results = {}
        self._reddit_graph = None
        self._reddit_actions = None
        self._runner = None
        self._closed = False
        self._started = False
        self._models = dict(models)
        self._controls = None
        self._control_network = {}
        self._config, self._agents, self._profiles, self._platforms = self._preflight()
        if set(self._models) != set(self._platforms) or any(
                model is None or not callable(getattr(model, "run", None))
                for model in self._models.values()):
            # CAMEL BaseModelBackend.run is the native agent's synchronous seam.
            raise NeutralCapabilityError("invalid_request")

    def _preflight(self):
        try:
            root = self.simulation_dir
            if self._native_output_exists():
                raise ValueError
            state = _bounded_json(root / "state.json")
            config = _bounded_json(root / "simulation_config.json")
            grounding = _bounded_json(root / "source_grounding.json")
            if (type(state) is not dict or type(config) is not dict
                    or type(grounding) is not dict
                    or state.get("status") != "ready"
                    or state.get("simulation_id") != self.simulation_id
                    or state.get("graph_id") != self.graph_id
                    or config.get("simulation_id") != self.simulation_id
                    or config.get("graph_id") != self.graph_id
                    or type(config.get("agent_configs")) is not list
                    or not 1 <= len(config["agent_configs"]) <= 500):
                raise ValueError
            clock = config.get("time_config")
            if (type(clock) is not dict
                    or type(clock.get("total_simulation_hours")) is not int
                    or not 1 <= clock["total_simulation_hours"] <= 168
                    or type(clock.get("minutes_per_round")) is not int
                    or not 1 <= clock["minutes_per_round"] <= 1440
                    or state.get("profiles_generated") is not True
                    or state.get("config_generated") is not True):
                raise ValueError
            agents = {}
            for index, item in enumerate(config["agent_configs"]):
                if (type(item) is not dict or item.get("agent_id") != index
                        or type(item.get("entity_uuid")) is not str
                        or item["entity_uuid"] not in grounding
                        or type(grounding[item["entity_uuid"]]) is not dict
                        or grounding[item["entity_uuid"]].get("source_entity_uuid")
                        != item["entity_uuid"]
                        or type(item.get("entity_name")) is not str
                        or type(item.get("entity_type")) is not str):
                    raise ValueError
                agents[index] = item
            if len(grounding) != len(agents):
                raise ValueError
            platforms = tuple(platform for platform in ("twitter", "reddit")
                              if state.get(f"enable_{platform}") is True)
            if (not platforms or any(
                    type(state.get(f"enable_{p}")) is not bool
                    or (type(config.get(f"{p}_config")) is dict)
                    != state[f"enable_{p}"] for p in ("twitter", "reddit"))):
                raise ValueError
            profiles = {}
            if "twitter" in platforms:
                path = root / "twitter_profiles.csv"
                if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
                    raise ValueError
                with path.open(newline="", encoding="utf-8") as stream:
                    rows = list(csv.DictReader(stream))
                if (len(rows) != len(agents) or any(
                        row.get("user_id") != str(index) or
                        row.get("name") != agents[index]["entity_name"]
                        for index, row in enumerate(rows))):
                    raise ValueError
                profiles["twitter"] = rows
            if "reddit" in platforms:
                rows = _bounded_json(root / "reddit_profiles.json")
                if type(rows) is not list or len(rows) != len(agents):
                    raise ValueError
                for index, row in enumerate(rows):
                    validate_reddit_profile(row, index,
                                            agents[index]["entity_name"])
                profiles["reddit"] = rows
            self._controls = parse_execution_controls(config, platforms, agents)
            from nexaweave_execution.native_seed_contracts import validate_native_seed_plan
            validate_native_seed_plan(config)
            return config, agents, profiles, platforms
        except (OSError, ValueError, TypeError, KeyError, UnicodeError,
                RecursionError, OverflowError,
                json.JSONDecodeError, NeutralCapabilityError):
            raise NeutralCapabilityError("invalid_request") from None

    def _native_output_exists(self):
        return any(os.path.lexists(self.simulation_dir / name) for name in (
            self._claim_name, "twitter_simulation.db", "reddit_simulation.db",
            "native_effective_controls.json"))

    def _claim_fresh_start(self):
        # This bridge is a one-shot runner. Keep the exclusive claim even after
        # failure: an uncertain native side effect must never be reset by retry.
        if self._native_output_exists():
            raise NeutralCapabilityError("invalid_request")
        try:
            descriptor = os.open(self.simulation_dir / self._claim_name,
                                 os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except OSError:
            raise NeutralCapabilityError("invalid_request") from None
        else:
            os.close(descriptor)
        if any(os.path.lexists(self.simulation_dir / name) for name in (
                "twitter_simulation.db", "reddit_simulation.db")):
            raise NeutralCapabilityError("invalid_request")

    def model_for(self, platform):
        return self._models[platform]

    def reddit_agent_graph(self, available_actions):
        if (self._reddit_graph is None
                or tuple(available_actions) != self._reddit_actions):
            raise NeutralCapabilityError("invalid_request")
        return self._reddit_graph

    def platform_for(self, platform, db_path, oasis):
        kwargs = (self._controls.platform_kwargs(platform)
                  if self._controls is not None else None)
        if kwargs is not None:
            from oasis.social_platform.platform import Platform
            return Platform(db_path=db_path, **kwargs)
        if platform == "reddit":
            return oasis.DefaultPlatformType.REDDIT
        # OASIS's default Twitter platform selects TWHIN weights. Its native
        # random recommender uses local Python random and keeps the real DB,
        # environment, action and interview implementations.
        from oasis.social_platform.platform import Platform
        return Platform(db_path=db_path, recsys_type="random",
                        refresh_rec_post_count=2, max_rec_post_len=2,
                        following_post_count=3, use_openai_embedding=False)

    def schedule_for(self, agent_config, simulated_minutes, current_hour):
        if self._controls is None:
            return (current_hour,
                    agent_config.get("active_hours", list(range(8, 23))),
                    agent_config.get("activity_level", 0.5))
        return self._controls.schedule(agent_config, simulated_minutes, current_hour)

    def initial_trace_cursor(self, env):
        # Initial follow/seed traces belong to initialization even when optional
        # controls are absent or empty; never replay them as autonomous actions.
        return env.platform.db.execute("SELECT COALESCE(MAX(rowid), 0) FROM trace").fetchone()[0]

    async def apply_initial_network(self, platform, env, action_logger, agent_names):
        if self._controls is None:
            return 0
        from oasis import ActionType
        attempts = self._control_network.setdefault(platform, [])
        # Registration is complete. Require the native signup mapping rather
        # than assuming nonexistent IDs can be passed to Platform.follow.
        rows = env.platform.db.execute("SELECT agent_id, user_id FROM user").fetchall()
        if (len(rows) != len(self._agents)
                or dict(rows) != {agent_id: agent_id for agent_id in self._agents}):
            raise NeutralCapabilityError("internal_error")
        for follower, followee in self._controls.edges(platform):
            attempt = {"follower_agent_id": follower, "followee_agent_id": followee,
                       "status": "uncertain"}
            attempts.append(attempt)
            agent = env.agent_graph.get_agent(follower)
            response = await agent.perform_action_by_data(
                ActionType.FOLLOW, followee_id=followee)
            if (type(response) is not dict or set(response) != {"success", "follow_id"}
                    or response["success"] is not True
                    or type(response["follow_id"]) is not int or response["follow_id"] < 1):
                raise NeutralCapabilityError("internal_error")
            attempt.update(status="applied", follow_id=response["follow_id"])
            agent.perform_agent_graph_action("follow", {"followee_id": followee})
            if action_logger:
                action_logger.log_action(
                    round_num=0, agent_id=follower,
                    agent_name=agent_names[follower], action_type="FOLLOW",
                    action_args={"followee_id": followee},
                    result=json.dumps(response), success=True)
        return len(attempts)

    def _write_controls_diagnostic(self, completed):
        if self._controls is None:
            return
        record = self._controls.diagnostic(self._platforms)
        for setting in record["agent_activity"]:
            agent_config = self._agents[setting["agent_id"]]
            if setting["active_hours"] is None:
                setting["active_hours"] = agent_config.get("active_hours", list(range(8, 23)))
            if setting["activity_probability"] is None:
                setting["activity_probability"] = agent_config.get("activity_level", 0.5)
        record.update(execution_status="completed" if completed else "failed",
                      initial_network=self._control_network)
        # Exclusive creation: an old diagnostic is never reset/replaced.
        with (self.simulation_dir / "native_effective_controls.json").open(
                "x", encoding="utf-8") as stream:
            json.dump(record, stream, allow_nan=False, sort_keys=True)

    def adopt(self, platform, result):
        self._results[platform] = result

    async def _start_async(self):
        from scripts import run_parallel_simulation as native
        previous_random_state = random.getstate()
        completed = False
        try:
            native._load_native_engine()
            logs = native.SimulationLogManager(str(self.simulation_dir))
            random.seed(self.seed)
            # Fail adapter construction before either native platform task
            # starts, under the same seed and RNG restoration as native rounds.
            if "reddit" in self._platforms:
                self._reddit_actions = tuple(native.REDDIT_ACTIONS)
                self._reddit_graph = build_reddit_agent_graph(
                    self._profiles["reddit"], self._models["reddit"],
                    list(self._reddit_actions))
            for platform in self._platforms:
                operation = (native.run_twitter_simulation if platform == "twitter"
                             else native.run_reddit_simulation)
                logger = (logs.get_twitter_logger() if platform == "twitter"
                          else logs.get_reddit_logger())
                result = await operation(self._config, str(self.simulation_dir),
                                         logger, logs, self.max_rounds,
                                         native_dependencies=self)
                if result.env is None or result.agent_graph is None:
                    raise NeutralCapabilityError("report_context_unavailable")
            completed = True
        finally:
            random.setstate(previous_random_state)
            self._write_controls_diagnostic(completed)

    def start(self):
        if self._closed or self._started:
            raise NeutralCapabilityError("invalid_request")
        self._claim_fresh_start()
        self._runner = asyncio.Runner()
        try:
            self._runner.run(asyncio.wait_for(self._start_async(), self.timeout_seconds))
            self._started = True
            return self
        except BaseException as error:
            try:
                self.close()
            except Exception:
                pass
            if isinstance(error, (KeyboardInterrupt, SystemExit,
                                  asyncio.CancelledError)):
                raise
            if isinstance(error, NeutralCapabilityError):
                raise NeutralCapabilityError(error.code) from None
            raise NeutralCapabilityError("internal_error") from None

    def _trace_id(self, platform, agent_id):
        path = self.simulation_dir / f"{platform}_simulation.db"
        with sqlite3.connect(path) as connection:
            row = connection.execute(
                "SELECT MAX(rowid) FROM trace WHERE action = ? AND user_id = ?",
                ("interview", agent_id)).fetchone()
        return row[0] or 0

    async def _interview_async(self, platform, agent_id, prompt):
        from scripts import run_parallel_simulation as native
        handler = native.ParallelIPCHandler(
            str(self.simulation_dir),
            twitter_env=self._results.get("twitter", None).env if "twitter" in self._results else None,
            twitter_agent_graph=(self._results["twitter"].agent_graph
                                 if "twitter" in self._results else None),
            reddit_env=self._results.get("reddit", None).env if "reddit" in self._results else None,
            reddit_agent_graph=(self._results["reddit"].agent_graph
                                if "reddit" in self._results else None),
        )
        before = self._trace_id(platform, agent_id)
        result = await handler._interview_single_platform(agent_id, prompt, platform)
        after = self._trace_id(platform, agent_id)
        if (type(result) is not dict or "error" in result or after <= before
                or result.get("agent_id") != agent_id
                or result.get("platform") != platform
                or type(result.get("response")) is not str
                or not result["response"].strip()):
            raise NeutralCapabilityError("interview_unavailable")
        return result

    def interview(self, *, platform, agent_id, prompt):
        if (not self._started or self._closed or platform not in self._platforms
                or type(agent_id) is not int or agent_id not in self._agents
                or type(prompt) is not str or not 1 <= len(prompt.strip()) <= 400):
            raise NeutralCapabilityError("invalid_request")
        try:
            return self._runner.run(asyncio.wait_for(
                self._interview_async(platform, agent_id, prompt),
                self.timeout_seconds))
        except NeutralCapabilityError:
            raise
        except Exception:
            raise NeutralCapabilityError("interview_unavailable") from None

    def batch_interview(self, *, platform, agent_ids, prompt):
        if (type(agent_ids) is not list or not 1 <= len(agent_ids) <= 10
                or any(type(agent_id) is not int for agent_id in agent_ids)
                or len(set(agent_ids)) != len(agent_ids)):
            raise NeutralCapabilityError("invalid_request")
        if (not self._started or self._closed or platform not in self._platforms
                or any(agent_id not in self._agents for agent_id in agent_ids)
                or type(prompt) is not str or not 1 <= len(prompt.strip()) <= 4096):
            raise NeutralCapabilityError("invalid_request")
        try:
            return self._runner.run(asyncio.wait_for(
                self._batch_interview_async(platform, agent_ids, prompt),
                self.timeout_seconds))
        except NeutralCapabilityError:
            raise NeutralCapabilityError("interview_unavailable") from None
        except Exception:
            raise NeutralCapabilityError("interview_unavailable") from None

    async def _batch_interview_async(self, platform, agent_ids, prompt):
        from scripts import run_parallel_simulation as native
        handler = native.ParallelIPCHandler(
            str(self.simulation_dir),
            twitter_env=self._results["twitter"].env if "twitter" in self._results else None,
            twitter_agent_graph=(self._results["twitter"].agent_graph
                                 if "twitter" in self._results else None),
            reddit_env=self._results["reddit"].env if "reddit" in self._results else None,
            reddit_agent_graph=(self._results["reddit"].agent_graph
                                if "reddit" in self._results else None),
        )
        before = {agent_id: self._trace_id(platform, agent_id) for agent_id in agent_ids}
        command_id = str(uuid.uuid4())
        response_path = self.simulation_dir / "ipc_responses" / f"{command_id}.json"
        try:
            completed = await handler.handle_batch_interview(
                command_id, [{"agent_id": agent_id, "prompt": prompt,
                              "platform": platform} for agent_id in agent_ids],
                platform=platform)
            response = _bounded_json(response_path)
            if (completed is not True or type(response) is not dict
                    or response.get("command_id") != command_id
                    or response.get("status") != "completed"
                    or type(response.get("result")) is not dict
                    or response["result"].get("interviews_count") != len(agent_ids)
                    or type(response["result"].get("results")) is not dict):
                raise NeutralCapabilityError("interview_unavailable")
            native_results = response["result"]["results"]
            if set(native_results) != {f"{platform}_{agent_id}" for agent_id in agent_ids}:
                raise NeutralCapabilityError("interview_unavailable")
            output = []
            for agent_id in agent_ids:
                result = native_results.get(f"{platform}_{agent_id}")
                if (type(result) is not dict
                        or result.get("platform") != platform
                        or result.get("agent_id") != agent_id
                        or type(result.get("response")) is not str
                        or not result["response"].strip()
                        or self._trace_id(platform, agent_id) <= before[agent_id]):
                    raise NeutralCapabilityError("interview_unavailable")
                output.append(result)
            return output
        finally:
            response_path.unlink(missing_ok=True)

    def report_interview_capability(self, *, graph_id, simulation_id,
                                    platform, agent_ids):
        """Bind a report tool to an exact prepared platform and agent cohort."""
        if graph_id != self.graph_id:
            raise NeutralCapabilityError("graph_mismatch")
        if simulation_id != self.simulation_id:
            raise NeutralCapabilityError("simulation_mismatch")
        if (not self._started or self._closed
                or platform not in self._platforms or type(agent_ids) is not list
                or not agent_ids or len(agent_ids) > 10
                or any(type(agent_id) is not int or agent_id not in self._agents
                       for agent_id in agent_ids)
                or len(set(agent_ids)) != len(agent_ids)):
            raise NeutralCapabilityError("invalid_request")
        bound_ids = tuple(agent_ids)

        def capability(*, simulation_id, interview_requirement,
                       simulation_requirement="", max_agents=5,
                       custom_questions=None):
            if simulation_id != self.simulation_id or not self._started or self._closed:
                raise NeutralCapabilityError("simulation_mismatch")
            if (type(interview_requirement) is not str
                    or not 1 <= len(interview_requirement.strip()) <= 400
                    or type(simulation_requirement) is not str
                    or len(simulation_requirement) > 4000
                    or type(max_agents) is not int or not 1 <= max_agents <= 10
                    or (custom_questions is not None and (
                        type(custom_questions) is not list
                        or len(custom_questions) > 10
                        or any(type(item) is not str
                               or not 1 <= len(item.strip()) <= 400
                               for item in custom_questions)))):
                raise NeutralCapabilityError("invalid_request")
            questions = list(custom_questions) if custom_questions else [interview_requirement]
            question = (questions[0] if len(questions) == 1 else
                        "\n".join(f"{index}. {item}" for index, item in
                                  enumerate(questions, start=1)))
            if len(question) > 4096:
                raise NeutralCapabilityError("invalid_request")
            ids = list(bound_ids[:max_agents])
            native_results = self.batch_interview(platform=platform,
                                                  agent_ids=ids, prompt=question)
            interviews = []
            selected = []
            for agent_id, native_result in zip(ids, native_results):
                config = self._agents[agent_id]
                profile = self._profiles[platform][agent_id]
                selected.append({"agent_id": agent_id, "platform": platform,
                                 "entity_uuid": config["entity_uuid"]})
                interviews.append(AgentInterview(
                    agent_name=config["entity_name"],
                    agent_role=config["entity_type"],
                    agent_bio=profile.get("bio", profile.get("description", "")),
                    question=question, response=native_result["response"]))
            return InterviewResult(
                interview_topic=interview_requirement,
                interview_questions=questions, selected_agents=selected,
                interviews=interviews, total_agents=len(bound_ids),
                interviewed_count=len(interviews))
        return capability

    async def _close_async(self):
        failed = False
        for platform in reversed(self._platforms):
            result = self._results.get(platform)
            if result is not None and result.env is not None:
                try:
                    if hasattr(result.env, "platform_task"):
                        await asyncio.wait_for(result.env.close(), self.timeout_seconds)
                    else:
                        result.env.platform.db_cursor.close()
                        result.env.platform.db.close()
                except Exception:
                    failed = True
                    try:
                        result.env.platform.db_cursor.close()
                        result.env.platform.db.close()
                    except Exception:
                        pass
        self._results.clear()
        self._reddit_graph = None
        self._reddit_actions = None
        if failed:
            raise NeutralCapabilityError("internal_error")

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            if self._runner is not None:
                self._runner.run(self._close_async())
        except Exception:
            raise NeutralCapabilityError("internal_error") from None
        finally:
            if self._runner is not None:
                self._runner.close()

    def __enter__(self):
        return self.start()

    def __exit__(self, _type, _value, _traceback):
        self.close()
