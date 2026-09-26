# U01b simulation boundary characterization

These are deterministic fixtures against inherited MiroFish code. Main runs and reviews them. They do not launch OASIS/CAMEL, create model clients, call paid APIs, or claim simulation parity.

| Test file | Executed boundary | What an assertion means |
|---|---|---|
| `test_simulation_boundary_contracts.py` | AST reading of the two configured runner action lists; real profile serializers and configuration dataclasses | The application offers the enumerated actions and emits the measured fixture fields/IDs. An offered enum value is not evidence an engine action succeeds. |
| `test_action_log_contracts.py` | Actual `PlatformActionLogger` and legacy `ActionLogger` methods writing JSONL under `tmp_path` | Start/round/action/end shapes, platform file separation, action args/result/success, UTF-8 round-trip, failed action and abstention serialization. |
| `test_interview_ipc_contracts.py` | Actual command/response DTOs, file IPC client/server, and runner forwarding with only the waiting boundary stubbed | Command payloads and status/error/timeout propagation. The fixture acknowledgment is not an agent answer. |
| `test_runner_schedule_contracts.py` | The exact `get_active_agents_for_round` AST function definition compiled from current source, controlled random draws, fake `agent_graph` | Active-hours/probability selection, peak/off-peak target counts and missing-agent omission for this function. It is not a full runner import or OASIS scheduling integration test. |

The configured catalogs in `run_parallel_simulation.py` are six Twitter-like actions (`CREATE_POST`, `LIKE_POST`, `REPOST`, `FOLLOW`, `DO_NOTHING`, `QUOTE_POST`) and thirteen Reddit-like actions (`LIKE_POST`, `DISLIKE_POST`, `CREATE_POST`, `CREATE_COMMENT`, `LIKE_COMMENT`, `DISLIKE_COMMENT`, `SEARCH_POSTS`, `SEARCH_USER`, `TREND`, `REFRESH`, `DO_NOTHING`, `FOLLOW`, `MUTE`). `INTERVIEW` is a separate manual IPC command.

The profile fixture uses actor IDs 0 and 1 across actual Twitter CSV and Reddit JSON outputs. Reddit retains `profile.user_id`. Twitter writes the CSV row index as `user_id`; a nonsequential source ID such as 42 becomes 0. `save_profiles` defaults to Reddit when the platform argument is omitted, and any value other than `"twitter"` also selects Reddit. These are inherited behaviors recorded here, not approval of their safety or future design. The exported platform profiles do not include the original `source_entity_uuid`/type fields.

`PlatformActionLogger.log_simulation_start` calculates `total_rounds` as `total_simulation_hours * 2`, without reading `minutes_per_round`. The fixture documents this hardcoded two-rounds-per-hour assumption and does not change it. The scheduler fixture separately measures `agents_per_hour` count scaling via a period multiplier and `random.uniform`.

Still unqualified: OASIS action execution and invalid-target behavior, native recommendation/exposure policy, CAMEL/OASIS memory effects, full-module runner import compatibility, real process/IPC deadlines, live interviews, report research, and end-to-end paired-platform progress. Those require Main's later integration and capability gates.

Main command after dependency setup:

```powershell
python tools/run_unit_tests.py
```

Main's first full Windows run exposed an actual stale IPC response defect.
The narrowly authorized fix removes command and response files independently.
Do not weaken the response-cleanup assertion. Linux CI is required for inherited
symlink tests because this Windows host lacks symlink privileges.
