"""Local, persistent application approval policy; never changes OS/CLI permissions."""
import json
import os
from pathlib import Path
import tempfile


class Automation:
    def __init__(self, data_dir, default="auto"):
        self.path = Path(data_dir) / "automation.json"
        self.mode = default
        self.error = ""
        if self.path.exists():
            try:
                if self.path.stat().st_size > 4096:
                    raise ValueError("Oversized policy")
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or data.get("mode") not in {"auto", "manual"}:
                    raise ValueError("Invalid policy")
                self.mode = data["mode"]
            except (OSError, ValueError):
                self.mode = "manual"
                self.error = "执行策略文件无效，已退回逐次确认；请在电脑设置中重新选择。"

    @property
    def automatic(self):
        return self.mode == "auto"

    def status(self):
        return {"mode": self.mode, "confirmation_required": not self.automatic,
                "scope": "configured-tools", "warning": self.error}

    def set_mode(self, mode):
        if mode not in {"auto", "manual"}:
            raise ValueError("执行方式只能为 auto 或 manual。")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.path.parent,
                                             prefix="automation-", suffix=".tmp", delete=False) as output:
                temporary = Path(output.name)
                json.dump({"mode": mode}, output)
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(self.path)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
        self.mode, self.error = mode, ""
