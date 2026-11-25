"""Starts the llama-server of model.toml, checks everything before using it, and always stops it."""
import hashlib
import json
import socket
import subprocess
import sys
import time
from datetime import datetime
from os import makedirs
from os.path import getmtime, getsize, isfile, join

import httpx


class ModelError(Exception):
    pass


# ---- checks before starting ----

def sha256_cached(path, cache_file, log=print):
    """The gguf weighs gigabytes: the hash is kept per (size, mtime) and only recomputed if the file changes."""
    key = f"{path}|{getsize(path)}|{getmtime(path)}"
    cache = json.load(open(cache_file, encoding="utf-8")) if isfile(cache_file) else {}
    if key not in cache:
        log(f"checking the sha256 of {path} (only the first time)...")
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(1 << 24), b""):
                h.update(block)
        cache[key] = h.hexdigest()
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=1)
    return cache[key]


def missing_options(exe, args):
    """Options in args that this llama-server doesn't have, checked against its --help."""
    r = subprocess.run([exe, "--help"], capture_output=True, text=True, errors="replace", timeout=60)
    help_text = r.stdout + r.stderr
    flags = [a for a in args if a.startswith("-") and not a.lstrip("-").replace(".", "").isdigit()]
    return [f for f in flags if f" {f}" not in help_text and f",{f}" not in help_text and f"\n{f}" not in help_text]


def port_busy(host, port):
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) == 0


def other_llama_servers():
    if sys.platform == "win32":
        r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq llama-server.exe", "/FO", "CSV", "/NH"],
                           capture_output=True, text=True, errors="replace")
        return [l for l in r.stdout.splitlines() if "llama-server" in l.lower()]
    r = subprocess.run(["pgrep", "-a", "-x", "llama-server"], capture_output=True, text=True)
    return r.stdout.splitlines()


_VRAM_PS = (r"$c = Get-Counter '\GPU Process Memory(*)\Dedicated Usage' -ErrorAction SilentlyContinue; "
            r"$c.CounterSamples | Where-Object { $_.CookedValue -gt 20MB } | ForEach-Object { "
            r"$n = if ($_.InstanceName -match 'pid_(\d+)') { [int]$Matches[1] } else { 0 }; "
            r"'{0}|{1}' -f [int]($_.CookedValue/1MB), (Get-Process -Id $n -ErrorAction SilentlyContinue).ProcessName }")


def other_vram_mb():
    """(total MB, [(MB, process)]) of dedicated gpu memory used by other programs. None outside windows."""
    if sys.platform != "win32":
        return None, []
    r = subprocess.run(["powershell", "-NoProfile", "-Command", _VRAM_PS], capture_output=True, text=True,
                       errors="replace", timeout=60)
    used = []
    for line in r.stdout.splitlines():
        mb, _, name = line.strip().partition("|")
        if mb.isdigit() and name.lower() != "llama-server":
            used.append((int(mb), name or "?"))
    return sum(mb for mb, _ in used), sorted(used, reverse=True)


def preflight(m, data_dir, log=print):
    """Every reason not to start, all together. Empty list = go."""
    srv, guards, problems = m["server"], m.get("guards", {}), []
    if not isfile(srv["exe"]):
        problems.append(f"llama-server not found: {srv['exe']}")
    if not isfile(srv["model"]):
        problems.append(f"model not found: {srv['model']}")
    if problems:
        return problems
    makedirs(data_dir, exist_ok=True)
    got = sha256_cached(srv["model"], join(data_dir, "model_hashes.json"), log)
    if got != srv["sha256"].lower():
        problems.append(f"sha256 of the model doesn't match model.toml: got {got}")
    bad = missing_options(srv["exe"], srv["args"])
    if bad:
        problems.append(f"this llama-server doesn't have: {' '.join(bad)}")
    if port_busy(srv.get("host", "127.0.0.1"), srv["port"]):
        problems.append(f"port {srv['port']} is already in use")
    if guards.get("refuse_if_other_llama_server", True) and other_llama_servers():
        problems.append("another llama-server is running (Hermes?): the gpu is busy")
    limit = guards.get("max_other_vram_mb", 0)
    if limit:
        total, used = other_vram_mb()
        if total is not None and total > limit:
            problems.append(f"other programs use {total} MB of the gpu (max {limit}): "
                            + ", ".join(f"{n} {mb}" for mb, n in used[:6]))
    return problems


