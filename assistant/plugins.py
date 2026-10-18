"""Plugin contract. Register new plugins in build_registry; no UI rewrite needed."""
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel


class Plugin(Protocol):
    name: str
    description: str
    title: str
    arguments: type[BaseModel]

    def available(self) -> tuple[bool, str]: ...
    async def execute(self, arguments: BaseModel) -> dict: ...


@dataclass
class Registry:
    plugins: dict[str, Plugin]

    def register(self, plugin: Plugin):
        if plugin.name in self.plugins:
            raise ValueError(f"Duplicate plugin: {plugin.name}")
        self.plugins[plugin.name] = plugin

    def catalog(self):
        return [{"name": plugin.name, "title": plugin.title, "description": plugin.description,
                 "available": plugin.available()[0], "reason": plugin.available()[1],
                 "parameters": plugin.arguments.model_json_schema()}
                for plugin in self.plugins.values()]

    def model_tools(self):
        return [{"type": "function", "function": {"name": plugin.name, "description": plugin.description,
                "parameters": plugin.arguments.model_json_schema()}}
                for plugin in self.plugins.values() if plugin.available()[0]]

    def validate(self, name, arguments):
        plugin = self.plugins.get(name)
        if not plugin:
            raise ValueError("未知插件")
        available, reason = plugin.available()
        if not available:
            raise ValueError(reason)
        return plugin.arguments.model_validate(arguments)
