# Flight Analyzer 项目结构分析与重构方向

## 1. 分析范围

本文档记录当前项目在目录结构、文件规模、模块职责和模块依赖方面的主要问题，并给出后续重构的总体方向。

当前阶段以分析为主，不包含详细迁移步骤、任务拆分、工期评估或具体实施顺序。详细重构计划将在下一阶段制定。

## 2. 总体判断

当前项目的问题不只是目录较为扁平，更核心的问题是：入口文件、页面文件和同步模块正在承担实际的业务边界。

项目已经形成以下多个相对独立的子系统：

- 本地桌面应用及本地 FastAPI 服务。
- 协作服务器及 MySQL 数据存储。
- 飞行数据扫描、格式识别和导入。
- 机型、飞机和飞行架次管理。
- 数据分析和图表展示。
- 本地与服务器之间的数据同步。
- 用户、认证和权限管理。

但是，当前目录结构和模块边界没有清晰表达这些子系统。接口、业务流程、数据库操作、文件操作和进程启动逻辑经常集中在同一文件中。

风险最高的区域依次为：

1. `main.py` 与同步模块职责过度集中。
2. Repository 层不完整，SQL 泄漏到接口层和业务流程。
3. 扫描、格式识别和解析模块之间存在循环依赖。
4. 前端页面同时承担数据请求、业务状态和大量 UI 展示。
5. 本地端与服务器端存在平行实现，业务契约容易漂移。
6. 缺少自动化测试，重构缺乏行为保护网。

## 3. 目录结构与文件规模

### 3.1 主要超大文件

| 文件 | 约行数 | 当前主要职责 |
| --- | ---: | --- |
| `main.py` | 3292 | 本地 FastAPI、本地业务接口、同步编排、静态资源、uvicorn、pywebview |
| `backend/server_sync.py` | 2066 | 服务端同步预检、冲突判断、包导入、SQL、文件复制、拉取包生成、删除 |
| `backend/sync_import.py` | 1744 | 本地同步包预览、映射、冲突处理、数据库写入、原始文件导入 |
| `frontend/src/pages/FlightView.tsx` | 1593 | 航班树、图表、筛选、统计、相关性、异常、编辑和删除 |
| `frontend/src/pages/ImportPage.tsx` | 1590 | 文件扫描、数据导入、航班管理、同步包导入导出、模型创建 |
| `frontend/src/pages/ModelManager.tsx` | 1331 | 机型、飞机、航班、原始文件、列配置和记录编辑 |
| `backend/format_configs.py` | 934 | 格式配置持久化、格式探测、配置生成、动态表和列注册 |
| `frontend/src/pages/SyncPage.tsx` | 892 | 同步队列、预览、执行、进度、错误和冲突展示 |
| `frontend/src/api.ts` | 867 | Token、HTTP 请求、全部 DTO 类型和全部业务 API |
| `backend/scanner.py` | 758 | 文件读取、编码探测、文件聚类、模型识别、目录扫描和数据库查询 |
| `backend/server_database.py` | 715 | MySQL schema、连接、认证、CRUD 和动态表创建 |
| `backend/analysis.py` | 664 | 数据表查询、时间对齐、过滤、统计、相关性、异常和对比 |
| `frontend/src/App.tsx` | 605 | 应用初始化、认证、导航、全局状态和页面组合 |
| `server_app.py` | 467 | 服务端 FastAPI、请求模型、鉴权依赖和业务接口 |

这些文件的问题并非单纯代码量较大，而是每个文件内部包含多条可以独立变化的业务流程。

例如，`main.py` 中同时包含：

- FastAPI 实例和中间件配置。
- Pydantic 请求模型。
- 认证、用户和权限接口。
- 机型、飞机和航班 CRUD。
- 文件夹选择和文件系统访问。
- 飞行数据导入和分析接口。
- 同步预览、上传、拉取、重试和进度管理。
- 静态前端挂载。
- uvicorn 日志配置和端口选择。
- pywebview 桌面窗口启动。

其中部分单个函数已达到 100 至 225 行。这说明 `main.py` 已经同时充当接口层、应用服务层、基础设施层和进程启动入口。

