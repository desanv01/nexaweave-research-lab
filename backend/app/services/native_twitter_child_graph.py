"""Private reconstruction of an owned Twitter child graph."""

import csv
import hashlib
import io
import math
import time

from .native_recording_contracts import HEX, MAX_JSON, RecordingError


_PROFILE_FIELDS = ("user_id", "name", "username", "user_char", "description")


def _check_deadline(deadline):
    if time.monotonic() >= deadline:
        raise RecordingError()


def _validate_prepared_profiles(profile_bytes, expected_owner_names, deadline):
    profiles = []
    with io.TextIOWrapper(io.BytesIO(profile_bytes), encoding="utf-8", newline="") as stream:
        rows = csv.reader(stream, strict=True)
        headers = next(rows, ())
        if (len(set(headers)) != len(headers)
                or any(not name or name != name.strip() or "\r" in name or "\n" in name
                       for name in headers)
                or not all(field in headers for field in _PROFILE_FIELDS)):
            raise RecordingError()

        positions = tuple(headers.index(field) for field in _PROFILE_FIELDS)
        for platform_id, expected_name in enumerate(expected_owner_names):
            _check_deadline(deadline)
            row = next(rows, ())
            if len(row) != len(headers):
                raise RecordingError()
            user_id, name, username, persona, description = (row[i] for i in positions)
            if (user_id != str(platform_id) or name != expected_name
                    or not all(value.strip() for value in (username, persona, description))):
                raise RecordingError()
            profiles.append((username, persona, description))
        if next(rows, None) is not None:
            raise RecordingError()
    _check_deadline(deadline)
    return tuple(profiles)


async def _rebuild_twitter_round_child_graph(
    profile_bytes, *, expected_profile_sha256, expected_owner_names, memory_bindings,
    restored_memories, expected_topology, model, available_actions, deadline_monotonic,
):
    """Adopt authenticated memories into a new, unstarted OASIS graph."""
    try:
        if (type(deadline_monotonic) not in (int, float) or not math.isfinite(deadline_monotonic)
                or type(expected_profile_sha256) is not str
                or not HEX.fullmatch(expected_profile_sha256)
                or type(expected_owner_names) is not tuple or len(expected_owner_names) != 2
                or not all(type(name) is str and name.strip() for name in expected_owner_names)
                or expected_owner_names[0] == expected_owner_names[1]
                or model is None or available_actions is None):
            raise RecordingError("invalid_request")
        _check_deadline(deadline_monotonic)
        if (type(profile_bytes) is not bytes or not 0 < len(profile_bytes) <= MAX_JSON
                or hashlib.sha256(profile_bytes).hexdigest() != expected_profile_sha256
                or b"\x00" in profile_bytes):
            raise RecordingError()
        _check_deadline(deadline_monotonic)
        expected_profiles = _validate_prepared_profiles(
            profile_bytes, expected_owner_names, deadline_monotonic,
        )

        from oasis.social_agent.agents_generator import (
            connect_platform_channel, generate_twitter_agent_graph,
        )
        from oasis.social_platform import Channel

        from .native_checkpoint_memory import _install_detached_twitter_round_memory

        _check_deadline(deadline_monotonic)
        with io.BytesIO(profile_bytes) as profile_stream:
            graph = await generate_twitter_agent_graph(
                profile_stream, model=model, available_actions=available_actions,
            )
        for platform_id, expected in enumerate(expected_profiles):
            _check_deadline(deadline_monotonic)
            user_info = graph.get_agent(platform_id).user_info
            # OASIS uses the CSV username as UserInfo.name.
            actual = (user_info.name, user_info.profile["other_info"]["user_profile"],
                      user_info.description)
            if not all(type(value) is str for value in actual) or actual != expected:
                raise RecordingError()
        _check_deadline(deadline_monotonic)
        channel = Channel()
        connect_platform_channel(channel=channel, agent_graph=graph)
        _check_deadline(deadline_monotonic)
        _install_detached_twitter_round_memory(
            graph, channel, memory_bindings=memory_bindings, restored_memories=restored_memories,
            expected_topology=expected_topology, deadline_monotonic=deadline_monotonic,
        )
        _check_deadline(deadline_monotonic)
        return graph
    except RecordingError as error:
        refusal_code = error.code
    except Exception:
        refusal_code = "invalid_recording"
    finally:
        profile_bytes = expected_profiles = user_info = actual = expected = None
        graph = channel = None
    # The native parser or adopter traceback can own raw data or a partial graph.
    raise RecordingError(refusal_code)
