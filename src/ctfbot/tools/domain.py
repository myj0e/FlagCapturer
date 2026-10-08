"""Domain helpers built on the same isolated command runtime and evidence store."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import PurePosixPath
from typing import Any

from ctfbot.model_adapters.protocol import ToolReply, ToolSpec
from ctfbot.packs.catalog import CATEGORIES, catalog, playbook, triage
from ctfbot.packs.scripts import ANALYZE, ENVIRONMENT
from ctfbot.tools.registry import ToolOutcome


def _relative(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 512 or "\x00" in value or "\\" in value:
        raise ValueError("path must be a bounded relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or value == ".":
        raise PermissionError("path must stay inside its runtime root")
    return path.as_posix()


def install_domain_tools(registry) -> None:
    definitions = registry._definitions
    saved: dict[str, dict[str, Any]] = {}
    packs = catalog()

    def command(argv):
        outcome = registry._run_command({"argv": argv})
        try:
            content = json.loads(outcome.reply.content)
            analysis = json.loads(content.get('stdout', ''))
            if isinstance(analysis, dict) and isinstance(analysis.get('coverage'), dict):
                outcome = ToolOutcome(outcome.reply, {**outcome.result, 'coverage': analysis['coverage']})
        except (ValueError, TypeError, AttributeError):
            pass
        if outcome.result.get("exit_code") in {126, 127}:
            result = {**outcome.result, "status": "environment_error", "required_binary": argv[0]}
            registry.evidence.append("workflow_dependency_missing", required_binary=argv[0], result=result)
            return ToolOutcome(outcome.reply, result)
        return outcome

    def reply(payload: dict[str, Any], *, success: bool = True) -> ToolOutcome:
        data = json.dumps(payload, ensure_ascii=True).encode()
        if len(data) > registry.max_model_output_bytes:
            ref = registry.evidence.write_artifact(data, media_type="application/json")
            content = json.dumps({"truncated": True, "evidence": ref})
        else:
            content = data.decode()
        return ToolOutcome(ToolReply(content, success), {"status": "ok" if success else "environment_error"})

    def define(name, description, properties, handler):
        definitions[name] = (ToolSpec(name, description, {
            "type": "object", "properties": properties, "required": list(properties), "additionalProperties": False,
        }), handler)

    enum = {"type": "string", "enum": list(CATEGORIES)}
    path_schema = {"type": "string", "maxLength": 512}

    def discover(args):
        routing = triage(registry.challenge_root)
        registry.evidence.append("domain_routing", **routing)
        return reply({"packs": packs, "triage": routing})

    define("workflow_discover", "List versioned workflows and bounded multi-label triage hints. Classification never changes network authority.", {}, discover)

    def read(args):
        name = args["pack"]
        content = playbook(name)
        ref = registry.evidence.write_artifact(content.encode(), media_type="text/markdown")
        registry.evidence.append("workflow_read", pack=name, version=packs[name]["version"], evidence=ref)
        return reply({"pack": name, "metadata": packs[name], "playbook": content, "evidence": ref})

    define("workflow_read", "Read one domain playbook, dependencies, workflow operations and limitations.", {"pack": enum}, read)

    def environment(args):
        name = args["pack"]
        if name not in packs:
            raise ValueError("unknown pack")
        return command(["python3", "-c", ENVIRONMENT,
                        *packs[name]["required_binaries"], *packs[name]["optional_binaries"]])

    define("workflow_environment", "Check required/optional binaries and Python modules inside the current sandbox; never installs software.", {"pack": enum}, environment)

    def run(args):
        name, operation = args["pack"], args["operation"]
        if name not in packs or operation not in packs[name]["operations"]:
            raise ValueError("unsupported pack/operation; inspect workflow_read")
        relative = _relative(args["path"])
        registry._checked_file(registry.challenge_root, relative)
        argv = ["objdump", "-d", f"/challenge/{relative}"] if operation == "disassemble" else [
            "python3", "-c", ANALYZE, name, operation, relative]
        registry.evidence.append("workflow_execution", pack=name, version=packs[name]["version"],
                                 operation=operation, input_path=relative,
                                 wrapper_sha256=hashlib.sha256(ANALYZE.encode()).hexdigest())
        return command(argv)

    define("workflow_run", "Run one documented bounded workflow against a read-only challenge path. Check input format first: crypto/rsa requires UTF-8 JSON n/e/c, elf requires ELF bytes, archive requires ZIP, response requires raw HTTP. Labels and filenames are heuristic; core tools remain available for mixed inputs. Returns raw evidence and coverage; verification is separate.", {
        "pack": enum, "operation": {"type": "string", "maxLength": 32}, "path": path_schema,
    }, run)

    def save(args):
        path, source = _relative(args["path"]), args["source"]
        if not path.endswith(".py") or not isinstance(source, str) or len(source.encode()) > 8192:
            raise ValueError("save requires a .py path and at most 8 KiB of Python source")
        if path in saved:
            raise ValueError("use a new path for a revised script")
        data = source.encode()
        artifact = registry.evidence.write_artifact(data, media_type="text/x-python")
        writer = ("import base64,os,pathlib,sys; root=pathlib.Path('/work'); p=root/sys.argv[1]; "
                  "assert p.parent.resolve().is_relative_to(root); p.parent.mkdir(parents=True,exist_ok=True); "
                  "assert p.parent.resolve().is_relative_to(root) and not p.is_symlink(); "
                  "fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600); "
                  "f=os.fdopen(fd,'wb'); f.write(base64.b64decode(sys.argv[2],validate=True)); f.close()")
        outcome = registry._run_command({"argv": ["python3", "-c", writer, path, base64.b64encode(data).decode()]})
        if outcome.result.get("exit_code") != 0:
            return outcome
        saved[path] = artifact
        registry.evidence.append("script_saved", path=path, source=artifact)
        return reply({"path": path, "source": artifact})

    define("script_save", "Save a bounded Python solve script as a new /work file and private source artifact. Revisions use a new filename. For expensive tasks print flushed progress every 5-10s, checkpoint and exit before the command deadline; use resumable batches. Output does not extend the hard timeout.", {
        "path": path_schema, "source": {"type": "string", "maxLength": 8192},
    }, save)

    def script_argv(args):
        path, argv = _relative(args["path"]), args["argv"]
        if path not in saved or not isinstance(argv, list) or len(argv) > 16 or any(
            not isinstance(arg, str) or len(arg) > 512 or "\x00" in arg for arg in argv
        ):
            raise ValueError("run requires a saved script and at most 16 bounded arguments")
        loader = ("import hashlib,pathlib,sys; p=pathlib.Path('/work')/sys.argv[1]; "
                  "assert p.resolve().is_relative_to('/work') and not p.is_symlink(); data=p.read_bytes(); "
                  "assert hashlib.sha256(data).hexdigest()==sys.argv[2]; sys.argv=[str(p),*sys.argv[3:]]; "
                  "exec(compile(data,str(p),'exec'),{'__name__':'__main__','__file__':str(p)})")
        registry.evidence.append("script_execution", path=path, source=saved[path], argv=argv)
        return ["python3", "-u", "-c", loader, path, saved[path]["sha256"], *argv]

    def script_run(args):
        return registry._run_command({"argv": script_argv(args)})

    define("script_run", "Run previously saved script bytes after checking their SHA-256; network policy and tool budget are unchanged.", {
        "path": path_schema, "argv": {"type": "array", "items": {"type": "string", "maxLength": 512}, "maxItems": 16},
    }, script_run)

    if callable(getattr(registry.runtime, "start_script_session", None)):
        define("script_start", "Start a saved, hash-checked Python script as a background session without the 120s command limit. Returns session_id immediately. Use session_read (elapsed_seconds/output_idle_seconds) to assess progress and session_close to stop only this script. Silence alone does not kill it; the remaining run budget still applies. Print flushed progress every 5-10s and checkpoint in /work.", {
            "path": path_schema, "argv": {"type": "array", "items": {"type": "string", "maxLength": 512}, "maxItems": 16},
        }, lambda args: registry._session_start({"argv": script_argv(args)}, background=True))

    def export(args):
        path = _relative(args["path"])
        parents = args["parents"]
        if not isinstance(parents, list) or not 1 <= len(parents) <= 16:
            raise ValueError("export requires 1 to 16 challenge input lineage paths")
        lineage = []
        for name in parents:
            source = registry._checked_file(registry.challenge_root, _relative(name))
            lineage.append({"input_path": name, "sha256": hashlib.sha256(source.read_bytes()).hexdigest()})
        reader = ("import base64,json,pathlib,sys; p=pathlib.Path('/work')/sys.argv[1]; "
                  "assert p.resolve().is_relative_to('/work') and not p.is_symlink() and p.is_file(); "
                  "assert p.stat().st_size<=262144; data=p.read_bytes(); assert len(data)<=262144; "
                  "print(json.dumps({'data_base64':base64.b64encode(data).decode()}))")
        outcome = registry._run_command({"argv": ["python3", "-c", reader, path]})
        if outcome.result.get("exit_code") != 0 or outcome.result.get("truncated"):
            return outcome
        ref = outcome.result["stdout_evidence"]
        payload = json.loads((registry.evidence.run_dir / ref["artifact"]).read_bytes())
        data = base64.b64decode(payload["data_base64"], validate=True)
        if len(data) > 262144:
            raise ValueError("export exceeds 256 KiB")
        artifact = registry.evidence.write_artifact(data)
        registry.evidence.append("artifact_lineage", runtime_path=path, artifact=artifact, parents=lineage)
        return reply({"artifact": artifact, "parents": lineage})

    define("artifact_export", "Export up to 256 KiB from /work into private evidence with declared challenge-input lineage; never reads controller paths.", {
        "path": path_schema, "parents": {"type": "array", "items": path_schema, "minItems": 1, "maxItems": 16},
    }, export)

    remote = getattr(registry.runtime, "remote_spec", None)
    memory = registry.memory_view
    if memory is not None and memory.namespaces:
        def search_memory(args):
            query = args["query"]
            if not isinstance(query, str) or len(query) > 256:
                raise ValueError("memory query must be a bounded string")
            matches = [item for item in memory.entries.values() if query.lower() in item["body"].lower()
                       or any(query.lower() in tag.lower() for tag in item["tags"])]
            fields = ("id", "namespace", "tags", "source_url", "license", "contamination", "body_sha256")
            return reply({"mode": memory.mode, "matches": [{key: item[key] for key in fields} for item in matches[:10]]})

        def read_memory(args):
            identity = args["id"]
            if not isinstance(identity, str) or identity not in memory.entries:
                raise PermissionError("memory version is outside this run's authorized snapshot")
            item = memory.entries[identity]
            artifact = registry.evidence.write_artifact(item["body"].encode(), media_type="text/markdown")
            registry.evidence.append("memory_used", memory_id=identity, namespace=item["namespace"],
                                     source_url=item["source_url"], license=item["license"], evidence=artifact)
            public_fields = ("id", "namespace", "body", "body_sha256", "source_url", "license", "tags", "contamination", "imported_at")
            return reply({"entry": {key: item[key] for key in public_fields}, "evidence": artifact})

        define("memory_search", "Search only this run's explicitly authorized reviewed memory snapshot; no external search or automatic writes.", {
            "query": {"type": "string", "maxLength": 256},
        }, search_memory)
        define("memory_read", "Read one approved memory version with provenance and license; unauthorized namespaces are inaccessible.", {
            "id": {"type": "string", "maxLength": 64},
        }, read_memory)
    if remote is not None:
        def http_request(args):
            method, path, encoded = args["method"], args["path"], args["body_base64"]
            if method not in {"GET", "HEAD", "POST"} or not isinstance(path, str) or len(path) > 2048:
                raise ValueError("HTTP supports GET/HEAD/POST and a bounded origin path")
            if not path.startswith("/") or path.startswith("//") or any(ord(char) < 33 or ord(char) > 126 for char in path) or "\\" in path:
                raise PermissionError("HTTP path must be an ASCII origin path without controls or authority")
            if not isinstance(encoded, str) or len(encoded) > 10924:
                raise ValueError("HTTP body exceeds 8 KiB")
            body = base64.b64decode(encoded, validate=True)
            if len(body) > 8192 or method in {"GET", "HEAD"} and body:
                raise ValueError("only POST accepts an HTTP body")
            host = f"[{remote.host}]" if ":" in remote.host else remote.host
            request = (f"{method} {path} HTTP/1.1\r\nHost: {host}:{remote.port}\r\n"
                       f"Connection: close\r\nContent-Type: application/octet-stream\r\nContent-Length: {len(body)}\r\n\r\n").encode("ascii") + body
            result = registry.runtime.remote_exchange(remote.host, remote.port, "tcp", request,
                                                       timeout=min(registry.command_timeout, 30), maximum_bytes=16384)
            response = result["data"]
            raw = registry.evidence.write_artifact(response, media_type="application/http")
            request_ref = registry.evidence.write_artifact(request, media_type="application/http")
            head, separator, content = response.partition(b"\r\n\r\n")
            if not separator or not head.startswith(b"HTTP/") or len(head) > 8192:
                return reply({"status": "invalid_or_incomplete_http", "transport_status": result["status"],
                              "response_evidence": raw, "request_evidence": request_ref}, success=False)
            status_line = head.split(b"\r\n", 1)[0].decode("latin1")
            headers = [line.decode("latin1") for line in head.split(b"\r\n")[1:]][:100]
            payload = {"status_line": status_line, "headers": headers,
                       "body_preview": content[:2048].decode("utf-8", errors="replace"),
                       "body_sha256": hashlib.sha256(content).hexdigest(), "body_bytes_received": len(content),
                       "transport_status": result["status"], "redirect_followed": False,
                       "body_decoding": "raw; no compression or transfer decoding",
                       "response_evidence": raw, "request_evidence": request_ref,
                       "coverage": {"source_kind": "controlled_request", "received_bytes": len(response),
                                    "body_preview_bytes": min(len(content),2048), "filter": "first 2048 body bytes; raw transfer encoding",
                                    "truncated": len(content)>2048 or len(response)>=16384,
                                    "transport_status": result['status']}}
            registry.evidence.append("http_response", method=method, path=path, status_line=status_line,
                                     response=raw, request=request_ref, redirect_followed=False,
                                     transport_status=result["status"])
            return reply(payload, success=result["status"] != "timed_out")

        define("http_request", "One plaintext HTTP request to the fixed controller-granted TCP endpoint. No redirects, TLS, proxy, cookie persistence or arbitrary headers; raw response evidence is retained.", {
            "method": {"type": "string", "enum": ["GET", "HEAD", "POST"]},
            "path": {"type": "string", "maxLength": 2048},
            "body_base64": {"type": "string", "maxLength": 10924},
        }, http_request)
