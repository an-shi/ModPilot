# ModPilot

面向 Windows 的轻量级、AI 驱动的多游戏 Mod 管理器。首个完整适配目标是《星露谷物语》（Stardew Valley / SMAPI），核心管理引擎与游戏逻辑严格分离。

ModPilot 使用 Python 3.12 + PySide6，SQLite 保存本地数据，Nexus Mods 官方 API 获取元数据与下载链接，可配置任意 OpenAI 兼容大模型用于汉化和日志诊断。

## 当前状态

这是一个可运行的完整桌面应用，不是静态原型。核心流程均已接入真实业务逻辑：

| 能力 | 状态 | 说明 |
|---|---|---|
| 多游戏适配器架构 | 已实现 | 统一 `GameAdapter` 接口；目前提供星露谷物语适配器 |
| 游戏目录识别 | 已实现 | Steam 注册表/库目录、GOG 常见目录、手动选择 |
| Mod 扫描 | 已实现 | 递归解析 `manifest.json`，包含禁用项、重复项、损坏 manifest |
| 安装/卸载 | 已实现 | ZIP/7Z/TAR.GZ 安全解压、路径穿越防护、安装前备份、失败回滚 |
| 启用/禁用 | 已实现 | 星露谷：点前缀重命名（SMAPI 忽略点开头目录），嵌套目录联动更新 |
| 依赖检测 | 已实现 | 必需/可选依赖、最低版本、禁用依赖、缺失依赖 |
| 配置档 | 已实现 | 保存/应用/复制/重命名/导入/导出；不复制 Mod 文件 |
| Nexus 元数据 | 已实现 | Mod ID 查询、文件列表、依赖、限流跟踪 |
| Nexus 下载 | 已实现 | 官方 API 链接、队列、进度、暂停、恢复、取消、重试、MD5 校验 |
| 更新中心 | 已实现 | 六种策略、计划检查、版本锁定、忽略版本、更新历史、安装后版本校验、回滚 |
| AI 汉化 | 已实现 | i18n 解析、占位符保护、批量翻译、本地缓存、人工编辑确认、结构校验、写入前备份 |
| 汉化包搜索 | 部分实现 | Nexus GraphQL + GitHub 公开搜索；候选需人工核对。远程包不自动下载/应用 |
| SMAPI 日志解析 | 已实现 | 加载失败、缺失依赖、版本不兼容、异常堆栈、Content Patcher 错误等 |
| 静态诊断 | 已实现 | 依赖、重复安装、SMAPI 版本、环境检查、可扩展已知问题规则 |
| AI 深度诊断 | 已实现 | 结构化日志 + 元数据；严格证据校验，拒绝无证据结论 |
| 修复计划 | 已实现 | 计划、勾选、风险等级、确认执行、重新扫描验证、失败回滚 |
| 自动修复 | 部分实现 | 可自动禁用重复副本、启用缺失依赖、在日志提供明确 Nexus 链接时安装依赖、清理临时文件；文件冲突/配置修复需手动 |
| 深色/浅色主题 | 已实现 | 响应式布局，支持窗口缩放 |
| 测试 | 已实现 | 53 个 pytest 测试，覆盖适配器、安装、备份、配置档、Nexus、下载、更新、汉化、诊断、凭证与 UI 烟测 |

### 明确未实现 / 限制

- **仅提供星露谷物语适配器**。Skyrim、Fallout 4、Cyberpunk 2077 需要新增适配器；核心无需改动。
- **RAR 不支持**。请转换为 ZIP/7Z。
- **Nexus 非 Premium 账号**无法通过 API 获取直接下载链接。ModPilot 不绕过会员权限；请使用网页下载后在“下载中心 → 导入本地压缩包”安装。
- **汉化包远程自动应用未实现**。搜索候选需要用户打开页面核对并下载，再通过“导入汉化包”应用；这样避免错误版本覆盖。
- **AI 翻译仅覆盖适配器声明的标准 i18n 格式**。当前为 SMAPI `i18n/default.json` / `en.json` → `i18n/zh.json`。
- **配置文件自动修复未实现**。诊断会标记 manifest/配置问题，但不会自动编辑未知格式的用户配置。
- **已知问题规则库默认为空**，避免编造兼容性结论。可通过 `known_issues.json` 增加有公开来源的规则。
- **自动更新仅应用运行期间执行**；关闭应用后不会后台运行。
- **UI 仅中文**；代码与架构支持后续本地化。
- **安装包脚本已提供**，但构建安装包需要本机安装 Inno Setup。
- 没有云端账号、自建服务器或遥测。

## 快速开始

### 环境

