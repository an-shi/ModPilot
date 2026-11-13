# 新增游戏适配器

核心原则：**游戏专属逻辑只能出现在 `modpilot/adapters/<game>/`**。`services/` 中的通用逻辑不得出现特定游戏的目录名、manifest 字段或日志格式。

## 1. 实现接口

创建 `modpilot/adapters/<game>/adapter.py`，继承 `GameAdapter`：

```python
from pathlib import Path
from modpilot.adapters.base import AdapterCapabilities, GameAdapter

class MyGameAdapter(GameAdapter):
    key = "my_game"
    name = "My Game"
    description = "Short description"
    nexus_domain = "mygame"
    steam_app_id = "123456"
    capabilities = AdapterCapabilities(
        scan=True,
        install=True,
        enable_disable=True,
        dependencies=True,
        log_parsing=True,
        environment_check=True,
        translation=False,
        conflict_rules=True,
        game_version=True,
    )

    def detect_installations(self): ...
    def validate_installation(self, path: Path): ...
    def resolve_mods_dir(self, install_path: Path) -> Path: ...
    def scan_mods(self, install_path, progress=None): ...
    def plan_install(self, archive_path, staging_dir, mods_root): ...
    def set_enabled(self, mods_root, relative_path, enabled) -> str: ...
```

必须遵守：

- `scan_mods` 返回每个已安装 Mod 的 `ScannedMod`，包括禁用项；损坏项用 `metadata_fallback` 表示，而不是静默忽略。
- `plan_install` 接收已经安全解压到 `staging_dir` 的内容，返回 `InstallPlan`。不要把压缩包路径直接拼进游戏目录。
- 目标目录必须位于 `mods_root` 内；核心安装器还会二次校验。
- `set_enabled` 必须无内容修改、可逆，并返回新的相对路径。不要删除文件。
- 所有异常使用 `AdapterError` 或带用户可读中文信息的异常。
- 如果某能力未实现，将 `capabilities` 对应项设为 `False`，不要返回伪造数据。

## 2. 注册适配器

在 `modpilot/app_services.py`：

```python
from .adapters.my_game.adapter import MyGameAdapter

self.registry.register(MyGameAdapter())
```

UI 的“游戏管理”会自动列出适配器信息。

## 3. 元数据与依赖

将游戏 manifest 转成统一 `ModMetadata`：

- `unique_id`、`name`、`author`、`version`、`description`
- `nexus_mod_id`（如果 manifest 含 Nexus 标识）
- `dependencies: list[ModDependencyInfo]`（必需/可选、最低版本）
- `kind`：代码 Mod / 内容包 / 框架
- `manifest_path`、`extra`

核心依赖分析只使用这些字段，不解析游戏特定文件。

## 4. 日志解析

实现：

```python
def find_logs(self, install_path: Path) -> list[Path]: ...
def parse_log(self, log_path: Path) -> ParsedLog: ...
```

`ParsedLog` 统一包含：

- 游戏与框架版本
- 已加载 Mod、被跳过 Mod
- `LogIssue` 列表（严重级别、类别、涉及 Mod、缺失依赖、原始行、行号、堆栈）

只解析日志明确表达的内容。**不得因为多个 Mod 出现在同一行就推断冲突**；冲突结论必须带明确证据或已知问题规则。

## 5. 翻译

如果游戏有标准翻译覆盖机制，实现：

```python
def is_translatable(self, metadata, mod_dir) -> bool: ...
def is_translation_applied(self, mod_dir, language="zh") -> bool: ...
def build_translation_plan(self, mod_dir, language="zh") -> TranslationPlan | None: ...
def validate_translation(self, plan, translations) -> list[str]: ...
def write_translation(self, plan, translations) -> TranslationApplyResult: ...
```

要求：

- 使用游戏/框架官方覆盖机制，优先新增语言文件，不直接改原始文件。
- `validate_translation` 必须检查结构、键集合、占位符、编码/格式。
- `write_translation` 写失败时抛异常；服务层会从备份恢复。
- 如果没有官方机制，先设计并记录独立补丁方案，再实现；不要把星露谷的 `i18n/` 规则复制到其他游戏。

## 6. 已知问题规则

实现 `load_known_issue_rules()`，返回规则列表。建议数据文件格式：

```json
{
  "rules": [
    {
      "id": "mygame.known.issue.1",
      "title": "A 与 B 在 X 版本前不兼容",
      "description": "来源说明",
      "severity": "warning",
      "certainty": "known",
      "category": "compatibility",
      "match_unique_ids": ["Author.A", "Author.B"],
      "max_versions": {"Author.B": "1.2.0"},
      "recommendation": "更新 B 到 1.2.1+",
      "source": "https://public-source.example/..."
    }
  ]
}
```

只添加可公开验证的规则；没有来源就不要写。

## 7. 测试规范

在 `tests/` 增加：

- 元数据解析（有效/无效/边界字段）
- 扫描（启用、禁用、嵌套、损坏）
- 安装计划（包装目录、根 manifest、非法压缩包）
- 启停往返
- 日志解析（缺失依赖、版本错误、堆栈）
- 翻译计划与占位符校验
- 真实临时目录上的安装/卸载/备份恢复
- 若支持翻译，测试写回后结构仍有效

测试禁止写入真实游戏目录。可参考 `tests/conftest.py` 的 `stardew` fixture 和 `tests/test_adapter.py`。

## 8. 不应做的事

- 不要在 `ModService` 中判断 `manifest.json`。
- 不要在 `BackupService` 中识别 `.esp` 或 SMAPI 日志。
- 不要为适配器新增游戏专属数据库表；先使用 `metadata_json`，确需关系型数据时再设计通用字段。
- 不要用模拟数据伪装已完成能力。未实现项写入 `capabilities` 和文档，并在 UI 中明确禁用或标注。