### 3.2 当前目录扁平带来的问题

后端大量模块直接放置在 `backend/` 根目录下，文件名只能说明技术职责，不能表达所属业务域及所在架构层级。例如：

- `scanner.py`、`parser.py`、`importer.py` 和 `format_configs.py` 共同组成导入流水线，但目录结构没有体现这种关系。
- `sync_package.py`、`sync_import.py`、`sync_client.py`、`sync_repository.py` 和 `server_sync.py` 共同组成同步子系统，但本地、服务端、协议和基础设施逻辑混杂。
- `database.py` 与 `server_database.py` 不只是数据库连接模块，还包含 schema、认证、CRUD 和部分业务规则。

前端虽然存在 `pages/`，但页面内部没有进一步形成 feature、hook 和复用组件边界，导致页面文件持续膨胀。

## 4. 当前模块职责与依赖关系

### 4.1 后端入口

当前主要有两个 FastAPI 入口：

```text
main.py
└── 本地桌面应用使用的 API 和进程启动

server_app.py
└── 协作服务器使用的 API
```

`main.py` 直接依赖大多数后端模块：

```text
main.py
├── auth / permissions
├── database / repositories
├── scanner / parser / importer / format_configs
├── raw_storage
├── analysis
├── sync_package / sync_import / sync_client / sync_repository
├── runtime_context
└── pywebview / uvicorn / StaticFiles
```

`server_app.py` 的主要依赖关系为：

```text
server_app.py
├── auth
├── server_database
└── server_sync
    └── server_database
```

入口层直接了解数据库函数、文件系统、同步协议和业务状态，接口层与具体功能实现没有分离。

### 4.2 数据导入流水线

当前导入相关模块职责如下：

- `scanner.py`：扫描目录、读取文件、识别编码、聚类文件、识别机型和会话。
- `format_configs.py`：读取和保存格式配置、自动推断格式、生成动态表定义。
- `parser.py`：编排一次完整会话导入及事务。
- `importer.py`：解析数据文件并写入动态数据表。
- `raw_storage.py`：复制和登记原始文件。
- `flight_repository.py`：处理部分航班元数据查询和写入。

当前存在明显循环依赖：

```text
scanner ─────────→ format_configs
   ↑                    │
   └────────────────────┘

parser ──────────→ scanner
   ↑                  │
   └──────────────────┘
```

为了绕过循环导入，代码中出现多处函数内部 import。根因是以下职责没有形成稳定边界：

- 文件读取和编码探测。
- 格式推断。
- 会话发现和文件聚类。
- 数据导入编排。
- 数据持久化。

### 4.3 同步子系统

当前同步模块职责如下：

- `sync_package.py`：生成本地同步包和 `manifest.json`。
- `sync_import.py`：预览并导入同步包，以及应用服务器拉取结果。
- `sync_client.py`：访问协作服务器 HTTP API。
- `sync_repository.py`：维护部分同步状态、同步队列和同步报告。
- `runtime_context.py`：读取服务器地址、检查在线状态和生成运行时上下文。
- `server_sync.py`：服务端预检、上传导入、冲突处理、拉取包生成和软删除。
- `main.py`：同步流程编排、进度管理、事务和错误处理。

同步子系统目前存在以下问题：

- 协议逻辑、本地应用逻辑和服务端应用逻辑没有清晰分离。
- ZIP 安全路径校验、SHA256 计算等功能在多个模块重复实现。
- `server_sync.py` 和 `sync_import.py` 同时负责协议解析、实体映射、冲突判断、SQL 和文件复制。
- `main.py` 继续承担同步用例编排，导致同步职责跨越多个层级。
- 本地 SQLite 与服务器 MySQL 的实现差异已经扩散到业务流程中。

本地与服务端使用不同数据库是合理的，但以下内容应当共享：

- Manifest 数据结构和版本规则。
- 同步包安全校验。
- 实体标识和映射规则。
- 冲突类型和冲突判定规则。
- 同步操作的结果模型和错误类型。

