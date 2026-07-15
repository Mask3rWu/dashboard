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
2. SQL 和复杂业务流程泄漏到 HTTP 接口层，入口边界不清晰。
3. 扫描、格式识别和解析模块之间存在循环依赖。
4. 前端页面同时承担数据请求、业务状态和大量 UI 展示。
5. 本地端与服务器端存在平行实现，业务契约容易漂移。
6. 缺少自动化测试，重构缺乏行为保护网。

## 3. 目录结构与文件规模

### 3.1 主要超大文件

| 文件 | 约行数 | 当前主要职责 |
| --- | ---: | --- |
| `main.py` | 3321 | 本地 FastAPI、本地业务接口、同步编排、静态资源、uvicorn、pywebview |
| `backend/server_sync.py` | 2066 | 服务端同步预检、冲突判断、包导入、SQL、文件复制、拉取包生成、删除 |
| `frontend/src/pages/ModelManager.tsx` | 1774 | 机型、飞机、航班、原始文件、列配置和记录编辑 |
| `backend/sync_import.py` | 1744 | 本地同步包预览、映射、冲突处理、数据库写入、原始文件导入 |
| `frontend/src/pages/FlightView.tsx` | 1593 | 航班树、图表、筛选、统计、相关性、异常、编辑和删除 |
| `frontend/src/pages/ImportPage.tsx` | 1081 | 文件扫描、数据导入、航班管理和模型创建 |
| `backend/format_configs.py` | 934 | 格式配置持久化、格式探测、配置生成、动态表和列注册 |
| `frontend/src/pages/SyncPage.tsx` | 892 | 同步队列、预览、执行、进度、错误和冲突展示 |
| `frontend/src/api.ts` | 876 | Token、HTTP 请求、全部 DTO 类型和全部业务 API |
| `backend/scanner.py` | 758 | 文件读取、编码探测、文件聚类、模型识别、目录扫描和数据库查询 |
| `backend/server_database.py` | 715 | MySQL schema、连接、认证、CRUD 和动态表创建 |
| `backend/analysis.py` | 664 | 数据表查询、时间对齐、过滤、统计、相关性、异常和对比 |
| `frontend/src/App.tsx` | 605 | 应用初始化、认证、导航、全局状态和页面组合 |
| `server_app.py` | 467 | 服务端 FastAPI、请求模型、鉴权依赖和业务接口 |

以上行数基于当前工作区快照，仅用于辅助判断职责集中程度。问题并非单纯代码量较大，而是每个文件内部包含多条可以独立变化的业务流程。

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

### 4.4 数据访问边界

当前代码已经出现 `flight_repository.py`、`sync_repository.py`、`user_repository.py` 等 repository，但数据访问职责仍然分散。

主要表现为：

- `main.py` 仍存在大量直接 SQL，HTTP 接口了解具体表结构。
- `database.py` 和 `server_database.py` 同时承担 schema、连接、查询和业务辅助函数。
- 普通实体 CRUD、同步状态查询等稳定操作只完成了部分 repository 化。
- `server_sync.py`、`sync_import.py`、`analysis.py`、`format_configs.py` 等数据处理模块直接操作连接对象。
- 事务边界主要依赖调用者约定，复杂流程的控制位置不够清晰。

直接操作连接对象并不一定都是问题。同步导入、动态表维护和分析查询与数据库结构紧密相关，可以继续在对应功能模块内部管理 SQL。当前更重要的边界是：HTTP Router 不直接编写 SQL；稳定且重复的实体访问逐步进入 repository；复杂数据处理逻辑留在所属功能模块中。

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
- `ModelManager`、`FlightView`、`ImportPage` 等页面仍包含数十组状态，多个独立工作流共享同一组件生命周期。
- 航班列表、重命名和删除逻辑在多个页面重复。
- 飞行记录的默认值、数字解析和表单控件在 `ImportPage` 与 `ModelManager` 中重复。
- 同步状态显示存在公共实现，但 `ModelManager` 仍保留自己的重复版本。
- `App.tsx` 既负责应用初始化，又承担认证界面、导航和多组领域状态。

## 5. 主要结构问题总结

### 5.1 入口文件成为 God Module

`main.py` 中包含大量请求模型、路由、SQL、同步编排和桌面启动逻辑，任何功能变化都可能影响应用入口。

### 5.2 接口层与功能实现耦合

FastAPI 路由直接调用数据库、文件系统和同步实现。接口层无法独立测试，业务逻辑也无法脱离 HTTP 入口复用。

### 5.3 数据访问职责分散

入口文件、repository 和功能模块都包含数据访问代码。问题重点不是所有 SQL 都必须进入 repository，而是接口层仍然了解表结构，稳定 CRUD 与复杂数据处理没有形成明确分工。

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

### 6.1 保留页面入口并按需拆分

