# Drawing2Model

工程图纸 → 3D 模型 + CNC G-Code + 制造报告 的智能重建服务。

上传机械零件工程图 PDF（叶片/阶梯轴/滚柱/箱体壳体等），服务通过 **VLM 识别 →
LLM 自主生成建模代码 → 沙箱执行 → cadquery 几何校验 + 视觉比对迭代 → 择优交付**
的闭环，产出 STL/GLB/STEP/OBJ/DXF、三视图投影与制造报告。

## 架构

```
                  ┌─────────────────────────────────────────┐
   Web UI (React) │  FastAPI (server.py, :8410)              │
  ───────────────►│   /api/jobs*        REST 任务管理         │
                  │   /mcp/sse          MCP SSE (StaffDeck 等)│
                  │   /                 静态托管 web/dist     │
                  └───────┬─────────────────────────────────┘
                          │
            ┌─────────────┴──────────────┐
            │ pipeline (零件族参数收敛)     │  evolve (LLM 自主写代码, 任意零件)
            └─────────────┬──────────────┘
                          ▼
   S1 预处理(+无损高清化) → S2 VLM 识别 → S3 PartSpec → S4/S5 建模·收敛回路 → S6 报告

   收敛回路 (evolve, 每版本 v{n}):
     LLM 生成 cadquery/OCP worker 代码
       → harness 沙箱执行 (静态扫描 + 隔离子进程) → STL + STEP
       → cadcheck 几何校验 (拓扑/体积/包围盒)
       → 多视角渲染 + VLM 视觉比对 → score + issues
       → 未达标: 差异喂回 LLM 修代码 (可扩展预算至 12 轮, 轮内重写 3 次)
     交付策略: 分数≥0.9 可携遗留问题交付 (几何硬伤除外); 分数≥0.8 且无遗留问题常规收敛;
              交付前 OCP 体积自检 + cadquery 几何校验双保险, 失败触发自愈轮
```

## 目录结构

```
server.py            FastAPI 服务入口 (REST + MCP + Web 静态托管, 默认 :8410)
mcp_server.py        MCP 工具定义 (drawing2model / d2m_status / d2m_artifacts / d2m_health)
app/                 核心逻辑
  runtime.py         运行时解释器路径解析 (跨平台, 环境变量可覆盖)
  pipeline.py        六阶段流水线编排 (零件族参数收敛模式)
  evolve.py          LLM 自主生成 worker 代码 + 多版本迭代闭环
  harness.py         worker 沙箱执行器 (危险模式扫描 + 隔离子进程)
  cadcheck.py        cadquery/OCP 几何校验 (STEP 读回: 拓扑/体积/包围盒)
  convergence.py     收敛判定与视觉比对
  enhance.py         图纸无损高清化 (扫描型原样提取/矢量型高倍率渲染)
  jobs.py            任务管理 (提交/查询/删除/日志落盘)
  artifacts.py       统一产物生成 (GLB/STEP/OBJ/DXF/投影/G-Code)
  report.py          HTML 制造报告
  render_views.py    STL 多视角渲染 (matplotlib)
  gcode.py           G-Code 生成
llm/gateway.py       LLM 调用收口 (OpenAI 兼容, token 用量统计, 离线降级)
families/            参数化零件族建模器 (blade/shaft/roller/box_housing)
templates/           报告 Jinja2 模板 + 前端 vendor 资源
web/                 React + Vite Web UI (dist/ 为构建产物, 随仓库分发)
deploy/              Ubuntu 一键部署: deploy.sh / ctl.sh / drawing2model.service
cases/ output/       案例库 / 任务产物目录 (运行时生成, 不入库)
```

## 环境变量

| 变量 | 说明 | 默认值 |
|---|---|---|
| `LLM_API_KEY` | OpenAI 兼容 API 凭证 (兼容别名 `DASHSCOPE_API_KEY`); 留空 = 离线模式 | 空 |
| `LLM_BASE_URL` | OpenAI 兼容 endpoint | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `LLM_MODEL` | 通用模型 (同时用于视觉+文本) | `qwen-vl-max` |
| `LLM_VL_MODEL` / `LLM_TEXT_MODEL` | 可选, 单独覆盖视觉/文本模型 | 同 `LLM_MODEL` |
| `D2M_WORKER_PY` | worker 沙箱解释器 (需装 cadquery + OCP) | Windows: 托管 python; Linux: 当前解释器 |
| `D2M_UTIL_PY` | 工具脚本解释器 (stl2glb/渲染等) | 同上 |
| `D2M_OCP_PYTHONPATH` | worker 子进程 PYTHONPATH (Windows `--target` 目录; Linux venv 留空) | 平台默认 |
| `MCP_PORT` | 独立 MCP 进程端口 (仅单独运行 mcp_server.py 时) | `8411` |

> 注意: `server.py` 已把 MCP SSE 挂在 `/mcp` 下与 REST 同端口, 通常无需单独跑
> `mcp_server.py`。

## 本地运行 (Windows 开发机)