### 4.4 数据库与 Repository

当前代码已经出现 `flight_repository.py`、`sync_repository.py`、`user_repository.py` 等 repository，但还没有形成完整持久化边界。

主要表现为：

- `main.py` 仍存在大量直接 SQL。
- `server_sync.py`、`sync_import.py`、`analysis.py`、`format_configs.py` 等模块直接操作连接对象。
- 事务边界依赖调用者约定，缺少统一的 Unit of Work 或应用服务边界。
- `database.py` 和 `server_database.py` 同时承担 schema、连接、查询和业务辅助函数。
- Repository 无法真正隔离 SQLite 和 MySQL 的方言及数据模型差异。

因此，当前 repository 更接近零散 SQL helper，而不是业务层依赖的稳定接口。

### 4.5 前端模块依赖

前端当前主要关系如下：

```text
App.tsx
├── ImportPage
├── ModelManager
├── FlightView
├── ComparePage
├── SyncPage
└── UserManagementPage

所有页面
└── api.ts
    ├── Token 和认证状态
    ├── 通用 fetch 封装
    ├── 全部请求响应类型
    └── 全部业务 API
```

前端主要问题包括：

- `api.ts` 有约 115 个导出和 61 个接口函数，任何领域接口变化都会集中影响该文件。
- 页面组件直接负责请求、加载状态、错误、业务操作和 UI。
- `ImportPage` 包含约 43 组状态，`ModelManager` 约 39 组，`FlightView` 约 30 组。
- 航班列表、重命名和删除逻辑在多个页面重复。
- 飞行记录的默认值、数字解析和表单控件在 `ImportPage` 与 `ModelManager` 中重复。
- 同步状态显示存在公共实现，但 `ModelManager` 仍保留自己的重复版本。
- `App.tsx` 既负责应用初始化，又承担认证界面、导航和多组领域状态。

## 5. 主要结构问题总结

### 5.1 入口文件成为 God Module

`main.py` 中包含大量请求模型、路由、SQL、同步编排和桌面启动逻辑，任何功能变化都可能影响应用入口。

### 5.2 接口层与功能实现耦合

FastAPI 路由直接调用数据库、文件系统和同步实现。接口层无法独立测试，业务逻辑也无法脱离 HTTP 入口复用。

### 5.3 Repository 边界不完整

业务模块仍然了解表结构、SQL、连接对象和数据库方言，导致数据库实现与业务流程强耦合。

### 5.4 导入流水线存在循环依赖

扫描、格式识别、解析和导入相互引用，并通过函数内部 import 暂时规避 Python 导入循环。

### 5.5 同步协议与两端实现混杂

共享协议没有形成独立核心，本地和服务端分别实现相似校验与映射逻辑，容易出现行为差异。

### 5.6 前端页面缺少内部业务边界

页面入口虽然明确，但页面内部聚合了多个独立功能，导致文件体量、状态数量和重复逻辑持续增长。

### 5.7 类型和接口契约分散

后端 Pydantic 模型集中在入口文件，前端接口类型集中在单个 `api.ts`，本地端和服务端还存在部分重复定义，接口演进容易产生漂移。

### 5.8 缺少测试保护

当前没有发现后端或前端测试文件。同步、导入、动态数据表和文件存储逻辑复杂，缺少测试会显著增加重构风险。

### 5.9 文档开始与实现不一致

例如同步文档记录的 schema version 与当前代码不一致，部分存储结构描述也已落后于实现。这说明架构边界和数据契约需要由代码和测试共同约束。

## 6. 调整后的前端重构方向

### 6.1 保留明确的页面入口

前端重构后继续保留 `pages/`。主要页面与入口文件保持一一对应，确保可以直接定位页面：

```text
frontend/src/
├── app/
│   ├── App.tsx
│   ├── navigation.ts
│   └── providers/
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
│   ├── sync/
│   └── users/
└── shared/
    ├── api/
    ├── components/
    ├── hooks/
    ├── types/
    └── utils/
```

`pages` 中的文件作为稳定页面入口，主要负责：

