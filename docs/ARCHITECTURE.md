# 架构设计

## 目标

ModPilot 是本地优先的 Windows 桌面 Mod 管理器。架构的第一约束是：**核心管理能力与游戏适配逻辑严格分离**。新增游戏原则上只新增适配器，不修改 `services/` 中的通用管理逻辑。

## 分层

```text
┌────────────────────────────────────────────────────────────┐
│ UI (PySide6)                                               │
│ pages / dialogs / widgets / theme / TaskManager            │
└─────────────────────────────┬──────────────────────────────┘
                              │ 调用同步服务，长任务放后台
┌─────────────────────────────▼──────────────────────────────┐
│ Services（业务编排，无 Qt 依赖）                            │
│ GameService · ModService · ProfileService · UpdateService  │
│ DownloadManager · TranslationEngine · DiagnosticEngine     │
│ RepairService · BackupService · ActivityService            │
└───────────────┬───────────────────────┬────────────────────┘
                │                       │
     ┌──────────▼──────────┐   ┌────────▼─────────────┐
     │ Adapters            │   │ Infrastructure       │
     │ GameAdapter 统一接口 │   │ SQLite / 文件 / 网络  │
     │ Stardew 实现         │   │ 备份 / 凭据 / 解压    │
     └─────────────────────┘   └──────────────────────┘
```

### UI 层

- 所有页面继承 `BasePage`。
- 业务动作通过 `TaskManager.run()` 在 `QThreadPool` 中执行；UI 回调通过 Qt 信号回到主线程。
- 页面不直接操作 SQLAlchemy 或文件系统，只调用服务。
- 主题 QSS 与 SVG 图标由代码生成，无外部 UI 资源依赖。

### 服务层

服务是同步 Python 类，不导入 PySide6，因此可被测试或未来 CLI 复用。核心服务：

| 服务 | 职责 |
|---|---|
| `GameService` | 游戏目录校验、环境检查、当前游戏、适配器查询 |
| `ModService` | 扫描、安装/卸载、启停、依赖视图、重复检测、回滚 |
| `ProfileService` | 配置档快照、应用、导入/导出 |
| `BackupService` | 统一创建/恢复/删除备份，记录数据库审计 |
| `DownloadManager` | 官方 Nexus 下载队列、进度、暂停/恢复/取消、校验 |
| `UpdateService` | 检查、更新策略、下载/安装、依赖保护、版本校验、回滚 |
| `TranslationEngine` | 三级汉化、AI 翻译、缓存、结构校验、写回 |
| `DiagnosticEngine` | 日志解析、静态规则、AI 证据过滤、报告持久化 |
| `RepairService` | 生成修复计划、执行、验证、回滚 |
| `ActivityService` | 用户可见审计事件 |

### 适配器层

`GameAdapter` 定义游戏专属能力：

- 安装目录探测与验证
- Mods 目录、扫描与元数据解析
- 安装映射（压缩包 → 目标目录）
- 启用/禁用机制
- 依赖与游戏环境
- 日志定位与解析
- 翻译计划、校验、写入
- 已知问题规则

核心代码只通过 `GameAdapter` 暴露的语义工作，不读取 `manifest.json`、不理解 SMAPI 日志、不假设 i18n 文件位置。详见 [ADDING_GAME.md](ADDING_GAME.md)。

### 国际化层

界面与业务层中的用户可见文案通过 `modpilot/i18n.py` 的 `tr()` 和稳定消息 ID 输出，源码不包含硬编码中文界面文案。完整目录位于 `modpilot/locales/zh_CN.json` 与 `modpilot/locales/en_US.json`，默认选中 `zh-CN`。启动时先读取配置并加载语言目录，再导入 UI，避免窗口构造时使用错误语言。语言切换保存在本地设置中，完整界面在下次启动时生效。详见 [I18N.md](I18N.md)。

### 基础设施层

- `core.archive`：ZIP/7Z/TAR.GZ 安全解压，拒绝绝对路径、`..`、符号链接、异常体积。
- `core.backup`：ZIP 内嵌 JSON 清单；恢复时先抽取到临时目录、逐项替换，失败回滚。
- `core.credentials`：Windows 凭据管理器优先，DPAPI 加密文件回退；密钥不进入配置。
- `core.text_tokens`：保护/恢复/校验占位符，防止翻译破坏变量。
- `core.paths`：安全路径拼接、Windows 文件名清洗、原子写入。