- Windows 10/11
- Python 3.12+
- 首次安装依赖需要网络

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m modpilot
```

也可以：

```powershell
python run.py
```

数据默认保存在 `%LOCALAPPDATA%\ModPilot`。可用环境变量或参数覆盖：

```powershell
$env:MODPILOT_DATA_DIR = "D:\ModPilotData"
python run.py --data-dir "D:\ModPilotData"
```

### 配置

1. 在“游戏管理”添加 Stardew Valley 根目录（包含 `Stardew Valley.exe` 和 `Content`）。
2. 在“设置”填写 Nexus API Key。Key 从 <https://www.nexusmods.com/users/myaccount?tab=api> 获取。
3. 如需 AI 汉化/诊断，填写 OpenAI 兼容 Base URL、模型名称和 API Key。支持自定义请求头与请求体参数。
4. 扫描 Mods 目录，按需安装/启用/汉化/诊断。

> AI 配置中的额外请求头/请求体仅用于非敏感参数（如 `top_p`、自定义路由头）。API Key 请使用专用输入框；带 `Authorization`、`token`、`secret` 等字样的自定义字段会被拒绝，以免密钥落入配置文件。

敏感密钥使用 Windows 凭据管理器保存；若不可用则回退到当前 Windows 用户的 DPAPI 加密文件。密钥不会写入配置导出、数据库或日志。

## 数据与安全

- 本地数据库：`%LOCALAPPDATA%\ModPilot\modpilot.db`
- 日志：`%LOCALAPPDATA%\ModPilot\logs\modpilot.log`
- 备份：`%LOCALAPPDATA%\ModPilot\backups`
- 下载：`%LOCALAPPDATA%\ModPilot\downloads`（可在设置修改）
- 翻译缓存：SQLite 中的 `translation_cache` 表，可人工清理
- 所有 Mod 文件修改前尽量创建 ZIP 备份；备份内含恢复清单
- 卸载会先备份；安装/更新替换前会备份；汉化写入前备份 i18n 目录
- API 密钥不写入日志；日志过滤器还会兜底脱敏常见密钥模式

> 安全提示：AI 翻译/诊断会把相关 Mod 文本或日志发送到你配置的 API。使用第三方 API 前请自行评估隐私与数据政策。

## 架构

```text
modpilot/
  adapters/           游戏适配器（唯一允许包含游戏专属逻辑的位置）
    base.py           GameAdapter 接口与注册表
    stardew/          manifest、安装映射、i18n、SMAPI 日志、已知问题
  core/               与游戏无关的基础设施
    archive.py        安全解压（防路径穿越、符号链接、ZIP 炸弹）
    backup.py         ZIP 备份与可回滚恢复
    credentials.py    Windows 凭据管理器 / DPAPI
    text_tokens.py    占位符保护与校验
    paths.py          路径安全、文件名、原子写入
  data/               SQLAlchemy 模型、数据库、仓储
  domain/             Pydantic DTO 与枚举
  services/           业务服务（UI 无直接业务逻辑）
    mod_service.py    扫描、安装、启停、卸载、依赖、回滚
    game_service.py   游戏目录与环境
    profile_service.py
    download_manager.py
    update_service.py
    nexus/client.py
    translation/      LLM 客户端、缓存、汉化引擎
    diagnostics/      日志解析规则、诊断引擎
    repair_service.py
  ui/                 PySide6 页面与组件
  app_services.py     组合根（依赖注入）
  app.py              应用入口
tests/                pytest
docs/                 架构、扩展、汉化、诊断、构建说明
```

核心数据流：

```text
UI → TaskManager（线程池）→ Service → Adapter / Repository / Core
                                   ↓
                             SQLite / 文件系统 / Nexus / LLM
```

详细设计见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest
```

UI 烟测使用 Qt offscreen 平台；测试数据全部位于临时目录，不会触碰真实游戏或用户数据。

## 构建 Windows 应用与安装包

```powershell
# 仅构建可执行程序目录 dist\ModPilot
powershell -ExecutionPolicy Bypass -File scripts\build.ps1

# 如已安装 Inno Setup，同时生成 installer\Output\ModPilot-Setup-*.exe
powershell -ExecutionPolicy Bypass -File scripts\build.ps1
```

构建细节、图标生成和 Inno Setup 说明见 [docs/BUILD.md](docs/BUILD.md)。

## 扩展新游戏

只需要实现 `GameAdapter` 并注册，不需要修改核心管理引擎。示例与测试规范见 [docs/ADDING_GAME.md](docs/ADDING_GAME.md)。

## 设计原则

1. 游戏逻辑只在适配器中。
2. UI 不直接做文件/数据库业务。
3. 所有长任务后台执行。
4. 文件修改可备份、可恢复、失败尽量自动回滚。
5. 自动化必须可控；高风险操作必须确认。
6. AI 结论必须有证据；不确定就明确标注不确定。
7. 不伪造功能；未完成能力在文档和 UI 中明确说明。

## License

MIT，见 [LICENSE](LICENSE)。
