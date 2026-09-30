"""Trusted partial-profile loader using real OASIS Reddit agent components.

Adapted from camel-oasis 0.2.5, oasis/social_agent/agents_generator.py
(`generate_reddit_agent_graph`) and oasis/social_platform/config/user.py.
Copyright 2023 CAMEL-AI.org. Licensed under Apache-2.0; see the inherited
project notices. This adapter changes only the handling of absent optional
profile attributes. It does not replace native agents, tools, or environments.
"""

from __future__ import annotations


OPTIONAL_FIELDS = ("gender", "age", "mbti", "country")
_LABELS = {"gender": "Gender", "age": "Age", "mbti": "MBTI", "country": "Country"}


def _valid_text(value, maximum, *, allow_lines=False):
    return (type(value) is str and bool(value.strip()) and len(value) <= maximum
            and not any((ord(char) < 32 and
                         (not allow_lines or char not in "\r\n\t"))
                        or ord(char) == 127 for char in value))


def validate_reddit_profile(row, index, expected_name):
    """Reject malformed supplied values; missing/null optionals mean unknown."""
    if (type(row) is not dict or type(row.get("user_id")) is not int
            or row["user_id"] != index or row.get("name") != expected_name
            or not _valid_text(row.get("name"), 256)
            or not _valid_text(row.get("username"), 128)
            or not _valid_text(row.get("bio"), 2000, allow_lines=True)
            or not _valid_text(row.get("persona"), 10000, allow_lines=True)):
        raise ValueError("invalid native Reddit profile")
    for field in OPTIONAL_FIELDS:
        value = row.get(field)
        if value is None:
            continue
        if field == "age":
            if type(value) is not int or not 1 <= value <= 120:
                raise ValueError("invalid native Reddit age")
        elif not _valid_text(value, {"gender": 64, "mbti": 32,
                                     "country": 128}[field]):
            raise ValueError("invalid native Reddit attribute")


def build_reddit_agent_graph(rows, model, available_actions):
    """Construct the inherited graph and SocialAgent objects without file edits."""
    if type(rows) is not list or not rows:
        raise ValueError("invalid native Reddit cohort")
    for index, row in enumerate(rows):
        validate_reddit_profile(row, index,
                                row.get("name") if type(row) is dict else None)
    # Keep all native imports behind actual simulation execution.
    from oasis.social_agent.agent import SocialAgent
    from oasis.social_agent.agent_graph import AgentGraph
    from oasis.social_platform.config.user import UserInfo

    class HonestRedditUserInfo(UserInfo):
        def to_reddit_system_message(self):
            details = self.profile["other_info"]
            lines = [f"Your name is {self.name}.",
                     f"Your profile: {details['user_profile']}."]
            for field in OPTIONAL_FIELDS:
                value = details.get(field)
                if value is None:
                    lines.append(f"{_LABELS[field]} was not supplied.")
                elif field == "age":
                    lines.append(f"Age: {value} years old.")
                else:
                    lines.append(f"{_LABELS[field]}: {value}.")
            description = "\n".join(lines)
            return ("\n# OBJECTIVE\nYou're a Reddit user, and I'll present "
                    "you with some tweets. After you see the tweets, choose "
                    "some actions from the following functions.\n\n"
                    "# SELF-DESCRIPTION\nYour actions should be consistent "
                    "with your self-description and personality.\n"
                    f"{description}\n\n# RESPONSE METHOD\n"
                    "Please perform actions by tool calling.\n")

    graph = AgentGraph()
    for index, row in enumerate(rows):
        other_info = {"user_profile": row["persona"]}
        for field in OPTIONAL_FIELDS:
            if row.get(field) is not None:
                other_info[field] = row[field]
        user_info = HonestRedditUserInfo(
            name=row["username"], description=row["bio"],
            profile={"nodes": [], "edges": [], "other_info": other_info},
            recsys_type="reddit")
        graph.add_agent(SocialAgent(
            agent_id=index, user_info=user_info, agent_graph=graph,
            model=model, available_actions=available_actions))
    return graph
