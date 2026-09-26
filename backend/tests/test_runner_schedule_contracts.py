"""Execute only the inherited scheduler function extracted from runner source.

Full runner import mutates builtins.open on Windows and loads OASIS/CAMEL. This
is a function-boundary fixture, not an engine or module-import test.
"""

import ast
from pathlib import Path
from types import SimpleNamespace


SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "run_parallel_simulation.py"


class ControlledRandom:
    def __init__(self, draws, uniform_value):
        self.draws = iter(draws)
        self.uniform_value = uniform_value
        self.sample_calls = []

    def uniform(self, lower, upper):
        assert lower <= self.uniform_value <= upper
        return self.uniform_value

    def random(self):
        return next(self.draws)

    def sample(self, candidates, count):
        self.sample_calls.append((list(candidates), count))
        return list(candidates[:count])


def _actual_scheduler(random_draws):
    module = ast.parse(SOURCE.read_text(encoding="utf-8"), filename=str(SOURCE))
    definitions = [node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "get_active_agents_for_round"]
    assert len(definitions) == 1
    extracted = ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), definitions[0]],
        type_ignores=[],
    )
    namespace = {"random": random_draws}
    exec(compile(ast.fix_missing_locations(extracted), str(SOURCE), "exec"), namespace)
    return namespace["get_active_agents_for_round"]


class FakeGraph:
    def __init__(self, missing=()):
        self.missing = set(missing)

    def get_agent(self, agent_id):
        if agent_id in self.missing:
            raise KeyError(agent_id)
        return SimpleNamespace(user_id=agent_id)


def _config(*, base=2):
    return {
        "time_config": {
            "agents_per_hour_min": base,
            "agents_per_hour_max": base,
            "peak_hours": [20],
            "peak_activity_multiplier": 2.0,
            "off_peak_hours": [2],
            "off_peak_activity_multiplier": 0.5,
        },
        "agent_configs": [
            {"agent_id": 0, "active_hours": [2, 10, 20], "activity_level": 1.0},
            {"agent_id": 1, "active_hours": [2, 10, 20], "activity_level": 0.5},
            {"agent_id": 2, "active_hours": [2, 10, 20], "activity_level": 1.0},
            {"agent_id": 3, "active_hours": [2, 10, 20], "activity_level": 1.0},
            {"agent_id": 4, "active_hours": [10], "activity_level": 1.0},
        ],
    }


def test_active_hours_probability_and_missing_agent():
    controlled = ControlledRandom(draws=[0.0, 0.9, 0.0, 0.0, 0.0], uniform_value=2)
    scheduler = _actual_scheduler(controlled)
    env = SimpleNamespace(agent_graph=FakeGraph(missing={2}))
    active = scheduler(env, _config(), current_hour=10, round_num=1)
    assert controlled.sample_calls == [([0, 2, 3, 4], 2)]
    assert [agent_id for agent_id, _ in active] == [0]  # id 2 is missing in graph
    assert active[0][1].user_id == 0


def test_peak_and_off_peak_change_selected_count():
    peak_random = ControlledRandom(draws=[0.0] * 4, uniform_value=2)
    off_peak_random = ControlledRandom(draws=[0.0] * 4, uniform_value=2)
    peak = _actual_scheduler(peak_random)(SimpleNamespace(agent_graph=FakeGraph()), _config(), current_hour=20, round_num=1)
    off_peak = _actual_scheduler(off_peak_random)(SimpleNamespace(agent_graph=FakeGraph()), _config(), current_hour=2, round_num=2)
    assert [agent_id for agent_id, _ in peak] == [0, 1, 2, 3]
    assert [agent_id for agent_id, _ in off_peak] == [0]
    assert peak_random.sample_calls[0][1] == 4
    assert off_peak_random.sample_calls[0][1] == 1
