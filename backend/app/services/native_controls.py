"""Pure, bounded prepared-native controls; no engine or provider imports."""

from dataclasses import dataclass
import math


_PRESETS = {
    "twitter": {
        "balanced_random": ("random", 2, 2, 3),
        "following_only": ("random", 0, 2, 10),
    },
    "reddit": {
        "compact_popularity": ("reddit", 1, 2, 3),
        "broad_popularity": ("reddit", 5, 20, 3),
    },
}


def _fields(value, allowed, required=()):
    if (type(value) is not dict or set(value) - set(allowed)
            or not set(required) <= set(value)):
        raise ValueError("invalid native controls")


@dataclass(frozen=True)
class NativeControls:
    # Tuple storage keeps caller-owned dictionaries/lists out of runtime state.
    presets: tuple
    follows: tuple
    activity: tuple

    def platform_kwargs(self, platform):
        name = dict(self.presets).get(platform)
        if name is None:
            return None
        recsys, refresh, maximum, following = _PRESETS[platform][name]
        result = dict(recsys_type=recsys, refresh_rec_post_count=refresh,
                      max_rec_post_len=maximum, following_post_count=following,
                      use_openai_embedding=False)
        if platform == "reddit":
            result.update(show_score=True, allow_self_rating=True)
        return result

    def edges(self, platform):
        return dict(self.follows).get(platform, ())

    def schedule(self, agent_config, simulated_minutes, current_hour):
        setting = next((item for item in self.activity
                        if item[0] == agent_config["agent_id"]), None)
        hours = agent_config.get("active_hours", list(range(8, 23)))
        probability = agent_config.get("activity_level", 0.5)
        if setting is None:
            return current_hour, hours, probability
        _, offset, supplied_hours, supplied_probability = setting
        local_hour = ((simulated_minutes + offset) // 60) % 24
        return (local_hour, supplied_hours if supplied_hours is not None else hours,
                supplied_probability if supplied_probability is not None else probability)

    def diagnostic(self, platforms):
        defaults = {
            "twitter": dict(recsys_type="random", refresh_rec_post_count=2,
                            max_rec_post_len=2, following_post_count=3,
                            use_openai_embedding=False),
            "reddit": dict(recsys_type="reddit", refresh_rec_post_count=5,
                           max_rec_post_len=100, following_post_count=3,
                           use_openai_embedding=False, show_score=True,
                           allow_self_rating=True),
        }
        return {
            "schema_version": 1,
            "recommendations": {platform: {
                "preset": dict(self.presets).get(platform),
                "resolved": self.platform_kwargs(platform) or defaults[platform],
                "inherited_default": self.platform_kwargs(platform) is None,
            } for platform in platforms},
            "agent_activity": [{"agent_id": agent_id,
                                "timezone_offset_minutes": offset,
                                "active_hours": list(hours) if hours is not None else None,
                                "activity_probability": probability}
                               for agent_id, offset, hours, probability in self.activity],
        }


def parse_execution_controls(config, platforms, agent_ids):
    """Validate before native claim/effects. Missing is different from null."""
    if "execution_controls" not in config:
        return None
    value = config["execution_controls"]
    _fields(value, ("schema_version", "recommendation_presets", "initial_follows",
                    "agent_activity"), ("schema_version",))
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("invalid native controls version")
    ids = set(agent_ids)
    if not 1 <= len(ids) <= 500 or any(type(item) is not int for item in ids):
        raise ValueError("invalid native agents")
    presets = value.get("recommendation_presets", {})
    follows = value.get("initial_follows", {})
    _fields(presets, platforms)
    _fields(follows, platforms)
    resolved_presets = []
    resolved_edges = []
    for platform in platforms:
        if platform in presets:
            name = presets[platform]
            if type(name) is not str or name not in _PRESETS[platform]:
                raise ValueError("invalid native preset")
            resolved_presets.append((platform, name))
        edges = follows.get(platform, [])
        if type(edges) is not list or len(edges) > 1000:
            raise ValueError("invalid initial follows")
        seen = set()
        for edge in edges:
            _fields(edge, ("follower_agent_id", "followee_agent_id"),
                    ("follower_agent_id", "followee_agent_id"))
            pair = edge["follower_agent_id"], edge["followee_agent_id"]
            if (any(type(item) is not int or item not in ids for item in pair)
                    or pair[0] == pair[1] or pair in seen):
                raise ValueError("invalid initial follow edge")
            seen.add(pair)
        resolved_edges.append((platform, tuple(sorted(seen))))
    activity = value.get("agent_activity", [])
    if type(activity) is not list or len(activity) > len(ids):
        raise ValueError("invalid agent activity")
    resolved_activity = []
    seen = set()
    inherited = {item["agent_id"]: item for item in config.get("agent_configs", [])}
    for setting in activity:
        _fields(setting, ("agent_id", "timezone_offset_minutes", "active_hours",
                          "activity_probability"), ("agent_id",))
        agent_id = setting["agent_id"]
        offset = setting.get("timezone_offset_minutes", 0)
        if (type(agent_id) is not int or agent_id not in ids or agent_id in seen
                or type(offset) is not int or not -720 <= offset <= 840):
            raise ValueError("invalid agent activity identity/offset")
        seen.add(agent_id)
        inherited_agent = inherited.get(agent_id, {})
        hours = setting.get("active_hours", inherited_agent.get("active_hours", list(range(8, 23))))
        if (type(hours) is not list or len(hours) > 24
                or any(type(hour) is not int or not 0 <= hour <= 23 for hour in hours)
                or len(set(hours)) != len(hours)):
            raise ValueError("invalid local hours")
        hours = tuple(sorted(hours))
        probability = setting.get("activity_probability", inherited_agent.get("activity_level", 0.5))
        if (type(probability) not in (int, float) or not 0 <= probability <= 1
                or not math.isfinite(probability)):
            raise ValueError("invalid activity probability")
        resolved_activity.append((agent_id, offset, hours, probability))
    return NativeControls(tuple(resolved_presets), tuple(resolved_edges),
                          tuple(sorted(resolved_activity)))