- 接收路由或应用顶层参数。
- 组合 feature 组件。
- 控制页面级布局。
- 处理少量真正属于整个页面的状态。

具体业务内容下沉到 `features`。例如：

```text
pages/ImportPage.tsx
    ↓
features/import/
├── components/
│   ├── FolderScanner.tsx
│   ├── SessionImportList.tsx
│   └── ImportRecordForm.tsx
├── hooks/
│   ├── useFolderScan.ts
│   └── useSessionImport.ts
├── api.ts
└── types.ts
```

这样既保留明确的页面定位，又避免 `pages` 中继续出现超过千行的组件。

### 6.2 前端各目录职责

- `app/`：应用初始化、导航、全局 Provider 和认证上下文。
- `pages/`：页面入口和页面级布局。
- `features/`：按业务能力组织组件、状态、hooks、API 和类型。
- `shared/components/`：无业务归属或跨 feature 复用的 UI。
- `shared/api/`：基础 HTTP Client、Token 注入和统一错误处理。
- `shared/types/`：真正跨多个 feature 使用的基础类型。

### 6.3 前端拆分方向

- `ImportPage` 拆分为目录扫描、会话导入、同步包导入导出等 feature 组件。
- `ModelManagerPage` 拆分为机型、飞机、航班、列配置和记录编辑组件。
- `FlightViewPage` 拆分为航班导航、图表、筛选、统计、相关性和异常分析组件。
- `SyncPage` 拆分为队列、预览、执行进度、冲突详情等组件。
- 航班列表、飞行记录表单和同步状态展示形成复用组件。
- 单体 `api.ts` 按 feature 拆分，保留一个共享 HTTP Client。
- `App.tsx` 只保留应用启动、导航和全局上下文组合。

## 7. 调整后的后端重构方向

### 7.1 建立独立接口层

后端需要设置单独的 `api/` 接口层，并与具体功能实现分离：

```text
backend/
├── api/
│   ├── desktop/
│   │   ├── app.py
│   │   └── routers/
│   │       ├── auth.py
│   │       ├── models.py
│   │       ├── flights.py
│   │       ├── imports.py
│   │       ├── analysis.py
│   │       ├── sync.py
│   │       └── runtime.py
│   ├── server/
│   │   ├── app.py
│   │   └── routers/
│   │       ├── auth.py
│   │       ├── users.py
│   │       ├── models.py
│   │       └── sync.py
│   ├── schemas/
│   │   ├── auth.py
│   │   ├── flights.py
│   │   ├── models.py
│   │   └── sync.py
│   ├── dependencies.py
│   └── error_handlers.py
├── application/
│   ├── auth_service.py
│   ├── model_service.py
│   ├── flight_service.py
│   ├── import_service.py
│   ├── analysis_service.py
│   └── sync_service.py
├── domain/
│   ├── models/
│   ├── import_pipeline/
│   ├── sync/
│   ├── policies/
│   └── errors.py
├── infrastructure/
│   ├── database/
│   │   ├── sqlite/
│   │   └── mysql/
│   ├── repositories/
│   ├── storage/
│   └── remote/
├── bootstrap/
│   ├── desktop.py
│   └── server.py
└── config.py
```

### 7.2 后端各层职责

#### API 接口层

`api/` 只负责：

- FastAPI App 和 Router。
- Pydantic 请求、响应模型。
- HTTP 参数解析。
- 认证依赖和依赖注入。
- 业务错误到 HTTP 状态码的转换。
- 调用 application 层用例。

接口层不应包含：

- SQL 或数据库表结构。
- 数据库事务编排。
- ZIP 和文件复制。
- 同步冲突判断。
- 数据格式识别和解析。
- 具体业务状态变更规则。

#### Application 应用层

`application/` 负责完整业务用例，例如：

- 创建、更新或删除机型。
- 导入一个飞行会话。
- 扫描并预览一个数据目录。
- 执行一次上传、拉取或完整同步。
- 查询航班分析数据。

该层负责用例编排和事务边界，但不直接依赖 FastAPI，也不直接编写 SQL。

#### Domain 领域层

