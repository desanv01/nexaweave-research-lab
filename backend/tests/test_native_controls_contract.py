"""Pure contracts and inherited scheduler behavior; authored, not worker-run."""

import copy
import random
from types import SimpleNamespace

import pytest

from app.services.native_controls import parse_execution_controls
from scripts.run_parallel_simulation import get_active_agents_for_round


def _parse(value):
    return parse_execution_controls({"execution_controls": value},
                                    ("twitter", "reddit"), (0, 1))


def test_missing_controls_is_exact_inherited_path():
    assert parse_execution_controls({}, ("twitter", "reddit"), (0, 1)) is None
    controls = _parse({"schema_version": 1})
    assert controls.platform_kwargs("reddit") is None
    assert controls.platform_kwargs("twitter") is None
    assert controls.schedule({"agent_id": 0, "activity_level": 0,
                              "active_hours": [4]}, 999, 4) == (4, [4], 0)


@pytest.mark.parametrize("value", [
    None, [], {}, {"schema_version": True}, {"schema_version": 2},
    {"schema_version": 1, "recsys_type": "twhin-bert"},
    {"schema_version": 1, "recommendation_presets": {"other": "balanced_random"}},
    {"schema_version": 1, "recommendation_presets": {"twitter": "compact_popularity"}},
    {"schema_version": 1, "recommendation_presets": {"twitter": {"url": "https://x"}}},
    {"schema_version": 1, "initial_follows": {"twitter": [[0, 1]]}},
    {"schema_version": 1, "initial_follows": {"twitter": [
        {"follower_agent_id": 0, "followee_agent_id": 0}]}},
    {"schema_version": 1, "initial_follows": {"twitter": [
        {"follower_agent_id": True, "followee_agent_id": 0}]}},
    {"schema_version": 1, "initial_follows": {"reddit": [
        {"follower_agent_id": 0, "followee_agent_id": 2}]}},
    {"schema_version": 1, "initial_follows": {"reddit": [
        {"follower_agent_id": 0, "followee_agent_id": 1}] * 2}},
    {"schema_version": 1, "initial_follows": {"twitter": [
        {"follower_agent_id": 0, "followee_agent_id": 1}] * 1001}},
    {"schema_version": 1, "agent_activity": [{"agent_id": True}]},
    {"schema_version": 1, "agent_activity": [{"agent_id": 2}]},
    {"schema_version": 1, "agent_activity": [{"agent_id": 0}] * 2},
    {"schema_version": 1, "agent_activity": [{"agent_id": 0, "active_hours": [1, 1]}]},
    {"schema_version": 1, "agent_activity": [{"agent_id": 0, "active_hours": [True]}]},
    {"schema_version": 1, "agent_activity": [{"agent_id": 0, "active_hours": [24]}]},
    {"schema_version": 1, "agent_activity": [{"agent_id": 0, "active_hours": None}]},
    {"schema_version": 1, "agent_activity": [{"agent_id": 0, "timezone_offset_minutes": 841}]},
    {"schema_version": 1, "agent_activity": [{"agent_id": 0, "timezone_offset_minutes": -721}]},
    {"schema_version": 1, "agent_activity": [{"agent_id": 0, "timezone_offset_minutes": 30.0}]},
    *[{"schema_version": 1, "agent_activity": [
        {"agent_id": 0, "activity_probability": probability}]}
      for probability in (True, -0.1, 1.1, float("nan"), float("inf"), None)],
])
def test_strict_validation(value):
    with pytest.raises(ValueError):
        _parse(value)


def test_disabled_platform_and_agent_bound():
    with pytest.raises(ValueError):
        parse_execution_controls({"execution_controls": {
            "schema_version": 1, "initial_follows": {"reddit": []}}},
            ("twitter",), (0,))
    with pytest.raises(ValueError):
        parse_execution_controls({"execution_controls": {"schema_version": 1}},
                                 ("twitter",), range(501))


def test_controls_freeze_input_and_order_network():
    value = {"schema_version": 1, "initial_follows": {"twitter": [
        {"follower_agent_id": 1, "followee_agent_id": 0},
        {"follower_agent_id": 0, "followee_agent_id": 1}]},
        "agent_activity": [{"agent_id": 0, "active_hours": [23, 0],
                            "activity_probability": 0}]}
    original = copy.deepcopy(value)
    controls = _parse(value)
    assert value == original
    value["agent_activity"][0]["active_hours"].append(1)
    assert controls.edges("twitter") == ((0, 1), (1, 0))
    assert controls.schedule({"agent_id": 0}, 0, 0) == (0, (0, 23), 0)


@pytest.mark.parametrize("minutes,offset,hour", [
    (0, -30, 23), (29, 30, 0), (30, 30, 1), (1439, 30, 0),
    (1440, -720, 12), (0, 840, 14),
])
def test_full_minutes_local_hour(minutes, offset, hour):
    controls = _parse({"schema_version": 1, "agent_activity": [
        {"agent_id": 0, "timezone_offset_minutes": offset}]})
    assert controls.schedule({"agent_id": 0}, minutes, minutes // 60 % 24)[0] == hour


def test_opt_in_scheduler_zero_one_and_inherited_rng_parity():
    config = {"time_config": {"agents_per_hour_min": 2, "agents_per_hour_max": 2,
                              "off_peak_activity_multiplier": 1},
              "agent_configs": [{"agent_id": i, "active_hours": [0],
                                 "activity_level": 1} for i in range(2)]}
    env = SimpleNamespace(agent_graph=SimpleNamespace(get_agent=lambda i: i))
    controls = parse_execution_controls(
        {**config, "execution_controls": {"schema_version": 1, "agent_activity": [
            {"agent_id": 0, "activity_probability": 0},
            {"agent_id": 1, "activity_probability": 1}]}},
        ("twitter", "reddit"), (0, 1))
    assert controls.schedule(config["agent_configs"][0], 0, 0) == (0, (0,), 0)
    assert controls.schedule(config["agent_configs"][1], 0, 0) == (0, (0,), 1)
    inherited_controls = parse_execution_controls(
        {**config, "execution_controls": {"schema_version": 1, "agent_activity": [
            {"agent_id": 0}, {"agent_id": 1}]}},
        ("twitter", "reddit"), (0, 1))
    for agent_config in config["agent_configs"]:
        assert inherited_controls.schedule(agent_config, 0, 0) == (0, (0,), 1)
    assert parse_execution_controls(config, ("twitter", "reddit"), (0, 1)) is None
    adapter = SimpleNamespace(schedule_for=controls.schedule)
    previous = random.getstate()
    try:
        random.seed(12)
        inherited = get_active_agents_for_round(env, config, 0, 0)
        inherited_state = random.getstate()
        random.seed(12)
        defaults = parse_execution_controls(
            {**config, "execution_controls": {"schema_version": 1}},
            ("twitter", "reddit"), (0, 1))
        assert get_active_agents_for_round(
            env, config, 0, 0, simulated_minutes=0,
            native_dependencies=SimpleNamespace(schedule_for=defaults.schedule)) == inherited
        assert random.getstate() == inherited_state
        random.seed(12)
        assert get_active_agents_for_round(
            env, config, 0, 0, simulated_minutes=0,
            native_dependencies=SimpleNamespace(
                schedule_for=inherited_controls.schedule)) == inherited
        assert random.getstate() == inherited_state
        random.seed(12)
        assert get_active_agents_for_round(env, config, 0, 0, simulated_minutes=0,
                                          native_dependencies=adapter) == [(1, 1)]
    finally:
        random.setstate(previous)
