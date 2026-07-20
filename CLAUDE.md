# Flight Analyzer — 项目开发指南

## 项目概述

飞行数据分析应用：FastAPI 后端 + React 前端 + pywebview 桌面壳。



&nbsp;

## 数据与迭代约定

项目正处于快速迭代阶段，库中现有数据均为测试数据，无生产价值。为保持简洁，如确有必要，可直接修改（重建）数据库表结构，无需为兼容旧测试数据而做迁移或保留历史字段。

## 开发环境

```bash
# 后端：使用 .venv 虚拟环境
.venv/Scripts/activate
pip install -r requirements.txt

# 前端
cd frontend
npm install
npm run dev          # 开发模式（Vite HMR）
```

也可用 `script/run.ps1` 启动桌面端，或用 `script/run-server.ps1` 启动开发服务端。

## 打包

**最小依赖原则：用 `.venv` 打包，避免 Anaconda 全量环境导致体积膨胀和依赖冲突。**

```bash
script/build.ps1         # 构建前端 + PyInstaller，输出 packaging/dist/FlightAnalyzer.exe
script/build-server.ps1  # 打包服务端，输出 packaging/dist/server
```

frozen 模式（`console=False`）下 uvicorn 日志会因 `sys.stdout` 为 `None` 崩溃，修复见 `main.py:_build_log_config()` 的 docstring。