# ---- the server ----

class Model:
    """with Model(settings) as llm: llm.chat(...). Stops the server on the way out, whatever happens."""

    def __init__(self, settings, log=print):
        self.m, self.log = settings.model, log
        self.logs = join(settings.data_dir, "logs")
        self.proc = None
        srv = self.m["server"]
        self.url = f"http://{srv.get('host', '127.0.0.1')}:{srv['port']}"
        self.tag = f"{self.m.get('name', 'model')}@{srv['sha256'][:12]}"

    def __enter__(self):
        problems = preflight(self.m, self.logs, self.log)
        if problems:
            raise ModelError("the model can't start:\n  - " + "\n  - ".join(problems))
        srv = self.m["server"]
        cmd = [srv["exe"], "-m", srv["model"], "--host", srv.get("host", "127.0.0.1"), "--port", str(srv["port"])]
        cmd += srv["args"]
        self.log_file = join(self.logs, f"llama-server_{datetime.now():%Y%m%d_%H%M%S}.log")
        self.log(f"starting llama-server ({self.m.get('name')}), log in {self.log_file}")
        self._out = open(self.log_file, "w", encoding="utf-8", errors="replace")
        self.proc = subprocess.Popen(cmd, stdout=self._out, stderr=subprocess.STDOUT)
        try:
            self._wait(srv.get("startup_timeout_s", 300))
        except Exception:
            self.__exit__(None, None, None)
            raise
        return self

    def _wait(self, limit):
        t0 = time.monotonic()
        while time.monotonic() - t0 < limit:
            if self.proc.poll() is not None:
                raise ModelError(f"llama-server ended by itself (code {self.proc.returncode}): see {self.log_file}")
            try:
                if httpx.get(f"{self.url}/health", timeout=5).status_code == 200:
                    self.log(f"model ready in {time.monotonic() - t0:.0f} s")
                    return
            except httpx.HTTPError:
                pass
            time.sleep(1)
        raise ModelError(f"llama-server didn't answer in {limit} s: see {self.log_file}")

    def __exit__(self, *exc):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        if getattr(self, "_out", None):
            self._out.close()

    def params(self):
        """Everything that shapes an answer, saved next to each summary."""
        return {"model": self.tag, "args": self.m["server"]["args"], "sampling": self.m["sampling"],
                "max_tokens": self.m.get("output", {}).get("max_tokens", 700)}

    def chat(self, messages, schema, name="answer"):
        """One request with ALL the sampling params and a forced json schema. Returns the parsed json."""
        out = self.m.get("output", {})
        if out.get("structured", "json_schema") == "json_schema":
            fmt = {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}}
        else:
            fmt = {"type": "json_object", "schema": schema}
        body = dict(self.m["sampling"], messages=messages, max_tokens=out.get("max_tokens", 700),
                    response_format=fmt, cache_prompt=True)
        r = httpx.post(f"{self.url}/v1/chat/completions", json=body,
                       timeout=self.m["server"].get("request_timeout_s", 300))
        if r.status_code != 200:
            raise ModelError(f"llama-server answered {r.status_code}: {r.text[:300]}")
        d = r.json()
        t = d.get("timings") or {}
        floor = self.m.get("guards", {}).get("min_prompt_tps", 0)
        if floor and (t.get("prompt_n") or 0) >= 500 and (t.get("prompt_per_second") or floor) < floor:
            raise ModelError(f"prompt read at {t['prompt_per_second']:.0f} tokens/s (min {floor}): "
                             "the gpu is spilling into ram. Close what's using it and run again")
        choice = d["choices"][0]
        if choice.get("finish_reason") == "length":
            raise ModelError("the answer hit max_tokens: raise output.max_tokens in model.toml")
        try:
            return json.loads(choice["message"]["content"])
        except (json.JSONDecodeError, TypeError) as e:
            raise ModelError(f"the answer is not the json asked for ({e}): {str(choice['message'])[:200]}")
