# Flight Analyzer 轻量重构执行计划

## 1. 文档目的

本文档基于 [`docs/refactor-find.md`](./refactor-find.md) 的分析结论，给出可以直接执行的重构批次、文件迁移关系、兼容策略和验收标准。

计划重点是让现有目录表达真实的接口、导入、同步和前端业务边界。重构期间不主动改变 API、数据库 schema、同步协议和用户界面行为。

计划基于 2026-07-15 的工作区快照制定。执行每个批次前，需要重新检查相关文件是否存在用户正在进行的修改，并在当前内容上工作。

## 2. 总体目标

重构完成后应达到以下状态：

- 根目录 `main.py` 只承担桌面启动、静态前端挂载、uvicorn 和 pywebview 相关逻辑。
- 根目录 `server_app.py` 只作为服务端兼容入口，不再包含具体路由实现。
- 本地端和服务端 FastAPI Router 分别位于 `backend/api/desktop/` 与 `backend/api/server/`。
- 导入模块集中在 `backend/import_pipeline/`，并消除 scanner、parser、format config 之间的循环依赖。
- 同步模块集中在 `backend/sync/`，共享协议、安全校验和哈希工具只有一份实现。
- 稳定实体的重复 CRUD 集中在 `backend/repositories/`，复杂分析、动态表和同步 SQL 仍可留在所属功能模块。
- 前端页面入口继续位于 `pages/`，复杂页面逐步由 `features/` 中的组件和 hook 组合而成。
- 前端单体 `api.ts` 按业务域拆分，同时保留统一 HTTP Client。
- 现有启动命令、API 路径、同步包版本和 PyInstaller 入口保持兼容。

## 3. 非目标

本轮重构不包含以下工作：

- 不修改 SQLite 或 MySQL schema，不新增数据迁移。
- 不调整 API 路径、HTTP 方法、请求字段或响应字段。
- 不修改 `PACKAGE_VERSION`、`SYNC_PROTOCOL_VERSION` 或 `CURRENT_SCHEMA_VERSION`。
- 不统一 SQLite 与 MySQL 的 Repository 接口。
- 不引入 Domain、Application、Infrastructure、Unit of Work 或依赖注入容器。
- 不引入新的前端状态管理框架、路由框架或 UI 组件库。
- 不进行页面视觉改版。
- 不以减少行数为目的拆分仍然职责单一的文件。
- 不在目录移动批次中顺便修复已有业务缺陷。

发现已有缺陷时，应记录为独立问题。尤其需要关注 FastAPI 静态路径和动态路径的注册顺序，例如 `/api/flights/scan` 与 `/api/flights/{flight_id}`；目录重构不得静默改变原有匹配行为。

## 4. 目标目录结构

### 4.1 后端

```text
backend/
├── api/
│   ├── desktop/
│   │   ├── __init__.py
│   │   ├── app.py
│   │   ├── schemas.py
│   │   └── routers/
│   │       ├── __init__.py
│   │       ├── auth.py
│   │       ├── users.py
│   │       ├── models.py
│   │       ├── flights.py
│   │       ├── imports.py
│   │       ├── analysis.py
│   │       ├── sync.py
│   │       └── runtime.py
│   └── server/
│       ├── __init__.py
│       ├── app.py
│       ├── dependencies.py
│       ├── schemas.py
│       └── routers/
│           ├── __init__.py
│           ├── auth.py
│           ├── users.py
│           ├── models.py
│           └── sync.py
├── import_pipeline/
│   ├── __init__.py
│   ├── file_reader.py
│   ├── session_metadata.py
│   ├── scanner.py
│   ├── format_configs.py
│   ├── parser.py
│   └── importer.py
├── sync/
│   ├── __init__.py
│   ├── protocol.py
│   ├── package.py
│   ├── local_import.py
│   ├── client.py
│   ├── repository.py
│   ├── workflow.py
│   ├── progress.py
│   └── server.py
├── repositories/
│   ├── __init__.py
│   ├── flights.py
│   ├── models.py
│   ├── users.py
│   ├── permissions.py
│   └── raw_files.py
├── model_catalog.py
├── analysis.py
├── auth.py
├── permissions.py
├── database.py
├── server_database.py
├── raw_storage.py
├── runtime_context.py
└── config.py

main.py
server_app.py
server_main.py
```

`sync/workflow.py` 和 `sync/progress.py` 是根据当前 `main.py` 中已经存在的大量同步编排确定的必要文件，不要求其他功能复制相同结构。

