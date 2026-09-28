"""Stdlib-only stand-in for whisper-server.exe, driven by test_stt_whisper_server.py.

Launched as a child process exactly the way `stt_whisper.WhisperServer` launches
the real binary, so the manager's lifecycle code (spawn, health poll, GPU-line
sniffing, request routing, stop) runs against a REAL process instead of a mock
object. It never touches whisper.cpp or a real model.

Argv shape: the manager appends its own flags (`-m`, `--host`, `--port`, `-t`,
`-bs`, `-bo`, `-nlp`, `--request-path`) after whatever a test's `command`
lambda put first. `--fake-*` flags configure THIS script; parse_known_args
ignores whichever set of flags is not its own, so the two can be interleaved
in any order.
"""
import argparse
import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# whisper answers with the full language name (see stt_whisper._LANG_CODES);
# the echo below mirrors that so a test can assert on the same strings the
# real server would produce.
_LANG_NAMES = {"tr": "turkish", "en": "english"}


def _parse_args(argv):
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--fake-gpu", choices=("on", "off", "silent"), default="off")
    parser.add_argument("--fake-startup-delay", type=float, default=0.0)
    parser.add_argument("--fake-crash-on-inference", action="store_true")
    parser.add_argument("--fake-inference-delay", type=float, default=0.0)
    parser.add_argument("--fake-exit-at-start", type=int, default=None)
    # The manager's own flags. Values are accepted and otherwise unused; this
    # script only needs --port and --request-path to serve anything.
    parser.add_argument("-m", dest="model", default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("-t", dest="threads", default=None)
    parser.add_argument("-bs", dest="beam_size", default=None)
    parser.add_argument("-bo", dest="best_of", default=None)
    parser.add_argument("-nlp", dest="no_lang_probe", action="store_true")
    parser.add_argument("--request-path", dest="request_path", required=True)
    args, _unknown = parser.parse_known_args(argv)
    return args


def _parse_multipart(body: bytes, boundary: str):
    """Parses exactly the shape `stt_whisper.build_multipart` produces: a run
    of text fields followed by one file part, each ending in a `\\r\\n` before
    the next boundary marker. Not a general-purpose multipart parser — it
    does not need to be, since this process only ever talks to that encoder.
    """
    delim = b"--" + boundary.encode("ascii")
    frags = body.split(delim)
    fields = {}
    file_bytes = b""
    for frag in frags[1:-1]:            # frags[0] is empty, frags[-1] is "--\r\n"
        if frag.startswith(b"\r\n"):
            frag = frag[2:]
        sep = frag.find(b"\r\n\r\n")
        if sep == -1:
            continue
        headers = frag[:sep].decode("utf-8", "replace")
        content = frag[sep + 4:]
        if content.endswith(b"\r\n"):
            content = content[:-2]
        name_match = re.search(r'name="([^"]+)"', headers)
        name = name_match.group(1) if name_match else None
        if "filename=" in headers:
            file_bytes = content
        elif name:
            fields[name] = content.decode("utf-8", "replace")
    return fields, file_bytes


def main():
    args = _parse_args(sys.argv[1:])

    if args.fake_exit_at_start is not None:
        # Exit before the socket ever opens: simulates a server that dies
        # during startup, which is what test_exit_at_start_* observes.
        sys.exit(args.fake_exit_at_start)

    # Printed to stderr (merged into the same pipe as stdout by the manager)
    # BEFORE the socket opens, matching the real server: the GPU line appears
    # while the model loads, ahead of the first successful /health.
    if args.fake_gpu == "on":
        print("whisper_backend_init_gpu: using Vulkan0 backend", file=sys.stderr)
    elif args.fake_gpu == "off":
        print("whisper_backend_init_gpu: no GPU found", file=sys.stderr)
    # "silent": prints nothing, matching a build with no GPU backend line —
    # the manager treats an unclear state as CPU once ready.
    sys.stderr.flush()

    prefix = args.request_path
    started_at = time.monotonic()
    startup_delay = args.fake_startup_delay
    crash_on_inference = args.fake_crash_on_inference
    inference_delay = args.fake_inference_delay

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *a):
            pass  # keep the pipe stt_whisper reads for the GPU line uncluttered

        def _write_json(self, status, payload):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == prefix + "/health":
                if time.monotonic() - started_at < startup_delay:
                    self._write_json(503, {"status": "loading"})
                else:
                    self._write_json(200, {"status": "ok"})
                return
            if self.path == prefix + "/env":
                # Lets a test confirm the manager's spawn env filter (build_spawn_env): only
                # names are returned, never values, even though this is a
                # test-only process.
                self._write_json(200, {"keys": sorted(os.environ.keys())})
                return
            self.send_response(404)
            self.end_headers()

        def do_POST(self):
            if self.path != prefix + "/inference":
                self.send_response(404)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            if crash_on_inference:
                # Die before answering: the client sees a dropped connection,
                # exactly like a real whisper-server crash mid-request.
                os._exit(1)
            if inference_delay:
                time.sleep(inference_delay)   # a request still in flight
            ctype = self.headers.get("Content-Type", "")
            boundary_match = re.search(r"boundary=([^;]+)", ctype)
            boundary = boundary_match.group(1).strip() if boundary_match else ""
            fields, file_bytes = _parse_multipart(body, boundary)
            lang_in = fields.get("language", "")
            lang_out = _LANG_NAMES.get(lang_in, lang_in or "unknown")
            # The leading space is deliberate: it lets a test prove the real
            # client applies .strip() to whatever the server answers, rather
            # than just passing the JSON through untouched.
            self._write_json(200, {"text": f" hello {lang_out} {len(file_bytes)}", "language": lang_out})

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    try:
        server.serve_forever(poll_interval=0.05)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