`domain/` 负责与框架和数据库无关的规则，例如：

- 实体和值对象。
- 同步 manifest 和协议版本。
- 实体映射与冲突规则。
- 导入格式和会话模型。
- 权限策略。
- 领域错误类型。

#### Infrastructure 基础设施层

`infrastructure/` 负责具体技术实现，例如：

- SQLite 和 MySQL 连接及 schema。
- Repository 实现。
- 原始文件和同步包存储。
- ZIP、SHA256 和安全路径校验。
- 服务器 HTTP Client。

#### Bootstrap 启动层

`bootstrap/` 负责组装依赖和启动进程：

- 创建本地或服务端 FastAPI App。
- 初始化数据库和 repository。
- 注入 application service。
- 启动 uvicorn。
- 启动 pywebview。

### 7.3 后端依赖方向

目标依赖方向为：

```text
bootstrap
    ↓
api
    ↓
application
    ↓
domain

infrastructure
    └── 实现 application/domain 定义的持久化与外部服务接口
```

禁止出现以下反向依赖：

- Domain 依赖 FastAPI。
- Domain 依赖 SQLite、MySQL 或 SQLAlchemy。
- Application 依赖具体 Router。
- API 直接依赖具体数据库表和 SQL。
- SQLite Repository 依赖 MySQL Repository，或反向依赖。

### 7.4 接口层目标形态

接口层路由应尽量保持为参数转换和用例调用：

```python
@router.post("/flights/import", response_model=ImportSessionResponse)
def import_flight(
    request: ImportSessionRequest,
    service: ImportService = Depends(get_import_service),
):
    result = service.import_session(request.to_command())
    return ImportSessionResponse.from_result(result)
```

路由函数中不应出现 `conn.execute()`、文件复制或同步冲突处理。

## 8. 重构后的主要边界

### 8.1 页面入口边界

- `pages/` 始终保留并与主要页面一一对应。
- 页面文件负责组合，不负责实现所有子功能。
- 页面内部复杂度通过 `features/` 拆分，而不是取消页面入口。

### 8.2 HTTP 接口边界

- 本地桌面 API 与服务器 API 分别拥有明确的 App 和 Router。
- 请求响应模型集中在接口层。
- 接口层只依赖 application service。

### 8.3 应用用例边界

- 一个 service 方法对应一个完整业务操作。
- 事务由应用用例控制，而不是由 Router 或底层 helper 隐式控制。
- HTTP、CLI 或未来后台任务可以复用同一 application service。

### 8.4 数据访问边界

- 所有 SQL 进入 repository 或数据库基础设施模块。
- Application 依赖 repository 接口，而不是具体连接对象。
- SQLite 与 MySQL 作为同一业务能力的不同实现存在。

### 8.5 同步协议边界

- Manifest、协议版本、安全校验和冲突规则形成共享核心。
- 本地端负责把同步结果应用到本地 SQLite。
- 服务器端负责把同步结果应用到 MySQL。
- 两端不重复实现协议含义。

### 8.6 导入流水线边界

导入流程应拆分为单向依赖：

```text
文件读取
    ↓
格式探测
    ↓
会话发现
    ↓
导入计划
    ↓
数据解析
    ↓
Repository / Raw Storage
```

扫描模块不再反向导入 parser，格式配置模块也不再反向依赖 scanner。

## 9. 后续规划阶段需要重点处理的内容

下一阶段制定详细重构计划时，应重点确定：

- 如何为现有 API、同步协议和导入行为建立测试基线。
- `main.py` 的接口层、应用层和桌面启动逻辑如何分批迁出。
- 同步协议共享核心与本地、服务端适配器的具体边界。
- 导入流水线如何解除循环依赖并保持现有行为。
- SQLite 与 MySQL repository 接口如何定义。
- 前端各主要页面优先拆分哪些 feature 和复用组件。
- 如何在重构期间保持现有启动脚本、PyInstaller 打包和前端 API 兼容。

详细计划应遵循“先建立行为基线，再移动代码，最后收紧依赖”的原则，避免在一次改动中同时改变目录结构、业务行为和数据契约。