`model_catalog.py` 承担机型配置导入导出、动态表注册和列配置等跨多张表的流程。简单机型、飞机 CRUD 放入 `repositories/models.py`，避免把复杂配置流程拆成大量细碎 Repository 方法。

### 4.2 前端

```text
frontend/src/
├── App.tsx
├── main.tsx
├── api/
│   ├── client.ts
│   ├── auth.ts
│   ├── users.ts
│   ├── models.ts
│   ├── flights.ts
│   ├── imports.ts
│   ├── analysis.ts
│   ├── sync.ts
│   └── index.ts
├── pages/
│   ├── ImportPage.tsx
│   ├── ModelManagerPage.tsx
│   ├── FlightViewPage.tsx
│   ├── ComparePage.tsx
│   ├── SyncPage.tsx
│   └── UserManagementPage.tsx
├── features/
│   ├── import/
│   ├── models/
│   ├── flights/
│   ├── analysis/
│   └── sync/
├── components/
├── utils/
└── syncStatus.ts
```

只创建实际需要的 feature 文件。目标结构不要求每个 feature 都拥有完整的 `components/hooks/types/api` 子目录。

## 5. 必须保持的契约

### 5.1 启动与打包

- `python main.py` 继续启动桌面应用。
- `python server_main.py` 继续启动协作服务器。
- `uvicorn server_app:app` 继续可用。
- PyInstaller 继续以根目录 `main.py` 为 Analysis 入口。
- `script/run.ps1`、`script/run.bat`、`script/run.sh` 的调用方式不变。
- `script/build.ps1`、`script/build.bat`、`script/build.sh` 的输出位置不变。
- 桌面 `requirements.txt` 不加入测试、MySQL 或其他仅开发期依赖。

### 5.2 HTTP API

- 所有 `/api/...` 路径和 HTTP 方法保持不变。
- 同一路径的路由注册顺序保持不变，直到有单独的缺陷修复批次。
- Pydantic 字段名称、默认值、可选性和额外字段处理行为保持不变。
- 异常状态码和当前主要错误消息保持不变。
- CORS、全局异常处理和静态前端 fallback 行为保持不变。

### 5.3 数据与同步

- `CURRENT_SCHEMA_VERSION = 4` 保持不变。
- `PACKAGE_VERSION = 2` 保持不变。
- `SYNC_PROTOCOL_VERSION = 1` 保持不变。
- Manifest 字段、ZIP 内路径、SQLite 中间包结构和哈希计算结果保持不变。
- 本地 SQLite 与服务端 MySQL 的事务提交、回滚时机保持不变。
- 原始文件的存储相对路径和冲突命名策略保持不变。

### 5.4 前端

- 页面 Tab、页面 Props 和 App 顶层状态流保持不变。
- Token 存储 key、Authorization Header、基础 URL 和错误解析行为保持不变。
- API 请求触发时机、加载状态和错误展示保持不变。
- DOM 结构和 CSS class 尽量保持不变；组件提取不同时进行视觉调整。

## 6. 执行原则

每个批次遵守以下规则：

1. 一个批次只处理一种变化：行为测试、文件移动、入口拆分或组件提取。
2. 文件移动优先使用 `git mv`，使审阅者能够区分移动和内容修改。
3. 移动文件时先保持函数签名，再更新导入路径，最后才处理内部结构。
4. 旧模块兼容 shim 只允许存在到清理阶段，并通过搜索清单跟踪。
5. 每批结束必须通过该批验收命令，失败时不继续叠加下一批。
6. 数据库 schema、API 契约或同步协议发生意外变化时立即停止该批。
7. 当前工作区已有修改不得被覆盖或回退；涉及相同文件时先重新读取并合并。

推荐一个批次对应一个独立提交。提交信息可以使用 `refactor:`、`test:` 或 `chore:` 前缀，但不要求修改项目现有提交规范。

## 7. 阶段 0：建立行为基线

### 7.1 目标

在移动代码前建立足以发现接口、同步协议和导入解析回归的最小保护网。当前项目没有测试框架，因此测试依赖必须与桌面打包依赖隔离。

### 7.2 新增文件

```text
requirements-dev.txt
pytest.ini
tests/
├── conftest.py
├── contracts/
│   ├── desktop_routes.json
│   └── server_routes.json
├── fixtures/
│   ├── import/
│   └── sync/
├── test_desktop_route_contract.py
├── test_server_route_contract.py
├── test_database_smoke.py
├── test_import_reader.py
└── test_sync_package_safety.py
```