前端继续保留 `pages/`，主要页面与入口文件保持一一对应。当前阶段不必为了目录完整而预先建立 `app/providers`、每个 feature 的独立类型目录等结构，优先处理已经出现实际复杂度的页面和 API：

```text
frontend/src/
├── App.tsx
├── main.tsx
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
├── api/
│   ├── client.ts
│   ├── auth.ts
│   ├── models.ts
│   ├── flights.ts
│   ├── analysis.ts
│   ├── sync.ts
│   └── users.ts
├── components/
└── utils/
```

`pages/` 中的文件作为稳定页面入口，主要负责页面级布局、顶层参数和 feature 组合。具体业务内容只在已经能够识别出独立职责时下沉到 `features/`，不要求每个 feature 都同时拥有 `api.ts`、`types.ts`、`hooks/` 和 `components/`。

例如 `ImportPage` 可以按实际界面职责逐步拆分：

```text
pages/ImportPage.tsx
    ↓
features/import/
├── FolderScanner.tsx
├── SessionImportList.tsx
├── ImportRecordForm.tsx
└── useSessionImport.ts
```

### 6.2 前端各目录职责

- `App.tsx`：应用初始化、认证状态、导航和必要的全局状态组合。
- `pages/`：页面入口和页面级布局。
- `features/`：从复杂页面中提取出的业务组件和业务状态。
- `api/`：共享 HTTP Client，以及按业务域拆分的请求函数和相关类型。
- `components/`：跨页面复用且没有单一业务归属的 UI 组件。
- `utils/`：无业务状态的通用工具；没有实际复用时不提前创建文件。

### 6.3 前端拆分方向

- 优先拆分 `ModelManagerPage`、`FlightViewPage`、`ImportPage` 和 `SyncPage` 中职责明确的区域。
- 航班列表、飞行记录表单和同步状态展示在出现稳定复用关系后形成公共组件。
- 单体 `api.ts` 按认证、机型、航班、分析、同步和用户拆分，保留一个共享 HTTP Client。
- `App.tsx` 保留在 `src/` 根目录；只有真正出现多个全局 Provider 后，再考虑增加 `app/` 目录。
- 拆分过程中保持页面 Props 和现有 API 调用行为不变，避免同时引入新的状态管理框架。

## 7. 调整后的后端重构方向

### 7.1 采用轻量的接口层和功能分组

后端当前最需要的是把 HTTP 接口、同步子系统和导入流水线从扁平目录中分离出来，而不是立即建立完整的 `application/domain/infrastructure/bootstrap` 分层。

建议目标结构如下：

```text
backend/
├── api/
│   ├── desktop/
│   │   ├── app.py
│   │   ├── schemas.py
│   │   └── routers/
│   │       ├── auth.py
│   │       ├── models.py
│   │       ├── flights.py
│   │       ├── imports.py
│   │       ├── analysis.py
│   │       ├── sync.py
│   │       └── runtime.py
│   └── server/
│       ├── app.py
│       ├── schemas.py
│       └── routers/
│           ├── auth.py
│           ├── users.py
│           ├── models.py
│           └── sync.py
├── import_pipeline/
│   ├── file_reader.py
│   ├── scanner.py
│   ├── format_configs.py
│   ├── parser.py
│   └── importer.py
├── sync/
│   ├── protocol.py
│   ├── package.py
│   ├── local_import.py
│   ├── client.py
│   ├── repository.py
│   └── server.py
├── repositories/
│   ├── flights.py
│   ├── users.py
│   ├── permissions.py
│   └── raw_files.py
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

该结构只建立当前已经存在的边界：接口、导入、同步和稳定数据访问。`analysis.py`、数据库模块、认证和存储等单文件职责暂时保留在 `backend/` 根目录，等它们自身出现多个明确子职责后再建立子目录。

### 7.2 各目录职责

#### API 接口层

`api/` 负责：

- 创建本地端和服务端 FastAPI App。
- 定义 Router 和 Pydantic 请求响应模型。
- 解析 HTTP 参数、认证信息和错误状态。
- 调用现有功能模块或 repository。

Router 中不应继续新增直接 SQL、文件复制、ZIP 处理和长流程编排。迁移第一阶段允许 Router 直接调用现有函数，不要求先为每个功能创建 service 和依赖注入接口。

#### 功能模块

`import_pipeline/` 和 `sync/` 按业务能力组织现有模块。功能模块可以：

- 接收数据库连接并控制必要的事务。
- 在模块内部执行与功能紧密相关的 SQL。
- 处理文件、ZIP、动态表和远程请求。
- 提供可供 Router、脚本或其他功能模块调用的函数。

如果某个 Router 拆出后仍包含明显的多步骤编排，可以只在对应功能目录中增加 `service.py` 或 `workflow.py`，不要求所有功能统一建立应用服务层。

#### Repository

`repositories/` 只承载机型、飞机、航班、用户、权限和原始文件等稳定且重复的实体访问。当前阶段不要求：

- 为 SQLite 和 MySQL 定义统一抽象接口。
- 将分析查询、动态表操作和同步批量导入全部包装成 repository。
- 引入 Unit of Work 或依赖注入容器。

#### 启动入口

- `main.py` 保留桌面窗口、静态资源挂载、uvicorn 启动和打包相关逻辑。
- `server_app.py` 保留服务端兼容入口，可以只重新导出 `backend.api.server.app` 中的 App。
- `server_main.py` 继续作为协作服务器进程入口。

这样可以保持现有启动脚本和 PyInstaller 的入口不变，降低目录迁移对打包的影响。

### 7.3 后端依赖方向

目标依赖关系保持简单：

```text
main.py / server_app.py
            ↓
        backend.api
            ↓
  功能模块 / repositories
            ↓
 数据库、存储、配置等基础模块
