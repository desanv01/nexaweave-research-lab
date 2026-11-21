"""Explicit Reddit checkpoint preparation option and committed config binding."""

REDDIT_CHECKPOINT = 'reddit_checkpoint'
_COMMITTED_COMMENT = 'reddit_checkpoint_comment'


def _bounded_text(value, maximum):
    if (type(value) is not str or not value.strip() or not 1 <= len(value.encode('utf-8')) <= maximum
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise ValueError("invalid Reddit checkpoint text")


def reddit_checkpoint_option(options):
    if REDDIT_CHECKPOINT not in options:
        return None
    value = options[REDDIT_CHECKPOINT]
    if (options['platforms'] != ['reddit'] or options['max_rounds'] != 1
            or not 2 <= options['max_agents'] <= 8
            or type(value) is not dict or set(value) != {'comment', 'seed_post'}):
        raise ValueError("invalid Reddit checkpoint option")
    seed = value['seed_post']
    if (type(seed) is not dict or set(seed) != {'poster_agent_id', 'content'}
            or type(seed['poster_agent_id']) is not int
            or not 0 <= seed['poster_agent_id'] < options['max_agents']):
        raise ValueError("invalid Reddit checkpoint seed")
    _bounded_text(seed['content'], 512)
    _bounded_text(value['comment'], 512)
    return value


def freeze_reddit_checkpoint(config, options):
    value = reddit_checkpoint_option(options)
    if _COMMITTED_COMMENT in config:
        raise ValueError("unowned Reddit checkpoint comment")
    if value is not None:
        if type(config.get('event_config')) is not dict:
            raise ValueError("invalid Reddit event configuration")
        config['event_config']['initial_posts'] = [dict(value['seed_post'])]
        config[_COMMITTED_COMMENT] = value['comment']
        validate_reddit_checkpoint_config(config, options)


def validate_reddit_checkpoint_config(config, options):
    value = reddit_checkpoint_option(options)
    if value is None:
        if _COMMITTED_COMMENT in config:
            raise ValueError("unowned Reddit checkpoint comment")
        return
    from nexaweave_execution.native_seed_contracts import validate_native_seed_plan

    actors = config.get('agent_configs')
    if (type(actors) is not list or not 2 <= len(actors) <= options['max_agents']
            or config.get(_COMMITTED_COMMENT) != value['comment']
            or config['event_config']['initial_posts'] != [value['seed_post']]):
        raise ValueError("changed Reddit checkpoint binding")
    if len(validate_native_seed_plan(config)) != 1:
        raise ValueError("invalid Reddit checkpoint seed context")