`requirements-dev.txt` 建议包含：

```text
-r requirements.txt
-r requirements-server.txt
pytest>=8.0
httpx>=0.27
```

测试依赖不得加入 `requirements.txt`，避免扩大 PyInstaller 产物。

### 7.3 测试内容

#### 路由契约

- 从 `app.routes` 记录所有 `/api/` 路由的注册顺序、HTTP 方法和路径。
- 桌面端与服务端分别保存快照。
- 排除 `/docs`、`/openapi.json` 和静态文件 Mount。
- 至少对登录、机型、航班导入、分析、同步上传和同步拉取保存请求模型的关键字段断言。
- 快照变更必须人工确认，不能用自动更新快照掩盖差异。

测试环境必须在导入 `backend.database` 前设置临时 `DATA_DIR` 和 `FLIGHT_ANALYZER_CONFIG`。由于数据库路径在模块导入时固定，测试间需要隔离时优先使用子进程，不依赖修改已导入模块的全局常量。

#### 数据库 smoke test

- 在临时目录执行 `init_db()`。
- 验证 schema version 和核心表存在。
- 创建最小机型、飞机和航班记录，再读取并删除。
- 不使用用户 `%APPDATA%/FlightAnalyzer` 下的真实数据。

#### 导入解析

- 覆盖 UTF-8、带 BOM 和当前实际支持的非 UTF-8 文本。
- 覆盖有表头、无表头、空行和非法数值。
- 锁定 `detect_encoding`、`has_header`、`parse_lines` 和时间转换的当前行为。
- 使用最小目录 fixture 锁定 session key、文件聚类和日期提取结果。

#### 同步安全和协议

- 锁定安全 ZIP 路径、拒绝路径穿越和绝对路径的行为。
- 锁定 SHA256 输出。
- 锁定 Manifest 必填字段、版本检查和标准错误类型。
- 构造最小同步包，比较规范化后的 Manifest 和 ZIP 文件列表，不比较可能包含时间戳的整个 ZIP 字节流。

### 7.4 验收

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m compileall -q main.py server_app.py server_main.py backend
Push-Location frontend
npm run build
npm run lint
Pop-Location
```

如果当前 `npm run lint` 已有失败，应在阶段 0 记录为基线并单独修复；后续批次至少不能增加新的 lint 错误。

### 7.5 完成条件

- 测试只读写临时目录。
- 路由快照已人工核对。
- 测试失败能够明确指出路由、导入或同步契约发生变化。
- 桌面运行依赖和打包 spec 没有变化。

## 8. 阶段 1：归组 Repository

### 8.1 目标

先处理风险较低的模块移动，验证包结构、兼容导入和打包收集行为。

### 8.2 文件迁移

| 当前文件 | 目标文件 |
| --- | --- |
| `backend/flight_repository.py` | `backend/repositories/flights.py` |
| `backend/user_repository.py` | `backend/repositories/users.py` |
| `backend/permission_repository.py` | `backend/repositories/permissions.py` |
| `backend/raw_file_repository.py` | `backend/repositories/raw_files.py` |
| `backend/sync_repository.py` | `backend/sync/repository.py` |

同时新增 `backend/repositories/__init__.py` 和 `backend/sync/__init__.py`。

### 8.3 执行步骤

1. 使用 `git mv` 移动实现文件，不修改函数体。
2. 更新 `auth.py`、`permissions.py`、`parser.py`、`raw_storage.py`、`sync_package.py`、`sync_import.py` 和 `main.py` 的导入。
3. 为旧路径建立短期 shim，例如旧 `flight_repository.py` 只重新导出新模块的公共函数。
4. 更新脚本 `generate_builtin_model_seeds.py` 涉及的同步导入路径。
5. 运行全部测试和 compileall。

### 8.4 边界

- 不重命名函数。
- 不合并 `permission_repository` 与 `user_repository`。
- 不把同步 SQL 移入通用 `repositories/`。
- 不在本批新增 `models.py`；机型 Repository 在拆桌面模型 Router 时按实际查询一次性提取。

### 8.5 验收

除阶段 0 命令外，执行：

```powershell
rg -n "backend\.(flight_repository|user_repository|permission_repository|raw_file_repository|sync_repository)" -g "*.py"
```

结果只允许出现在兼容 shim、测试兼容性断言或迁移说明中。

## 9. 阶段 2：整理导入流水线并解除循环依赖

### 9.1 目标依赖

```text
file_reader
    ↓
