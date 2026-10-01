# Prepared native execution controls (U07a)

This bounded U07 packet adds optional controls to the accepted prepared
`NativeSimulationSession`. Main must qualify the candidate with the locked
actual OASIS/CAMEL runtime. It is not whole-U07, all-44 capability, paid-model,
HTTP/UI, report IPC, checkpoint or deployment acceptance.

## Frozen prepared input

`simulation_config.json` may contain this strict versioned object:

```json
{
  "execution_controls": {
    "schema_version": 1,
    "recommendation_presets": {
      "twitter": "following_only",
      "reddit": "compact_popularity"
    },
    "initial_follows": {
      "twitter": [{"follower_agent_id": 1, "followee_agent_id": 0}],
      "reddit": [{"follower_agent_id": 0, "followee_agent_id": 1}]
    },
    "agent_activity": [
      {"agent_id": 0, "timezone_offset_minutes": 330,
       "active_hours": [8, 9, 20], "activity_probability": 0.75},
      {"agent_id": 1, "timezone_offset_minutes": -30,
       "active_hours": [23], "activity_probability": 0}
    ]
  }
}
```

Only `schema_version` is mandatory inside the object. Missing
`execution_controls` takes the accepted native path, including the Reddit
default enum construction. Null is invalid. All nested unknown fields are
invalid; platform dictionaries may mention only enabled platforms. Presets
are names, never arbitrary native kwargs, module selectors, recsys strings,
provider credentials, URLs or source paths.

Preflight rejects malformed controls before the one-shot claim, native engine
loading, SQLite creation or model use. Limits are 500 agents, 1,000 unique
directed edges per platform, and one activity record per agent. IDs must be
existing integers, with booleans rejected. Edges may not be self edges or
duplicates. Hours are unique integers 0–23 (an empty list means no eligible
hours). Offset minutes are integers -720 through 840, including half-hour
offsets. Probabilities are finite numbers 0–1, with explicit zero inactive.
For named agents, omitted hours/probability inherit validated prepared agent
settings (defaults 8–22 and 0.5); omitted offset is zero. Controls are copied
into immutable tuple storage; prepared input files are never rewritten.

## Native feed presets

| Platform / preset | Native recommender | Refresh recommendation count | Recommendation buffer maximum | Followed-post count |
|---|---|---:|---:|---:|
| Twitter `balanced_random` | random | 2 | 2 | 3 |
| Twitter `following_only` | random | 0 | 2 | 10 |
| Reddit `compact_popularity` | reddit | 1 | 2 | 3 (unused by Reddit refresh) |
| Reddit `broad_popularity` | reddit | 5 | 20 | 3 (unused by Reddit refresh) |

These tune existing native feed sizes and sources. Twitter random rec-table
updates still occur for `following_only`, but refresh returns zero posts from
that table and up to ten posts by followed users. Native refresh merges and
deduplicates followed and random posts for balanced mode, so returned counts
can vary with overlap and available posts. Reddit retains its native hot-score
ordering and random sample from the bounded recommendation buffer; these are
not new semantic relevance algorithms. Reddit presets retain `show_score=True`
and `allow_self_rating=True`. All four keep embeddings off. Native trace and
TWHIN weight-loading recommenders are not exposed.

Unspecified Twitter retains accepted random counts 2/2/3. Unspecified Reddit
uses `DefaultPlatformType.REDDIT`, whose locked native constructor has refresh
5, maximum 100, followed count 3, score display and self-rating enabled.

## Owned network and scheduling

After native `env.reset` completes signup, and within the existing round-zero
stage before initial posts, the session checks the actual signup mapping and
applies edges in sorted `(follower, followee)` order. It uses supported
`SocialAgent.perform_action_by_data(ActionType.FOLLOW, followee_id=...)`;
this invokes the real channel/platform action, updates native agent memory
and returns the exact native response. A successful response must contain only
`success=True` and a positive integer `follow_id`. The native graph edge is
then updated with its supported graph-action method. Successful follow actions
are logged once in round zero and included in existing action counts.
No database rows are injected and no models are called for this stage.

When controls are present, the trace cursor starts after initial events to
avoid logging those actions again as autonomous work. Absent controls retains
the inherited cursor behavior. Failure propagates to accepted session cleanup,
which closes adopted environments. The exclusive start marker is retained;
partial effects are never reset or retried. A malformed/failure response may
follow a real native side effect, so its diagnostic status is `uncertain`, not
a fabricated assertion that nothing happened.

The two inherited platform call sites pass full `simulated_minutes` into the
optional scheduler hook. A named agent's local hour is
`((simulated_minutes + timezone_offset_minutes) // 60) % 24`. Global configured
peak/off-peak multipliers, min/max target count, ordering, random probability
draws, candidate sampling and OASIS autonomous action model remain inherited.
Only named-agent local eligibility and probability change. Direct CLI without
the trusted session remains unchanged. Python RNG state is restored around
native loading and execution, including failures.

## Diagnostic and Main verification

After validated owned execution (successful or failed), the session exclusively
creates `native_effective_controls.json`. It contains schema, resolved fixed
preset/default values, named-agent resolved activity settings, and bounded
network attempts with native follow IDs on successful responses. A failed
attempt is conservatively uncertain. The file contains no model/credential
payloads and is not a durable completion receipt or independent acceptance.
Existing diagnostic files also fence a fresh start; no output is reset.

Authored pure tests exercise strict input validation, freezing, deterministic
edge order, scheduling boundaries, zero/one probability and inherited scheduler
RNG parity. Authored actual-engine tests use the accepted offline model/prepared
fixture to inspect follow rows/counters before autonomous work, all four actual
rec tables and refresh contents/counts, both platforms' action availability,
midnight/half-hour activity, seeded reruns, frozen bytes, duplicate claims,
preflight rejection and partial-effect cleanup. Main owns test execution,
inherited script manifest/patch hashes, runner/CI changes, review and acceptance.
Retain Vue/Flask/OASIS/CAMEL, Graphiti/self-hosted Neo4j Community, configurable
DeepSeek official API (`deepseek-flash` initially), separate embeddings and
inherited notices. This work makes no provider calls or weight downloads.
