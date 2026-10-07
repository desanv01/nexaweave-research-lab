"""SDK-cold seed admission shared by owned binding and legacy native runners.

This boundary uses only the standard library. It does not load a backend,
engine, provider, model factory or database. Returned tuples snapshot the plan.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class NativeSeedPost:
    actor_id: int
    content: str


def validate_native_seed_plan(config):
    """Validate the whole batch, preserving exact scalar text and source order.

    Empty/absent seeds do not impose richer actor requirements on minimal
    owned bindings. Nonempty seeds require contiguous, nonboolean actor IDs
    from the admitted configuration. Existing artifact limits bound the batch.
    """
    def refuse():
        raise ValueError("invalid native seed plan")

    if type(config) is not dict:
        refuse()
    if "event_config" not in config:
        return ()
    event = config["event_config"]
    if type(event) is not dict:
        refuse()
    posts = event.get("initial_posts", [])
    if type(posts) is not list:
        refuse()
    if not posts:
        return ()
    actors = config.get("agent_configs")
    if type(actors) is not list or not 1 <= len(actors) <= 500:
        refuse()
    for index, actor in enumerate(actors):
        if (type(actor) is not dict or type(actor.get("agent_id")) is not int
                or actor["agent_id"] != index):
            refuse()
    result = []
    for post in posts:
        if (type(post) is not dict
                or type(post.get("poster_agent_id")) is not int
                or not 0 <= post["poster_agent_id"] < len(actors)
                or type(post.get("content")) is not str):
            refuse()
        try:
            post["content"].encode("utf-8", errors="strict")
        except UnicodeError:
            refuse()
        result.append(NativeSeedPost(post["poster_agent_id"], post["content"]))
    return tuple(result)
