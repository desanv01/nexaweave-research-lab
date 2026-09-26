# File IPC boundary (U02b)

`SimulationIPCClient` and `SimulationIPCServer` retain the command, response,
interview and close-environment schemas. The caller supplies a trusted
`simulation_dir`. The transport rejects that directory if its entry is a
symlink or Windows reparse point, then validates command, response, status and
temporary files against that same root with `safe_path` before filesystem
access. It does not treat `ipc_commands` or `ipc_responses` as a new trusted
root.

Command IDs use the portable resource ID rules and are limited to 123
characters so the `.json` filename fits the shared 128-character component
bound. A command filename stem must match its payload ID; a response payload
ID must match the client's awaited command. Corrupt, excessively nested or oversized candidates
are logged without payloads or private paths and left untouched. Later valid
commands remain available. Incoming JSON reads are limited to 1 MiB before
UTF-8 decoding. Outbound messages are checked as UTF-8 bytes before writing;
serialization recursion failures are treated as invalid messages.
Decoded and outbound messages also have a 64-container depth limit, counted
from a root object or array at depth 1. Outbound data is checked after JSON
serialization, so Python tuples that serialize as arrays count too. This
explicit limit does not depend on a particular Python decoder's recursion
threshold.
This limit does not control the overall HTTP request body.

Regular-file checks reject FIFOs, sockets, and devices before bounded reads.
Response publication checks both its own target and the matching command
target and verifies any existing command payload ID first.

Command, response and environment-status writes use unique `.tmp` files in the
same directory and `os.replace` after a complete write. Pollers consider only
published `.json` files. On failure, cleanup targets
only the exact temporary file owned by that write. Command and response
cleanup remain independent. Polling uses monotonic deadlines; zero timeout
expires immediately after the command is sent and removed.

This is a local file transport, not a durable queue or an authorization
boundary. An actor able to change directory entries concurrently can still
race checks and opens. The inherited simulation scripts also have their own
file IPC loops; this packet changes only `simulation_ipc.py` and does not
qualify actual agent interviews or surveys.