format_configs
    ↓
scanner
    ↓
parser
    ↓
importer / repositories / raw_storage
```

允许 `scanner` 使用 format config 的纯查询函数，但 `format_configs` 不再导入 `scanner`。`scanner` 和 `parser` 共享的日期、session metadata 逻辑进入 `session_metadata.py`。

### 9.2 批次 2A：提取纯文件读取能力

从 `backend/scanner.py` 移动以下函数到 `backend/import_pipeline/file_reader.py`：

- `detect_encoding`
- `has_header`
- `parse_lines`
- 与上述函数直接相关且无业务状态的辅助函数

执行要求：

- `format_configs.py` 和 `importer.py` 改为直接导入 `file_reader.py`。
- 删除这些模块为绕过循环依赖而设置的函数内部 import。
- scanner 可以临时 re-export 这三个函数，直到所有调用方迁移完成。
- `scanner.time_to_sec` 与 `importer.time_to_sec` 先通过测试比较行为；只有行为完全一致才合并，否则保持两份并记录差异。

### 9.3 批次 2B：提取 session metadata

新增 `backend/import_pipeline/session_metadata.py`，移动：

- `_extract_flight_date`
- session key 中时间戳提取的纯逻辑
- scanner 和 parser 都使用的路径元数据规则

完成后 `scanner.py` 不再通过函数内部 import 引用 `parser.py`。

### 9.4 批次 2C：移动导入模块

| 当前文件 | 目标文件 |
| --- | --- |
| `backend/scanner.py` | `backend/import_pipeline/scanner.py` |
| `backend/parser.py` | `backend/import_pipeline/parser.py` |
| `backend/importer.py` | `backend/import_pipeline/importer.py` |
| `backend/format_configs.py` | `backend/import_pipeline/format_configs.py` |

更新 `main.py`、`analysis.py`、`sync_package.py`、`sync_import.py` 和所有导入流水线内部引用。旧模块路径保留短期 shim。

`raw_storage.py` 不移动到导入目录，因为同步导入同样使用原始文件存储。

### 9.5 验收

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_import_reader.py
.\.venv\Scripts\python.exe -m compileall -q backend\import_pipeline backend
rg -n "from backend\.(scanner|parser|importer|format_configs)" -g "*.py"
rg -n "from backend\.import_pipeline\.(scanner|parser|format_configs).*" backend\import_pipeline -g "*.py"
```

验收结果：

- 旧路径只存在于 shim 或兼容测试。
- `format_configs.py` 不再导入 scanner。
- `scanner.py` 不再导入 parser。
- 导入 fixture 的聚类、日期、表头判断和解析结果与基线一致。

## 10. 阶段 3：整理同步子系统

### 10.1 批次 3A：提取共享协议与安全工具

新增 `backend/sync/protocol.py`，第一步只提取已经确定重复且纯粹的能力：

- `PACKAGE_VERSION`
- `SYNC_PROTOCOL_VERSION`
- Manifest 基础字段和版本检查
- ZIP entry 安全路径规范化
- ZIP 路径穿越检查
- 文件 SHA256
- 共享的协议错误类型

从 `sync_package.py`、`sync_import.py` 和 `server_sync.py` 删除重复实现，改为调用 `protocol.py`。

本批不统一以下内容：

- SQLite 与 MySQL 的实体查找 SQL。
- 本地导入与服务端导入的事务流程。
- 两端不同的冲突处理分支。
- Pull 与 Push 的数据库映射实现。

### 10.2 批次 3B：移动同步模块

| 当前文件 | 目标文件 |
| --- | --- |
| `backend/sync_package.py` | `backend/sync/package.py` |
| `backend/sync_import.py` | `backend/sync/local_import.py` |
| `backend/sync_client.py` | `backend/sync/client.py` |
| `backend/sync_repository.py` | 阶段 1 已移动为 `backend/sync/repository.py` |
| `backend/server_sync.py` | `backend/sync/server.py` |

更新 `main.py`、`server_app.py`、`script/generate_builtin_model_seeds.py` 和同步模块内部导入。旧路径继续使用短期 shim。

### 10.3 批次 3C：迁出本地同步编排

新增：

- `backend/sync/progress.py`：操作 ID、进度状态、百分比和字节进度回调。
- `backend/sync/workflow.py`：preview、push、retry、run、pull、abandon 和同步删除编排。

