"""Source-level action catalog and executable profile/config boundaries.

These fixtures do not run OASIS actions or construct provider clients.
"""

import ast
import csv
import json
from pathlib import Path

from app.services.oasis_profile_generator import OasisAgentProfile, OasisProfileGenerator
from app.services.simulation_config_generator import (
    AgentActivityConfig,
    PlatformConfig,
    SimulationParameters,
    TimeSimulationConfig,
)
from app.services.simulation_ipc import CommandType


RUNNER_SOURCE = Path(__file__).resolve().parents[1] / "scripts" / "run_parallel_simulation.py"


def _configured_action_names(name: str) -> list[str]:
    # Importing the whole runner changes global builtins on Windows and loads
    # CAMEL/OASIS. Read only its actual application-configured list assignment.
    module = ast.parse(RUNNER_SOURCE.read_text(encoding="utf-8"))
    assignments = [node for node in module.body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == name for target in node.targets)]
    assert len(assignments) == 1
    values = assignments[0].value
    assert isinstance(values, ast.List)
    assert all(isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name) and value.value.id == "ActionType" for value in values.elts)
    return [value.attr for value in values.elts]


def test_application_action_catalogs_and_manual_interview_command():
    assert _configured_action_names("TWITTER_ACTIONS") == [
        "CREATE_POST", "LIKE_POST", "REPOST", "FOLLOW", "DO_NOTHING", "QUOTE_POST",
    ]
    assert _configured_action_names("REDDIT_ACTIONS") == [
        "LIKE_POST", "DISLIKE_POST", "CREATE_POST", "CREATE_COMMENT",
        "LIKE_COMMENT", "DISLIKE_COMMENT", "SEARCH_POSTS", "SEARCH_USER",
        "TREND", "REFRESH", "DO_NOTHING", "FOLLOW", "MUTE",
    ]
    assert "INTERVIEW" not in _configured_action_names("TWITTER_ACTIONS")
    assert "INTERVIEW" not in _configured_action_names("REDDIT_ACTIONS")
    assert CommandType.INTERVIEW.value == "interview"
    assert CommandType.BATCH_INTERVIEW.value == "batch_interview"


def _profiles():
    return [
        OasisAgentProfile(user_id=0, user_name="mira", name="Mira Vale", bio="研究员", persona="Interested in policy", source_entity_uuid="synthetic-person", source_entity_type="Person"),
        OasisAgentProfile(user_id=1, user_name="harbor", name="Harbor Labs", bio="研究机构", persona="Publishes reports", source_entity_uuid="synthetic-org", source_entity_type="Organization"),
    ]


def test_actual_platform_serializers_keep_sequential_actor_mapping(tmp_path):
    generator = object.__new__(OasisProfileGenerator)
    profiles = _profiles()
    twitter_path = tmp_path / "twitter.csv"
    reddit_path = tmp_path / "reddit.json"
    generator.save_profiles(profiles, str(twitter_path), "twitter")
    generator.save_profiles(profiles, str(reddit_path), "reddit")
    with twitter_path.open(newline="", encoding="utf-8") as handle:
        twitter = list(csv.DictReader(handle))
    reddit = json.loads(reddit_path.read_text(encoding="utf-8"))
    assert [row["user_id"] for row in twitter] == ["0", "1"]
    assert [row["user_id"] for row in reddit] == [0, 1]
    assert [row["name"] for row in twitter] == [row["name"] for row in reddit] == ["Mira Vale", "Harbor Labs"]
    assert [row["username"] for row in twitter] == [row["username"] for row in reddit] == ["mira", "harbor"]
    assert [row["description"] for row in twitter] == ["研究员", "研究机构"]
    assert [row["bio"] for row in reddit] == ["研究员", "研究机构"]
    assert all("user_char" in row for row in twitter)
    assert all("persona" in row for row in reddit)


def test_missing_and_invalid_platform_currently_select_reddit(tmp_path):
    generator = object.__new__(OasisProfileGenerator)
    for platform in (None, "unknown"):
        path = tmp_path / f"profiles-{platform}.json"
        if platform is None:
            generator.save_profiles(_profiles(), str(path))
        else:
            generator.save_profiles(_profiles(), str(path), platform)
        assert [row["user_id"] for row in json.loads(path.read_text(encoding="utf-8"))] == [0, 1]
    # The inherited Twitter serializer uses row order instead of profile.user_id.
    # This is a documented limitation, not a desired new contract.
    nonsequential = [OasisAgentProfile(user_id=42, user_name="mira", name="Mira Vale", bio="bio", persona="persona")]
    path = tmp_path / "renumbered.csv"
    generator.save_profiles(nonsequential, str(path), "twitter")
    with path.open(newline="", encoding="utf-8") as handle:
        assert next(csv.DictReader(handle))["user_id"] == "0"


def test_configuration_dataclasses_preserve_platform_and_actor_ids():
    parameters = SimulationParameters(
        simulation_id="fixture-sim", project_id="fixture-project", graph_id="fixture-graph",
        simulation_requirement="Synthetic research question",
        time_config=TimeSimulationConfig(total_simulation_hours=3, minutes_per_round=60),
        agent_configs=[
            AgentActivityConfig(agent_id=0, entity_uuid="synthetic-person", entity_name="Mira Vale", entity_type="Person", active_hours=[9, 10]),
            AgentActivityConfig(agent_id=1, entity_uuid="synthetic-org", entity_name="Harbor Labs", entity_type="Organization", active_hours=[10]),
        ],
        twitter_config=PlatformConfig(platform="twitter"),
        reddit_config=PlatformConfig(platform="reddit"),
    )
    data = json.loads(parameters.to_json())
    assert [agent["agent_id"] for agent in data["agent_configs"]] == [0, 1]
    assert [agent["entity_type"] for agent in data["agent_configs"]] == ["Person", "Organization"]
    assert data["twitter_config"]["platform"] == "twitter"
    assert data["reddit_config"]["platform"] == "reddit"
    assert data["time_config"]["minutes_per_round"] == 60