```powershell
# 1) 创建虚拟环境 (需 Python >= 3.10, 推荐 3.12)
python -m venv venv
.\venv\Scripts\Activate.ps1

# 2) 安装依赖
pip install -r requirements.txt           # 主服务 (FastAPI/识别/渲染/报告)
pip install cadquery numpy pymupdf pillow  # 建模内核 (worker 沙箱执行 LLM 代码用)

# 3) 前端构建 (需在真实路径下, 符号链接会触发 rollup 报错)
cd web; npm install; npm run build; cd ..

# 4) 指定 worker/util 解释器为当前 venv (覆盖默认的托管 python 路径)
$env:LLM_API_KEY = "sk-0884061a7ead43e7be2a5ab436df103c"
$env:D2M_WORKER_PY = "$PWD\venv\Scripts\python.exe"
$env:D2M_UTIL_PY   = "$PWD\venv\Scripts\python.exe"
# 5) 启动服务 (默认 :8410)
python server.py
```

Web UI: `http://127.0.0.1:8410` · API 文档: `http://127.0.0.1:8410/docs`

## Ubuntu 一键部署 (服务器目录 /data/drawing2model)

要求: Ubuntu 20.04+ x86_64, python3 ≥ 3.10 (推荐 3.12, cadquery 轮子要求),
`python3-venv`。

```bash
# 1) 克隆代码
git clone ssh://git@192.168.15.96:2222/rd/drawing2model.git
cd drawing2model

# 2) 一键部署 (装依赖/建 venv/装 systemd 服务/启动)
sudo ./deploy/deploy.sh
# 国内网络可加镜像:
# PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple sudo ./deploy/deploy.sh

# 3) 填写 LLM 密钥后重启
sudo vi /data/drawing2model/.env          # 填 LLM_API_KEY
/data/drawing2model/deploy/ctl.sh restart
```

部署脚本做的事:
1. 代码复制到 `/data/drawing2model` (保留已有 `.env` 与 `output/` 产物)
2. 创建 `venv_server` (主服务) 与 `venv_worker` (cadquery + OCP 建模内核)
3. 安装 `drawing2model` systemd 服务并设为开机自启
4. 以专用用户 `drawing2model` 运行, 启动后自动健康检查

重复执行 `deploy.sh` 即为**升级部署** (幂等): 停服 → 更新代码与依赖 → 启服。

## 服务管理

```bash
/data/drawing2model/deploy/ctl.sh start     # 启动
/data/drawing2model/deploy/ctl.sh stop      # 停止
/data/drawing2model/deploy/ctl.sh restart   # 重启
/data/drawing2model/deploy/ctl.sh status    # 状态 + 健康检查
/data/drawing2model/deploy/ctl.sh logs      # 跟踪日志
/data/drawing2model/deploy/ctl.sh version   # 当前前端构建版本
# 等价原生命令
systemctl start|stop|restart|status drawing2model
journalctl -u drawing2model -f
```

## 接口

### REST (`:8410`)

| 方法 | 路径 | 说明 |
|---|---|---|
| `POST` | `/api/jobs` | 提交重建任务 (multipart: pdf + mode + max_rounds) |
| `GET` | `/api/jobs` | 任务列表 |
| `GET` | `/api/jobs/{id}` | 任务详情 (状态/日志/历史/产物清单) |
| `DELETE` | `/api/jobs/{id}` | 删除单个任务 (运行中拒绝) |
| `POST` | `/api/jobs/delete-batch` | 批量删除 `{ids:[...]}` |
| `GET` | `/api/jobs/{id}/artifacts/{kind}` | 产物下载 (glb/stl/step/obj/dxf/report/...) |
| `GET` | `/api/jobs/{id}/artifacts.zip` | 全部产物打包 |
| `GET` | `/api/status` | LLM 状态 + 任务表 |

### MCP SSE (`:8410/mcp/sse`)

工具: `drawing2model` (提交重建) / `d2m_status` (查询) / `d2m_artifacts` (产物路径) /
`d2m_health` (健康检查)。客户端配置示例:

```json
{ "mcpServers": { "drawing2model": { "url": "http://<服务器IP>:8410/mcp/sse" } } }
```

## 交付策略 (evolve 模式)

- **快速交付线**: 视觉评分 ≥ **0.9** 时即使有遗留问题也直接交付 (几何硬伤除外);
- **常规收敛线**: 评分 ≥ 0.8 且无遗留问题;
- 两条线未满足时自动扩展迭代预算 (上限 12 轮), 不因轮次上限带低分草草交付;
- 每轮与任务结束的 token 消耗记录在任务日志中。

## 说明

- `web/dist/` 构建产物随仓库分发, 服务器无需 node 即可部署; 修改前端后需本机
  在真实路径下 `npm run build`, 并同步升级 `web/src/main.jsx` 的 `BUILD_ID` 与
  `web/index.html` 的 `data-build` (两处不一致会触发浏览器硬刷新自愈)。
- 沙箱安全: LLM 生成的 worker 代码执行前经静态危险模式扫描 (系统调用/网络/动态
  执行类), 并在剥离凭证的最小环境变量子进程中运行, 只允许写任务目录内产物。