## 组合与依赖注入

`modpilot/app_services.py` 是组合根：

```text
AppPaths + ConfigStore + CredentialStore + Database
        ↓
AdapterRegistry ← StardewValleyAdapter
        ↓
Activity / Backup / NexusClient / Game / Mod / Profile
        ↓
DownloadManager → UpdateService
        ↓
LlmClient → TranslationEngine
        ↓
DiagnosticEngine → RepairService
        ↓
DashboardService
```

测试通过构造同样服务并注入临时数据库、临时目录和 `InMemorySecretBackend`，避免触碰真实凭据或游戏文件。

## 线程模型

- UI 主线程：界面渲染、服务查询、轻量刷新。
- `TaskManager`：`QThreadPool`，最多 4 个后台任务；进度通过信号回传。
- `DownloadManager`：两个常驻 daemon 工作线程 + `queue.Queue`。下载流中每 256 KB 检查取消/暂停标志；关闭应用时取消并把任务标记为暂停，避免进程无法退出。
- SQLite：WAL、`busy_timeout`、外键开启；每个服务方法使用独立 Session，避免跨线程共享 ORM 对象。
- 长任务不会从 worker 直接更新 QWidget；进度/完成/错误均通过 Qt 信号回主线程。

## 数据库

SQLite 表（`data/models.py`）：

- `games`：适配器 key、安装路径、Mods 路径、版本、Nexus domain
- `mods`：UniqueID、名称、作者、版本、启用状态、Nexus ID、更新策略、最新版本、锁定/忽略版本
- `mod_dependencies`：依赖 UniqueID、最低版本、必需/可选
- `profiles` / `profile_entries`：配置档与条目快照
- `backups`：备份路径、SHA-256、清单、恢复状态
- `downloads`：队列状态、进度、目标、期望 MD5、归档路径
- `update_records`：更新历史、策略、结果、备份
- `translations`：逐条原文/译文、来源、模型、确认、错误
- `translation_cache`：跨 Mod/模型复用的文本缓存
- `translation_packs`：搜索候选及兼容性评分
- `diagnostic_runs` / `diagnostic_issues`：诊断报告与逐条证据
- `repair_plans`：修复计划 JSON、执行结果、备份
- `activity` / `install_records`：用户审计
- `app_settings`：少量应用级键值

数据库 schema 版本写入 `PRAGMA user_version`。当前为 v1；未来版本应增加显式迁移步骤，而不是静默修改表结构。

## 备份与恢复

`BackupManager` 创建的 ZIP 包含：

```json
{
  "manifest_version": 1,
  "created_at": "...",
  "kind": "install|update|uninstall|translation|repair|manual",
  "label": "...",
  "note": "...",
  "root": "C:\\...\\Mods",
  "items": ["Mod Folder", "i18n/zh.json"],
  "extra": {"restore_root": "..."}
}
```

恢复流程：

1. 校验清单与归档中的每个路径（不得越界）。
2. 将归档抽取到临时目录。
3. 对每个顶层项：移动当前内容到回滚临时目录 → 放入备份内容。
4. 任一步失败：反向恢复旧内容。
5. 恢复操作本身会先创建安全备份。
6. 服务层限制恢复根目录必须位于游戏安装目录内。

## 安全边界

- Nexus：仅官方 API，不绕过 Premium/API 限制；下载 URL 必须 HTTPS。
- AI：仅在用户触发时调用；不会后台自动上传日志。
- 文件：解压/备份/恢复都验证路径；删除操作限制在游戏 Mods 或 ModPilot 自有目录。
- 凭据：`keyring` 优先，回退 DPAPI；日志过滤器和导出流程排除密钥。
- 自动化：安装、启用/禁用、修复都记录审计；高风险修复需 UI 确认。

## 已知架构限制

- 一个适配器当前只管理一套安装路径（`games.adapter_key` 唯一）。多套同游戏安装需要后续迁移 schema。
- 嵌套 Mod 作为独立记录扫描，父目录启停会联动更新子记录；复杂嵌套安装的卸载仍可能影响父 Mod，UI 会给出备份。
- Nexus v2 GraphQL 仍在演进，汉化包搜索失败会以警告呈现，不影响其他功能。
- 翻译缓存按目标语言与原文哈希共享；不包含模型上下文，适合短文本，不适合需要全文语境的对话。
- 诊断的证据过滤是保守策略：宁可丢弃 AI 结论，也不展示无法引用的原因。
