# Installed OASIS native action qualification boundary

`backend/engine_tests/test_native_platform_actions.py` targets the installed `camel-oasis==0.2.5` `oasis.social_platform.platform.Platform`. It constructs the real Platform and its real temporary SQLite schema, signs up two synthetic actors, calls all six application-configured Twitter-like action methods and all thirteen Reddit-like action methods, and checks both return values and native database rows/traces. No Platform or SQLite mutation method is mocked. This test directory is separate from lean `backend/tests` discovery; Main runs it with the inherited backend virtual environment.

Twitter uses the inherited `twhin-bert` recommendation type. The fixture does not call its `update_rec_table`, which can download model weights. Reddit's `update_rec_table` executes locally; the fixture checks rec rows and a successful `refresh`. This establishes only a small primitive-path result, not recommendation quality or exposure policy. Platform import opens a log file under the current directory, so the fixture changes into `tmp_path` before importing it.

The negative fixture checks only observed native rejection paths: missing post targets for repost/quote/like, and a duplicate actor sign-up. The installed SQLite setup does not enable foreign-key enforcement by default, and some methods do not check whether actor/post IDs exist before a write. We do not assert that all invalid targets are safely rejected. Main should treat this as a limitation for later hardening.

These tests do not construct a `SocialAgent` or CAMEL model, dispatch `env.step`, perform autonomous action selection, demonstrate memory effects, launch a simulation process, or call live interviews. All OASIS/CAMEL inference and full application capability gates remain separate. No paid model calls are made.

Main's isolated command after its environment setup:

```powershell
backend/.venv/Scripts/python.exe tools/check_engine_imports.py
backend/.venv/Scripts/python.exe tools/run_engine_tests.py
```

The Main-owned launchers strip inherited model credentials, disable model
downloads/telemetry and block non-loopback test sockets. Native log files are
confined to disposable working directories. The Windows CI job installs the
unchanged inherited backend lock before running these commands. This profile
does not establish Linux native-engine compatibility. Main's Windows import
probe passed with Torch2.9.1, CAMEL0.2.78 and OASIS0.2.5.

U01b also corrects one inherited file-IPC cleanup defect in `backend/app/services/simulation_ipc.py`: a server may already have deleted the generated command file when the client consumes a response. The client now attempts cleanup of the generated command and response paths independently, so the response is not left behind. It does not change waiting, timeout or response semantics.
