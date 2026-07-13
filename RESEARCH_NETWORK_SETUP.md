# 科研网统一服务器部署流程

本流程不依赖 Docker。统一服务器运行 `server_app.py`，使用 MySQL 保存共享数据；本地软件仍使用本机 SQLite 缓存，通过服务器 API 同步。

## 1. 准备配置文件

复制模板并填写真实值：

```powershell
Copy-Item .\flight_analyzer.ini.example .\flight_analyzer.ini
```

关键项：

- `server.host = 0.0.0.0`：服务器监听科研网网卡。
- `server.port = 9000`：统一服务器 API 端口。
- `server.data_dir`：服务器保存上传原始文件和同步包的位置。
- `mysql.*`：MySQL 地址、库名、账号、密码。
- `local.server_base_url = http://服务器IP或域名:9000/api`：本地软件连接统一服务器的地址，必须带 `/api`。

本地模拟同一台机器时可先填：

```ini
[local]
server_base_url = http://127.0.0.1:9000/api
```

## 2. 在统一服务器上安装 MySQL 并建库

安装 MySQL 8.x 后，用 root 登录 MySQL，执行：

```sql
CREATE DATABASE flight_analyzer
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

CREATE USER 'flight'@'localhost' IDENTIFIED BY '你的强密码';
GRANT ALL PRIVILEGES ON flight_analyzer.* TO 'flight'@'localhost';
FLUSH PRIVILEGES;
```

然后把 `flight_analyzer.ini` 里的 `mysql.password` 改成同一个密码。

如果 MySQL 和 `server_app.py` 不在同一台机器，把 `mysql.host` 改为 MySQL 服务器地址，并按你的网络策略放通 MySQL 端口。推荐优先让 MySQL 只对应用服务器可见，不直接暴露给所有本地客户端。

## 3. 启动统一服务器

在服务器机器进入项目目录：

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements-server.txt
python server_main.py
```

首次启动会自动创建表，并创建内置管理员：

- 用户名：`admin`
- 初始密码：`123456`

启动后在服务器本机验证：

```powershell
Invoke-RestMethod http://127.0.0.1:9000/api/health
```

在科研网内其他电脑验证：

```powershell
Invoke-RestMethod http://服务器IP:9000/api/health
```

如果本机可访问、其他电脑不可访问，检查服务器防火墙是否放通 TCP `9000`。

## 4. 启动本地软件连接科研网服务器

在本地电脑的项目目录放置 `flight_analyzer.ini`，至少填写：

```ini
[local]
server_base_url = http://服务器IP:9000/api
```

然后正常启动本地软件：

```powershell
.\run.ps1
```

或开发模式分别启动前后端：

```powershell
.\.venv\Scripts\Activate.ps1
python main.py
```

打开应用后进入同步页面，应看到服务器状态为 `online`。在同步页面右上角用服务器账号登录；第一次可用 `admin / 123456` 登录，随后立即修改密码并创建普通用户。

## 5. 本地模拟完整链路

在同一台电脑上模拟“科研网客户端连接统一服务器”：

1. 安装并启动本机 MySQL。
2. `flight_analyzer.ini` 中保持：

```ini
[local]
server_base_url = http://127.0.0.1:9000/api

[server]
host = 127.0.0.1
port = 9000
```

3. 开一个终端启动统一服务器：

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements-server.txt
python server_main.py
```

4. 另开一个终端启动本地软件：

```powershell
.\run.ps1
```

5. 在同步页确认 `online`，登录服务器账号，执行“只上传”“从服务器拉取到本地”或“同步一次”。

## 6. 运行方式说明

也可以继续用环境变量覆盖配置文件，例如临时换服务器：

```powershell
$env:SERVER_BASE_URL = "http://10.0.0.12:9000/api"
.\run.ps1
```

配置文件查找顺序：

1. `FLIGHT_ANALYZER_CONFIG` 指定的路径。
2. 打包后 exe 所在目录的 `flight_analyzer.ini`。
3. 当前工作目录的 `flight_analyzer.ini`。
4. 打包时嵌入的 `flight_analyzer.ini`。
5. 项目根目录的 `flight_analyzer.ini`。

## 7. 打包前内置服务器机型

如果软件要在完全不能连接服务器的地方首次使用，可以在打包前从统一服务器生成内置机型 seed：

```powershell
.\.venv\Scripts\python.exe .\tools\generate_builtin_model_seeds.py
```

生成文件：

```text
backend\builtin_model_seeds.json
```

之后再执行正常打包：

```powershell
cd frontend && npm run build && cd ..
.\.venv\Scripts\pyinstaller FlightAnalyzer.spec
```

打包后的软件第一次启动时，`init_db()` 会自动读取 `backend\builtin_model_seeds.json`，把缺失的机型、数据类型、列定义和动态数据表写入本地 `%APPDATA%\FlightAnalyzer\data.db`。如果本地已经存在同 `server_id`、`client_uid` 或同名机型，则跳过，不覆盖用户已有配置。

工具默认读取 `flight_analyzer.ini`：

```ini
[local]
server_base_url = http://服务器IP:9000/api

[server_auth]
username = admin
password = 你的服务器登录密码
```



&nbsp;

&nbsp;

# 其他

清空数据

```
.venv/Scripts/python tools/reset_project_data.py --scope all --yes
```



&nbsp;
