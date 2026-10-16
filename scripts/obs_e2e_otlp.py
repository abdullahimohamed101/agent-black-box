"""A tiny OTLP/HTTP trace receiver for scripts/obs-e2e.sh (Phase 19c step 6): stdlib `http.server` only, no collector image.

Run with the interpreter of the `otel` environment (it needs `opentelemetry-proto`, which the OTLP exporter already depends on):

    python obs_e2e_otlp.py --port 4318 --out DIR            # keeps every request body and a decoded JSON line per request
    python obs_e2e_otlp.py --port 4318 --discard            # only counts (the cost benchmark)

`POST /v1/traces` with a protobuf `ExportTraceServiceRequest` is decoded into plain dictionaries (hex ids, attributes, links,
events) so the checks can run in the default environment without OpenTelemetry. Every raw body is also written verbatim to
`DIR/body-<n>.bin`, which the sentinel scan reads: what was exported is what is scanned. Answers 200 with an empty response.
"""

import argparse
import gzip
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest


def _value(any_value: Any) -> Any:
    kind = any_value.WhichOneof("value")
    if kind is None:
        return None
    if kind == "array_value":
        return [_value(v) for v in any_value.array_value.values]
    if kind == "kvlist_value":
        return {kv.key: _value(kv.value) for kv in any_value.kvlist_value.values}
    return getattr(any_value, kind)


def _attrs(attributes: Any) -> dict[str, Any]:
    return {kv.key: _value(kv.value) for kv in attributes}


def decode(body: bytes) -> list[dict[str, Any]]:
    request = ExportTraceServiceRequest()
    request.ParseFromString(body)
    spans: list[dict[str, Any]] = []
    for resource_spans in request.resource_spans:
        resource = _attrs(resource_spans.resource.attributes)
        for scope_spans in resource_spans.scope_spans:
            for span in scope_spans.spans:
                spans.append({
                    "trace_id": span.trace_id.hex(), "span_id": span.span_id.hex(), "parent_span_id": span.parent_span_id.hex(), "name": span.name,
                    "kind": int(span.kind), "attributes": _attrs(span.attributes), "resource": resource, "scope": scope_spans.scope.name,
                    "links": [{"trace_id": link.trace_id.hex(), "span_id": link.span_id.hex(), "attributes": _attrs(link.attributes)} for link in span.links],
                    "events": [event.name for event in span.events], "status_code": int(span.status.code), "status_message": span.status.message,
                    "dropped_attributes": int(span.dropped_attributes_count),
                })
    return spans


class Receiver(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, out: Path | None) -> None:
        self.out = out
        self.count = 0
        self.lock = threading.Lock()
        super().__init__(("127.0.0.1", port), Handler)


class Handler(BaseHTTPRequestHandler):
    server: Receiver

    def log_message(self, *_: Any) -> None:  # nothing is logged: bodies are what this receiver exists to inspect, elsewhere
        return

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        if self.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        if self.path != "/v1/traces":
            self.send_response(404)
            self.end_headers()
            return
        server = self.server
        with server.lock:
            server.count += 1
            number = server.count
        if server.out is not None:
            (server.out / f"body-{number}.bin").write_bytes(body)
            try:
                spans = decode(body)
            except Exception as error:  # noqa: BLE001 - recorded, so the check fails with a reason
                spans = []
                line = {"n": number, "error": type(error).__name__}
            else:
                line = {"n": number, "content_type": self.headers.get("Content-Type"), "spans": spans}
            with server.lock, (server.out / "spans.jsonl").open("a") as handle:
                handle.write(json.dumps(line) + "\n")
        self.send_response(200)
        self.send_header("Content-Type", "application/x-protobuf")
        self.send_header("Content-Length", "0")
        self.end_headers()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--discard", action="store_true")
    args = parser.parse_args()
    if not args.discard:
        if args.out is None:
            parser.error("--out or --discard")
        args.out.mkdir(parents=True, exist_ok=True)
    server = Receiver(args.port, None if args.discard else args.out)
    print(f"listening {args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
