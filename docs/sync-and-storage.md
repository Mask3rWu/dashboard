# 同步包协议与文件对象存储

本文档记录当前离线同步与原始文件对象存储的稳定边界，供后续科研网服务器化迁移参考。

## 数据库边界

- 当前 schema 版本从 `CURRENT_SCHEMA_VERSION = 1` 开始。
- `backend/database.py` 暴露 `DB_BACKEND=sqlite|...` 配置入口；本地版当前只实现 `sqlite`，非 sqlite 会启动失败。
- 新增数据库访问优先放在 repository/helper 层：
  - `flight_repository.py`: 架次元数据、飞行记录字段、导出树查询。
  - `raw_file_repository.py`: `file_objects` 与 `flight_raw_files` 元数据。
  - `sync_repository.py`: 同步导入导出元数据与 `sync_imports` 报告。
  - `permission_repository.py`: 环境设置与 session 用户查询。
  - `user_repository.py`: 用户、密码、session 元数据。
- 动态数据表名只能通过 `backend.format_configs.data_table_name()` 生成。

## 原始文件对象存储

- 原始文件按内容 sha256 去重，数据库只记录 object key 与业务归属。
- 物理路径位于 `%APPDATA%/FlightAnalyzer/objects/sha256/<prefix>/<sha256>.<ext>`。
- 业务展示路径由 `flight_id -> flight_raw_files -> file_objects` 动态解析，不能依赖机型/飞机/架次名称作为真实路径。
- 架次删除只删除引用，不立即删除 hash object；未引用 object 的清理由后续 GC 处理。

## 同步包协议

- `.fapkg` 本质为 zip，包内路径必须是相对路径，禁止绝对路径与 `..`。
- `manifest.json` 是跨环境契约；`data/parsed.sqlite` 只是同版本快速导入缓存。
- 包内固定结构：
  - `manifest.json`
  - `models/model_<source_model_id>.json`
  - `data/parsed.sqlite`
  - `objects/sha256/...`
- manifest 必须声明来源节点、环境、导出时间、机型、飞机、架次、raw object、parsed cache hash/size。
- 导入时以 manifest 为准，先校验 zip 路径和 raw/parsed hash；manifest 未声明的额外文件只记录 warning，不参与导入。

## 事务边界

- `parser.import_session()` 负责单次本地 session 导入事务，成功后统一 commit，失败回滚数据库。
- `backend/importer.py` 不主动 commit。
- raw object 已写入磁盘但数据库事务回滚时，允许留下未引用 object，后续由 GC 清理。
- 同步包兼容导入路径中，raw object 缺失或 hash 错误只记录 warning；parsed 数据仍可导入。
