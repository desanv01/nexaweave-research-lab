"""Exercise the inherited JSONL writers without starting simulation engines."""

import json

from scripts.action_logger import ActionLogger, PlatformActionLogger


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_platform_jsonl_separation_round_shape_and_non_ascii(tmp_path):
    twitter = PlatformActionLogger("twitter", str(tmp_path))
    reddit = PlatformActionLogger("reddit", str(tmp_path))
    config = {"time_config": {"total_simulation_hours": 3, "minutes_per_round": 60}, "agent_configs": [{"agent_id": 0}, {"agent_id": 1}]}
    twitter.log_simulation_start(config)
    twitter.log_round_start(1, 9)
    twitter.log_action(1, 0, "米拉", "CREATE_POST", {"content": "你好，世界"}, result="post:1", success=True)
    twitter.log_action(1, 1, "Harbor Labs", "DO_NOTHING", {}, result="abstained", success=True)
    twitter.log_round_end(1, 2)
    twitter.log_simulation_end(total_rounds=1, total_actions=2)
    reddit.log_simulation_start(config)
    reddit.log_round_start(1, 9)
    reddit.log_action(1, 1, "Harbor Labs", "LIKE_POST", {"post_id": 404}, result="missing post", success=False)
    reddit.log_round_end(1, 1)
    reddit.log_simulation_end(total_rounds=1, total_actions=1)

    tw = _read_jsonl(tmp_path / "twitter" / "actions.jsonl")
    rd = _read_jsonl(tmp_path / "reddit" / "actions.jsonl")
    assert [entry.get("event_type", entry.get("action_type")) for entry in tw] == ["simulation_start", "round_start", "CREATE_POST", "DO_NOTHING", "round_end", "simulation_end"]
    assert [entry.get("event_type", entry.get("action_type")) for entry in rd] == ["simulation_start", "round_start", "LIKE_POST", "round_end", "simulation_end"]
    assert tw[0]["platform"] == "twitter" and rd[0]["platform"] == "reddit"
    assert tw[0]["agents_count"] == rd[0]["agents_count"] == 2
    # Inherited logger hardcodes two rounds per simulated hour, regardless of
    # minutes_per_round; U01b records this behavior rather than changing it.
    assert tw[0]["total_rounds"] == 6
    assert tw[1]["round"] == 1 and tw[1]["simulated_hour"] == 9
    assert tw[2]["agent_name"] == "米拉" and tw[2]["action_args"] == {"content": "你好，世界"}
    assert tw[2]["result"] == "post:1" and tw[2]["success"] is True
    assert tw[3]["action_type"] == "DO_NOTHING" and tw[3]["success"] is True
    assert rd[2]["action_type"] == "LIKE_POST" and rd[2]["success"] is False
    assert rd[2]["action_args"] == {"post_id": 404} and rd[2]["result"] == "missing post"
    assert tw[4]["actions_count"] == 2 and rd[3]["actions_count"] == 1
    assert tw[-1]["total_actions"] == 2 and rd[-1]["total_actions"] == 1
    assert all("timestamp" in entry for entry in tw + rd)
    assert "你好，世界" in (tmp_path / "twitter" / "actions.jsonl").read_text(encoding="utf-8")
    assert "米拉" not in (tmp_path / "reddit" / "actions.jsonl").read_text(encoding="utf-8")


def test_legacy_logger_writes_platform_field_per_record(tmp_path):
    path = tmp_path / "legacy.jsonl"
    logger = ActionLogger(str(path))
    logger.log_round_start(2, 20, "twitter")
    logger.log_action(2, "reddit", 0, "米拉", "DO_NOTHING", result="abstained")
    logger.log_round_end(2, 1, "reddit")
    entries = _read_jsonl(path)
    assert [entry["platform"] for entry in entries] == ["twitter", "reddit", "reddit"]
    assert entries[1]["success"] is True and entries[1]["result"] == "abstained"
