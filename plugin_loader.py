"""
插件加载器 - 自动发现并加载 plugins/ 目录下的所有插件
"""

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

import plugin_base


class PluginLoader:
    def __init__(self, plugins_dir: str | Path | None = None):
        self.plugins_dir = Path(plugins_dir or os.path.join(os.getcwd(), "plugins"))
        self.loaded_plugins: dict[str, Any] = {}
        self.load_errors: list[dict[str, Any]] = []

    def load_all(self) -> dict[str, Any]:
        """加载 plugins/ 目录下所有 .py 文件"""
        if not self.plugins_dir.exists():
            self.plugins_dir.mkdir(parents=True, exist_ok=True)
            return {}

        self.load_errors.clear()

        for file_path in sorted(self.plugins_dir.glob("*.py")):
            if file_path.name.startswith("_"):
                continue

            try:
                module = self._load_module(file_path)
                self.loaded_plugins[file_path.stem] = module
                print(f"[PluginLoader] Loaded: {file_path.name}")
            except Exception as exc:
                error_info = {
                    "file": file_path.name,
                    "error": str(exc),
                }
                self.load_errors.append(error_info)
                print(f"[PluginLoader] Failed to load {file_path.name}: {exc}")

        return self.loaded_plugins

    def _load_module(self, file_path: Path) -> Any:
        """动态加载单个模块"""
        module_name = f"plugins.{file_path.stem}"

        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load spec for {file_path}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module

    def reload_all(self) -> dict[str, Any]:
        """重新加载所有插件"""
        plugin_base.clear_registry()
        self.loaded_plugins.clear()
        return self.load_all()

    def get_status(self) -> dict[str, Any]:
        """获取插件加载状态"""
        return {
            "plugins_dir": str(self.plugins_dir),
            "loaded_count": len(self.loaded_plugins),
            "loaded_plugins": list(self.loaded_plugins.keys()),
            "errors": self.load_errors,
            "commands_count": len(plugin_base.get_registered_commands()),
            "handlers_count": len(plugin_base.get_message_handlers()),
        }
