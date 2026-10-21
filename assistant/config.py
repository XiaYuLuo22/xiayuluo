from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    api_key: str = ""
    api_base: str = "https://api.openai.com/v1"
    model: str = "gpt-4.1-mini"
    codex_workspace: str = ""
    codex_executable: str = "codex"
    codex_sandbox: str = "read-only"
    desktop_enabled: bool = False
    port: int = 8765
    data_dir: str = ".assistant"
    mobile_enabled: bool = False
    chat_provider: str = "auto"
    approval_mode: str = "auto"
    voice_autostart: bool = True

    @classmethod
    def load(cls, env_file=None):
        # Only read the selected project's .env, never search parent directories.
        load_dotenv(Path(env_file) if env_file is not None else Path.cwd() / ".env")
        settings = cls(
            api_key=os.getenv("ASSISTANT_MODEL_KEY") or os.getenv("OPENAI_API_KEY", ""),
            api_base=os.getenv("OPENAI_BASE_URL", cls.api_base).rstrip("/"),
            model=os.getenv("OPENAI_MODEL", cls.model),
            codex_workspace=os.getenv("CODEX_WORKSPACE", ""),
            codex_executable=os.getenv("CODEX_EXECUTABLE", "codex"),
            codex_sandbox=os.getenv("CODEX_SANDBOX", "read-only"),
            desktop_enabled=os.getenv("DESKTOP_ENABLED", "false").lower() == "true",
            port=int(os.getenv("ASSISTANT_PORT", "8765")),
            data_dir=os.getenv("ASSISTANT_DATA_DIR", ".assistant"),
            mobile_enabled=os.getenv("MOBILE_ENABLED", "false").lower() == "true",
            chat_provider=os.getenv("CHAT_PROVIDER", "auto").strip().lower(),
            approval_mode=os.getenv("APPROVAL_MODE", "auto").strip().lower(),
            voice_autostart=os.getenv("VOICE_AUTOSTART", "true").strip().lower() == "true",
        )
        url = urlsplit(settings.api_base)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username:
            raise ValueError("OPENAI_BASE_URL 必须是 HTTP(S) 服务地址，不能包含凭据。")
        if url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("远程模型接口必须使用 HTTPS。")
        if settings.codex_sandbox not in {"read-only", "workspace-write"}:
            raise ValueError("CODEX_SANDBOX 只能为 read-only 或 workspace-write。")
        if settings.chat_provider not in {"auto", "api", "codex"}:
            raise ValueError("CHAT_PROVIDER 只能为 auto、api 或 codex。")
        if settings.approval_mode not in {"auto", "manual"}:
            raise ValueError("APPROVAL_MODE 只能为 auto 或 manual。")
        if not 1024 <= settings.port <= 65535:
            raise ValueError("ASSISTANT_PORT 必须在 1024–65535 之间。")
        return settings
