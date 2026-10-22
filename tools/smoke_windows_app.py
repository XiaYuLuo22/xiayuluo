"""Start the actual packaged backend in isolated storage, without chat or devices."""
import os
from pathlib import Path
import socket
import subprocess
from tempfile import TemporaryDirectory
import time

import httpx


def smoke(command, expected_version, *, timeout=30):
    if not command or isinstance(command, (str, bytes)):
        raise ValueError("Smoke command must be an argument list.")
    with TemporaryDirectory(prefix="xiayuluo-packaged-smoke-") as temporary:
        directory = Path(temporary)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        environment = {name: value for name, value in os.environ.items()
                       if not name.endswith(("API_KEY", "MODEL_KEY"))}
        environment.update(ASSISTANT_DATA_DIR=str(directory / "memory"), ASSISTANT_PORT=str(port),
            MOBILE_ENABLED="false", DESKTOP_ENABLED="false", VOICE_AUTOSTART="false",
            APPROVAL_MODE="manual", CHAT_PROVIDER="codex", CODEX_WORKSPACE="",
            CODEX_EXECUTABLE=str(directory / "disabled-codex"),
            OPENAI_BASE_URL="https://api.openai.com/v1", OPENAI_MODEL="gpt-4.1-mini")
        process = subprocess.Popen([*command, "--serve"], cwd=directory, env=environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            deadline = time.monotonic() + timeout
            base = f"http://127.0.0.1:{port}"
            with httpx.Client(trust_env=False, timeout=.5) as client:
                while time.monotonic() < deadline:
                    code = process.poll()
                    if code is not None:
                        raise RuntimeError(f"Packaged backend exited before readiness (code {code}).")
                    try:
                        root = client.get(base + "/")
                        root.raise_for_status()
                        response = client.get(base + "/api/bootstrap")
                        response.raise_for_status()
                        bootstrap = response.json()
                    except (httpx.HTTPError, ValueError):
                        time.sleep(.05)
                        continue
                    status = bootstrap.get("status", {})
                    runtime = status.get("runtime", {})
                    if "夏鱼洛" not in root.text or runtime.get("version") != expected_version:
                        raise RuntimeError("Packaged backend resources or version do not match the build.")
                    if (runtime.get("presentation") != "service" or status.get("mobile", {}).get("enabled") is not False
                            or status.get("desktop", {}).get("available") is not False
                            or status.get("model", {}).get("api_configured") is not False
                            or status.get("model", {}).get("codex_connection", {}).get("checked_at") is not None):
                        raise RuntimeError("Packaged backend smoke isolation checks failed.")
                    token = bootstrap.get("token")
                    if not isinstance(token, str) or len(token) < 20:
                        raise RuntimeError("Packaged backend bootstrap is incomplete.")
                    voice = client.get(base + "/api/voice", headers={"X-Assistant-Token": token})
                    voice.raise_for_status()
                    if voice.json().get("running") is not False:
                        raise RuntimeError("Packaged backend unexpectedly started voice listening.")
                    if process.poll() is not None:
                        raise RuntimeError("Packaged backend stopped during readiness checks.")
                    return {"version": expected_version, "voice_running": False, "api_configured": False}
            raise RuntimeError("Packaged backend did not become ready within the smoke timeout.")
        finally:
            # Popen owns this exact child. Never kill another app by name or port.
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
