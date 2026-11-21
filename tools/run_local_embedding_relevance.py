"""One fixed retrieval relevance witness for an already-ready loopback model."""

import http.client
import io
import json
import math
import socket
import sys
import time


HOST = "127.0.0.1"
MODEL = "qwen3-embedding-0.6b-q8"
DIMENSIONS = 1024
DEADLINE_SECONDS = 5.0
HEADER_BYTES = 8192
RESPONSE_BYTES = 131072
TIE_TOLERANCE = 1e-12

QUERY = "Which organization employs the specialist who studies sound underwater?"
INSTRUCTION = "Given a research question, retrieve the passage that answers it."
RELEVANT = "Harbor Acoustics Laboratory hired marine bioacoustics researcher Mira Vale as a staff scientist to study underwater sound."
COMPETITOR = "Estuary Signal Laboratory posted a vacancy for a marine bioacoustics scientist to study underwater sound. It had not hired anyone for the position."
ADJUDICATION = "Harbor Acoustics is relevant. Estuary describes a vacancy without employment."


class Refused(ValueError):
    pass


def remaining(deadline):
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise Refused("deadline_exceeded")
    return seconds


def receive(connection, size, deadline):
    connection.settimeout(remaining(deadline))
    data = connection.recv(size)
    if not data:
        raise Refused("incomplete_response")
    return data


def response_body(connection, deadline):
    pending = bytearray()
    while b"\r\n\r\n" not in pending:
        available = HEADER_BYTES - len(pending)
        if available <= 0:
            raise Refused("response_headers_too_large")
        pending.extend(receive(connection, min(4096, available), deadline))
    head, _, initial = pending.partition(b"\r\n\r\n")
    status, _, fields = head.partition(b"\r\n")
    words = status.split(b" ", 2)
    if len(words) < 2 or words[0] not in (b"HTTP/1.0", b"HTTP/1.1"):
        raise Refused("invalid_http_response")
    code = words[1]
    if len(code) != 3 or not code.isdigit():
        raise Refused("invalid_http_response")
    if code != b"200":
        raise Refused("http_status_failure")
    headers = http.client.parse_headers(io.BytesIO(fields + b"\r\n\r\n"))
    lengths = headers.get_all("Content-Length", [])
    media = headers.get_all("Content-Type", [])
    if (headers.defects or headers.get_all("Transfer-Encoding") or headers.get_all("Content-Encoding")
            or len(lengths) != 1 or len(media) != 1 or headers.get_content_type() != "application/json"):
        raise Refused("invalid_http_response")
    length = lengths[0].strip(" \t")
    if not length.isascii() or not length.isdecimal():
        raise Refused("invalid_http_response")
    if len(length) > len(str(RESPONSE_BYTES)) or int(length) > RESPONSE_BYTES:
        raise Refused("response_too_large")
    size = int(length)
    if not 0 < size or len(initial) > size:
        raise Refused("invalid_http_response")
    while len(initial) < size:
        initial.extend(receive(connection, min(8192, size - len(initial)), deadline))
    return initial


def request_embeddings(port):
    payload = json.dumps({
        "model": MODEL,
        "input": [f"Instruct: {INSTRUCTION}\nQuery: {QUERY}", RELEVANT, COMPETITOR],
    }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = (
        f"POST /v1/embeddings HTTP/1.1\r\nHost: {HOST}:{port}\r\n"
        "Connection: close\r\nAccept: application/json\r\nContent-Type: application/json\r\n"
        f"Content-Length: {len(payload)}\r\n\r\n"
    ).encode("ascii") + payload

    start = time.monotonic()
    deadline = start + DEADLINE_SECONDS
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(remaining(deadline))
        connection.connect((HOST, port))
        connection.settimeout(remaining(deadline))
        connection.sendall(request)
        response = response_body(connection, deadline)
        elapsed = time.monotonic() - start
        if elapsed >= DEADLINE_SECONDS:
            raise Refused("deadline_exceeded")
    return response, elapsed


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise Refused("invalid_embedding_response")
        value[key] = item
    return value


def invalid_constant(_value):
    raise Refused("invalid_embedding_response")


def unit_vector(value):
    if (type(value) is not list or len(value) != DIMENSIONS
            or any(type(number) not in (int, float) for number in value)):
        raise Refused("invalid_vector")
    try:
        values = [float(number) for number in value]
    except OverflowError:
        raise Refused("invalid_vector") from None
    if not all(math.isfinite(number) for number in values):
        raise Refused("invalid_vector")
    scale = max(abs(number) for number in values)
    if not scale:
        raise Refused("invalid_vector")
    scaled = [number / scale for number in values]
    norm = math.hypot(*scaled)
    return [number / norm for number in scaled]


def embeddings(raw):
    try:
        reply = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object,
                           parse_constant=invalid_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise Refused("invalid_embedding_response") from None
    if type(reply) is not dict or reply.get("model") != MODEL:
        raise Refused("invalid_response_model")
    data = reply.get("data")
    if type(data) is not list or len(data) != 3:
        raise Refused("invalid_embedding_indexes")
    vectors = {}
    for row in data:
        if type(row) is not dict:
            raise Refused("invalid_embedding_response")
        index = row.get("index")
        if type(index) is not int or index not in (0, 1, 2) or index in vectors:
            raise Refused("invalid_embedding_indexes")
        vectors[index] = unit_vector(row.get("embedding"))
    return vectors[0], vectors[1], vectors[2]


def evaluate(port):
    raw, elapsed = request_embeddings(port)
    query, relevant, competitor = embeddings(raw)
    scores = {
        "relevant": math.fsum(left * right for left, right in zip(query, relevant)),
        "competitor": math.fsum(left * right for left, right in zip(query, competitor)),
    }
    result = {
        "adjudication": ADJUDICATION,
        "dimensions": DIMENSIONS,
        "inference_request_seconds": elapsed,
        "scores": scores,
    }
    if math.isclose(scores["relevant"], scores["competitor"], rel_tol=0, abs_tol=TIE_TOLERANCE):
        return {**result, "status": "failed", "reason": "ambiguous_rank", "rank": None}
    rank = sorted(scores, key=scores.__getitem__, reverse=True)
    if rank[0] != "relevant":
        return {**result, "status": "failed", "reason": "relevant_not_first", "rank": rank}
    return {**result, "status": "passed", "rank": rank}


def read_port(args):
    if (len(args) != 2 or args[0] != "--port" or not 1 <= len(args[1]) <= 5
            or not args[1].isascii() or not args[1].isdecimal()):
        raise Refused("invalid_port")
    port = int(args[1])
    if not 1 <= port <= 65535:
        raise Refused("invalid_port")
    return port


def main():
    try:
        result = evaluate(read_port(sys.argv[1:]))
    except Refused as error:
        result = {"status": "failed", "reason": str(error)}
    except TimeoutError:
        result = {"status": "failed", "reason": "deadline_exceeded"}
    except (OSError, http.client.HTTPException):
        result = {"status": "failed", "reason": "io_failure"}
    print(json.dumps({"case": "fixed_local_embedding_relevance", "model": MODEL, **result},
                     allow_nan=False, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