从 `main.py` 移动的内容包括：

- `_sync_progress_update`、`_sync_progress_fail` 和字节进度辅助函数。
- `_sync_preview_upload`、`_sync_preview_pull`。
- push、retry、run 和 pull 的事务及异常编排。
- 同步 token 解析以外的远程调用流程。
- 与同步状态相关的删除编排。

HTTP Header、Query、Request 和 `HTTPException` 的转换仍留在 Router。workflow 返回普通 dict 或抛出功能模块自定义异常，Router 负责转成 HTTP 响应。

不要求本批把 `workflow.py` 再拆成 Service 类。

### 10.4 验收

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_sync_package_safety.py
.\.venv\Scripts\python.exe -m pytest tests\test_desktop_route_contract.py tests\test_server_route_contract.py
.\.venv\Scripts\python.exe -m compileall -q backend\sync main.py server_app.py
```

还需人工验证一个最小同步场景：

1. 本地创建或选择一条待上传航班。
2. 执行预览，记录 action、reason 和映射结果。
3. 执行 push，确认本地同步状态更新。
4. 执行 pull preview 和 pull，确认无重复原始文件。
5. 对比迁移前后的同步报告结构。

没有可用 MySQL 环境时，可以先完成纯协议和本地测试，但阶段 8 最终验收前必须在真实协作服务器环境完成一次端到端同步。

## 11. 阶段 4：拆分桌面 FastAPI 接口

### 11.1 App 工厂

新增 `backend/api/desktop/app.py`：

- 创建 FastAPI App。
- 注册 CORS。
- 注册全局异常处理器。
- 按固定顺序 include Router。
- 提供 `create_app()` 供测试和根入口使用。

静态前端挂载继续由根目录 `main.py` 完成，避免测试 App 必须依赖 `frontend/dist`。

`backend/api/desktop/schemas.py` 初期集中放置从 `main.py` 移出的 Pydantic 模型。只有该文件之后再次出现明确领域边界时才进一步拆分。

### 11.2 Router 分组

| Router | 现有接口范围 |
| --- | --- |
| `runtime.py` | `/api/health`、`/api/app/context`、`/api/runtime/*`、`/api/server-auth/*` |
| `auth.py` | `/api/auth/*` |
| `users.py` | `/api/users*` |
| `models.py` | `/api/models*`、`/api/aircraft*`、`/api/registry/columns`、`/api/presets*`、`/api/filter-presets*` |
| `flights.py` | 航班列表、详情、raw files、raw manifest、打开 raw folder、更新与删除 |
| `imports.py` | `/api/folders/*`、`/api/files/browse`、`/api/flights/scan`、`/api/flights/import` |
| `analysis.py` | flight columns、aligned、alerts、stats、correlation、anomaly 和 `/api/compare` |
| `sync.py` | 全部 `/api/sync/*` |

### 11.3 拆分顺序

按以下顺序逐个 Router 迁移，每移动一个 Router 都执行路由快照测试：

1. `runtime.py`
2. `auth.py`
3. `users.py`
4. `analysis.py`
5. `imports.py`
6. `flights.py`
7. `models.py`
8. `sync.py`

低风险 Router 先迁移，用于验证 App 工厂和注册顺序；模型与同步最后迁移，因为它们包含最多 SQL 和编排。

### 11.4 模型和航班数据访问

拆 `models.py` 时新增：

- `backend/repositories/models.py`：机型、飞机、preset 和 column registry 的稳定 CRUD。
- `backend/model_catalog.py`：模型配置导出、导入、动态表注册和跨表更新流程。

拆 `flights.py` 时优先复用 `repositories/flights.py`。删除操作涉及服务器状态时调用 `sync/workflow.py`，不要把同步规则复制到 flights Router。

### 11.5 根 `main.py` 最终职责

`main.py` 最终保留：

- `load_app_config()` 和本地数据库初始化。
- `create_app()` 调用和静态前端挂载。
- `_build_log_config()`。
- 可用端口选择和 uvicorn 线程启动。
- pywebview 窗口创建、错误窗口和进程生命周期。

`main.py` 中不再出现：

- `@app.get/post/patch/delete`。
- Pydantic 请求模型。
- `conn.execute()`。
- 同步、导入或分析业务函数。

### 11.6 验收

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_desktop_route_contract.py
.\.venv\Scripts\python.exe -m compileall -q main.py backend\api\desktop
rg -n "^@app\.(get|post|patch|delete)" main.py
rg -n "conn\.execute" backend\api\desktop main.py
```

后两个搜索应无结果。随后使用临时数据目录启动一次桌面应用，检查：首页加载、登录、页面切换、文件浏览和一条分析查询。

## 12. 阶段 5：拆分服务端 FastAPI 接口

### 12.1 文件职责

- `backend/api/server/app.py`：App、CORS、异常处理、startup 和 Router 注册。
- `backend/api/server/dependencies.py`：数据库连接、current user 和 require user。
- `backend/api/server/schemas.py`：服务端请求响应模型。
- `routers/auth.py`：health、auth 和 capabilities。
- `routers/users.py`：用户管理。
- `routers/models.py`：模型创建和实体删除入口。
- `routers/sync.py`：preflight、push、changes、preview 和 bundle。

服务端 Router 继续直接调用 `server_database.py` 与 `sync/server.py` 的稳定公共函数，不引入统一 Repository 接口。

### 12.2 根入口

根目录 `server_app.py` 最终只保留兼容导出：

```python
from backend.api.server.app import app

__all__ = ["app"]
```

`server_main.py` 的 `uvicorn.run("server_app:app", ...)` 保持不变。

### 12.3 验收

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_server_route_contract.py
.\.venv\Scripts\python.exe -m compileall -q server_app.py server_main.py backend\api\server backend\sync\server.py
rg -n "^@app\.(get|post|patch|delete)" server_app.py
```

在配置了测试 MySQL 的环境中额外验证：health、登录、preflight、push、changes 和 bundle 下载。

## 13. 阶段 6：拆分前端 API

### 13.1 HTTP Client

新增 `frontend/src/api/client.ts`，从现有 `api.ts` 移动：

- `getSessionToken`、`setSessionToken`
- `getServerToken`、`setServerToken`
- 基础 URL 处理
- `request<T>()`
- Authorization Header 注入
- JSON 与错误响应解析

必须先为这些行为补最小单元测试，或者在不引入 Vitest 的前提下通过现有页面 smoke test 锁定。若增加 Vitest 会明显扩大本轮范围，则继续以 TypeScript build 和浏览器 smoke test 为准。

### 13.2 API 模块映射

| 新模块 | 内容 |
| --- | --- |
| `auth.ts` | Health、AppContext、RuntimeContext、登录、登出、修改密码、server auth |
| `users.ts` | 用户类型与用户管理 API |
| `models.ts` | AircraftModel、Aircraft、模型/飞机/列配置 API |
| `flights.ts` | Flight、FlightRecordFields、raw file、航班 CRUD |
| `imports.ts` | ScanResult、SessionPreview、目录/文件浏览、scan 和 import |
| `analysis.ts` | Column、AlignedData、Filter、Stats、Preset、compare 和分析 API |
| `sync.ts` | 同步队列、预览、进度、导入导出和执行 API |
| `index.ts` | 迁移期间的统一导出，不承载实现 |

跨模块共享类型应放在最自然的所有者模块，并由使用方使用 `import type` 引用。不要新建一个重新集中所有 DTO 的大 `types.ts`。

### 13.3 兼容迁移

1. 将现有 `frontend/src/api.ts` 改成只 re-export `./api/index` 的兼容入口。
2. 按 `App.tsx`、UserManagement、Import、ModelManager、FlightView、Compare、Sync 的顺序更新导入。
3. 每更新一个页面执行 `npm run build`。
4. 所有调用方迁移后删除兼容 `api.ts`。
5. 更新 `syncStatus.ts` 和 `FilterBar.tsx` 的类型导入。

### 13.4 验收

```powershell
Push-Location frontend
npm run build
npm run lint
Pop-Location
rg -n "from ['\"]\.\.?/api['\"]" frontend\src -g "*.ts" -g "*.tsx"
```

最终搜索不应再引用旧的单文件入口；允许引用 `api/index` 或具体业务模块。

## 14. 阶段 7：拆分前端大页面

### 14.1 通用原则

- 先提取纯展示组件，再提取含状态的业务组件，最后才提取 hook。
- 页面继续持有跨区域共享状态，避免通过多层回调或新 Context 隐藏数据流。
- 组件提取时复制现有 DOM 和 className，不同时调整样式。
- 一个组件只被一个页面使用时放在对应 feature；确认跨页面稳定复用后才移到 `components/`。

### 14.2 批次 7A：飞行记录表单复用

`ImportPage` 与 `ModelManager` 当前重复存在默认值、数字解析、时长输入和文本字段逻辑。先提取：

```text
features/flights/
├── FlightRecordForm.tsx
└── recordFields.ts
```

`recordFields.ts` 放置 `emptyRecord`、record normalization 和纯格式化函数。`FlightRecordForm.tsx` 只接收 value、onChange、disabled 和布局变体，不自行请求 API。

### 14.3 批次 7B：SyncPage

建议拆分：

```text
features/sync/
├── SyncQueue.tsx
├── SyncPreviewDialog.tsx
├── SyncProgress.tsx
├── SyncConflictDetails.tsx
├── previewFormatters.ts
└── useSyncOperation.ts
```

先移动纯格式化函数和 Preview UI，再把操作轮询、busy、operationId 和 progress 收敛到 `useSyncOperation`。队列筛选和选择状态仍可由页面持有。

### 14.4 批次 7C：ModelManagerPage

建议拆分：

```text
features/models/
├── ModelList.tsx
├── AircraftList.tsx
├── FlightList.tsx
├── ColumnEditor.tsx
├── ModelExportDialog.tsx
└── ModelImportDialog.tsx
```

拆分顺序：模型列表、飞机列表、列编辑、航班列表、导入导出弹窗。每一步只转移对应状态和 handler；`selectedModelId`、`selectedAircraftId`、刷新版本等跨区状态留在页面。

### 14.5 批次 7D：FlightViewPage

建议拆分：

```text
features/analysis/
├── FlightTree.tsx
├── FlightChart.tsx
├── AnalysisToolbar.tsx
├── StatsPanel.tsx
├── CorrelationHeatmap.tsx
├── AnomalyChart.tsx
└── chartOptions.ts
```

`buildChartOption` 移入 `chartOptions.ts` 并保持纯函数。ECharts 实例生命周期继续由具体图表组件管理。筛选条件、选中列和 view mode 保持在页面，直到能够证明某个 hook 拥有完整生命周期。

### 14.6 批次 7E：ImportPage 与 App

ImportPage 建议拆分：

```text
features/import/
├── DirectoryPicker.tsx
├── DirectorySummary.tsx
├── SessionImportList.tsx
├── AircraftAssignment.tsx
└── ModelFromScanForm.tsx
```

App 中已有的 `RuntimeStatus` 和 `AccountMenu` 可以移动到 `components/`。App 继续持有 tab、当前航班、运行时上下文、应用上下文和顶层刷新协调，不引入全局状态库。

### 14.7 前端验收

每个页面批次执行：

```powershell
Push-Location frontend
npm run build
npm run lint
Pop-Location
```

并人工检查以下视图：

- 桌面宽度下所有 Tab 可切换且页面状态不意外重置。
- 导入目录选择、扫描、机型选择和单条 session 导入。
- Model Manager 的机型、飞机、航班和列编辑。
- Flight View 的树、图表、筛选、统计、相关性和异常模式。
- Sync 的筛选、多选、预览、冲突选择、进度和错误状态。
- 登录、登出、修改密码和用户管理。

## 15. 阶段 8：移除兼容层并完成最终验证

### 15.1 删除兼容 shim

确认所有调用方迁移后删除：

- `backend/flight_repository.py`
- `backend/user_repository.py`
- `backend/permission_repository.py`
- `backend/raw_file_repository.py`
- `backend/sync_repository.py`
- `backend/scanner.py`
- `backend/parser.py`
- `backend/importer.py`
- `backend/format_configs.py`
- `backend/sync_package.py`
- `backend/sync_import.py`
- `backend/sync_client.py`
- `backend/server_sync.py`
- `frontend/src/api.ts`

删除前使用 `rg` 确认没有运行时代码仍引用旧路径。测试中如果需要验证旧路径，只允许保留到该路径明确属于公共兼容 API 的情况下；本项目当前不把这些内部模块视为长期公共 API。

### 15.2 结构检查

- 删除空目录和无内容的 `__init__.py` 之外的占位文件。
- 确认没有 `application/`、`domain/`、`infrastructure/` 等未使用架构目录。
- 确认 Router 中没有直接 SQL。
- 确认导入流水线不存在函数内部 import 用于规避循环依赖。
- 确认同步协议工具只有一份实现。
- 更新 `docs/introduce.md` 和 `docs/sync-and-storage.md` 中受目录变化影响的路径说明。

### 15.3 全量自动验证

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m compileall -q main.py server_app.py server_main.py backend script
Push-Location frontend
npm run build
npm run lint
Pop-Location
.\.venv\Scripts\pyinstaller.exe --distpath packaging\dist --workpath packaging\build --noconfirm packaging\FlightAnalyzer.spec
```

### 15.4 最终人工验证

#### 桌面端

1. 使用全新临时数据目录启动应用。
2. 登录并检查运行环境状态。
3. 创建机型和飞机。
4. 扫描并导入一组 fixture 数据。
5. 打开 Flight View，检查图表和分析。
6. 编辑飞行记录并检查原始文件。
7. 执行模型导出和导入。
8. 启动打包后的 `FlightAnalyzer.exe`，重复 health 与页面加载 smoke test。

#### 协作服务器

1. 使用测试 MySQL 和独立 `SERVER_DATA_DIR` 启动。
2. 验证登录、用户管理和 capabilities。
3. 完成一次 preflight、push、changes、preview、bundle 和 pull。
4. 验证软删除和本地同步状态更新。
5. 检查同步包、原始文件和动态数据行数量。

## 16. 批次依赖与建议提交边界

| 批次 | 内容 | 前置 | 主要风险 |
| --- | --- | --- | --- |
| 0 | 测试与契约基线 | 无 | 测试误用真实数据 |
| 1 | Repository 归组 | 0 | 导入路径、PyInstaller 收集 |
| 2A | file reader 提取 | 0 | 编码和表头行为漂移 |
| 2B | session metadata 提取 | 2A | 日期和聚类行为漂移 |
| 2C | 导入模块移动 | 2A、2B | 循环依赖、旧导入路径 |
| 3A | 同步协议提取 | 0 | Manifest 和 ZIP 兼容性 |
| 3B | 同步模块移动 | 1、2C、3A | 跨模块导入范围大 |
| 3C | 同步 workflow 迁出 | 3B | 事务、进度和错误状态 |
| 4 | 桌面 Router 拆分 | 1、2C、3C | 路由顺序、SQL 迁移 |
| 5 | 服务端 Router 拆分 | 3B | 鉴权依赖、MySQL 事务 |
| 6 | 前端 API 拆分 | 0 | Token 和错误解析行为 |
| 7A-7E | 页面组件拆分 | 6 | 状态生命周期和 UI 回归 |
| 8 | shim 清理与全量验证 | 4、5、7 | 遗漏旧路径、打包缺失 |

阶段 2 与阶段 3A 可以独立进行；阶段 6 可以与后端目录整理独立进行。但同一工作区执行时仍建议按表中顺序提交，减少同时修改 `main.py`、`api.ts` 和多个大页面造成的冲突。

## 17. 停止条件

出现以下情况时停止当前批次，不继续叠加后续修改：

- 路由方法、路径或注册顺序发生未计划变化。
- Pydantic schema 或前端 DTO 出现字段差异。
- 同步 Manifest、ZIP 路径或版本号发生变化。
- 临时测试写入了真实 DATA_DIR 或服务器存储目录。
- 数据库 schema 被意外重建或修改。
- 文件移动后只能通过新增跨层循环依赖才能继续。
- PyInstaller 找不到新包，且需要扩大隐藏导入范围才能勉强运行。
- 当前工作区的用户修改与计划迁移发生无法安全合并的重叠。

停止后应先确定差异属于已有缺陷、测试基线错误还是重构回归，再决定恢复批次或建立独立修复任务。

## 18. 完成定义

只有同时满足以下条件，目录重构才算完成：

- 阶段 0 的全部自动测试通过。
- 桌面端和服务端路由契约快照无未确认差异。
- `main.py` 与 `server_app.py` 不再包含业务 Router 实现。
- API Router 中无直接 SQL。
- 导入模块无循环依赖和用于规避循环的函数内部 import。
- 同步协议、安全路径和哈希逻辑无重复实现。
- 前端不再存在单体 `api.ts`。
- 主要页面已拆出职责清晰的 feature 组件，但没有引入新的全局状态框架。
- 所有短期兼容 shim 已删除。
- 前端 build、lint、Python compileall、pytest 和 PyInstaller 全部通过。
- 桌面端与协作服务器的人工 smoke test 完成。
- 相关开发文档中的路径和启动说明与实现一致。

该完成定义只评价结构和兼容性，不要求同时解决重构过程中发现的所有已有业务缺陷。
