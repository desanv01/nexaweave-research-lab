# Native handler IPC integration (U02c)

The Twitter, Reddit, and parallel simulation script handlers delegate their
file transport to `SimulationIPCServer`. They retain their `poll_command`,
`send_response`, and `update_status` methods, environment and agent references,
path attributes, and running flags. A validated `IPCCommand` is converted to
the existing command dictionary at the dispatcher boundary. Response strings
are converted to `CommandStatus` before the shared server writes them.

The shared server now exposes `update_status(status, *, twitter_available=None,
reddit_available=None)`. It accepts a nonempty status string of at most 64
characters without control characters, preserving emitted lifecycle values
such as `running`, `waiting`, `alive`, and `stopped`. The parallel handler
passes both platform availability booleans. The shared server generates the
timestamp and fixes the destination; callers cannot supply either. Status
files use the shared bounded, atomic file writer.

Command polling, response publication, and status writes inherit the U02b
path, byte, depth, regular-file and atomicity bounds. Malformed transport
candidates do not reach script dispatch. The script handlers still decide how
to conduct interviews and which platform to use. File transport tests extract
and execute the complete handler class definitions with real shared transport;
they do not run native agents or qualify interview answers.

Main's import smoke launcher separately loads each complete script in its own
temporary-working-directory child with dummy credentials, blocked external
Python sockets and offline model caches. This catches actual import wiring
errors without executing any script's main simulation entry point. It is not
an autonomous simulation or an interview-quality test.

This remains a local filesystem transport. A local actor able to swap links
concurrently may race a check and a later file operation. Authentication,
engine action behavior, and native interview qualification are separate work.