```

需要约束的是：

- 功能模块不反向依赖具体 Router 或 FastAPI App。
- `main.py` 和 `server_app.py` 不再承载具体业务实现。
- Router 不直接了解数据库表结构。
- 本地 SQLite 和服务端 MySQL 的实现保持独立，不为了形式统一强行抽象。
- 新目录迁移时避免通过大量兼容 re-export 长期维持两套入口；兼容模块只用于分阶段迁移。

### 7.4 接口层目标形态

Router 应保持为参数转换和功能调用，但不强制引入 Service 类：

```python
@router.post("/flights/import", response_model=ImportSessionResponse)
def import_flight(request: ImportSessionRequest):
    result = import_session(request.model_dump())
    return ImportSessionResponse.model_validate(result)
```

当 `import_session` 内部流程足够复杂时，它可以是 `import_pipeline/service.py` 中的函数或类；这个选择由实际复杂度决定，而不是由目录模板决定。

## 8. 重构后的主要边界

### 8.1 页面入口边界

- `pages/` 始终保留并与主要页面一一对应。
- 页面文件负责组合，不负责实现所有子功能。
- `features/` 只接收已经识别出的复杂业务区域，不预先填充空目录。

### 8.2 HTTP 接口边界

- 本地桌面 API 与服务器 API 分别拥有明确的 App 和 Router。
- 请求响应模型分别与本地端、服务端接口放置，避免错误共享相似但不同的契约。
- Router 不包含 SQL、文件复制、格式识别和同步冲突判断。

### 8.3 数据访问边界

- 稳定、重复的实体 CRUD 进入 repository。
- 分析、同步和动态表模块可以在功能边界内部保留 SQL。
- 事务由执行完整写入流程的功能函数控制，不强制引入 Unit of Work。
- SQLite 与 MySQL 不要求实现同一套 repository 接口。

### 8.4 同步协议边界

- Manifest、协议版本、安全路径校验、哈希计算和共享错误类型进入 `sync/protocol.py`。
- 本地端负责把同步结果应用到 SQLite，服务端负责应用到 MySQL。
- 两端共享协议含义，但不强求共享具体数据库写入代码。

### 8.5 导入流水线边界

导入流程形成单向依赖：

```text
文件读取
    ↓
格式探测
    ↓
会话发现
    ↓
数据解析与导入编排
    ↓
数据库 / Raw Storage
```

通用的文件读取、编码探测、表头判断和行解析进入 `file_reader.py`。扫描模块不再反向导入 parser，格式配置模块也不再反向依赖 scanner。

### 8.6 启动与兼容边界

- 根目录入口文件名和现有命令保持不变。
- API 路径、请求响应结构、数据库 schema 和同步协议在目录迁移阶段保持不变。
- PyInstaller 继续以根目录 `main.py` 为入口。
- 旧模块路径只在迁移期间提供必要的兼容导入，完成调用方迁移后删除。

## 9. 后续详细规划需要重点处理的内容

下一阶段的详细重构计划应围绕可独立验证的小批次改动展开：

- 为现有 API 路径、同步 Manifest、安全校验和导入解析建立最小测试基线。
- 确定 `main.py` 和 `server_app.py` 的 Router 拆分批次，以及每批需要保持的接口契约。
- 确定同步模块移动、公共协议提取和兼容导入的顺序。
- 确定 `file_reader.py` 的职责，并逐步解除导入流水线循环依赖。
- 区分适合进入 repository 的稳定 CRUD 与应留在功能模块中的复杂 SQL。
- 按页面实际职责确定前端组件、hook 和 API 的拆分顺序。
- 每个批次分别验证后端启动、前端构建、桌面运行和 PyInstaller 打包。

详细计划应遵循“先建立行为基线，再按功能移动代码，随后拆入口，最后收紧依赖”的原则。目录移动、业务行为修改和数据契约调整不应放在同一个批次中。
