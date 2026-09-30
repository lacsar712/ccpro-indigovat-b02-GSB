# IndigoVat-01 · 染缸还原台

FastAPI + PostgreSQL + Jinja2：主界面是横向**缸位条**（Alpine 反应式），不是工坊/染缸/批次三表导航。Session Cookie 登录；规则在 `app/services/vat_rules.py`。

## 技术栈

- FastAPI、SQLAlchemy 2、PostgreSQL
- 启动时 `create_all` + 幂等种子（蓝靛湾一号坊 / 清水江二号坊）
- Session Cookie 认证（Starlette SessionMiddleware）
- Jinja2 + Alpine.js + Pico（叠靛蓝水墨自定义样式）
- Docker Compose：`web` + `db`

## 端口与数据库

| 服务 | 端口 |
|------|------|
| Web  | **4720** |
| Postgres | **6120**（容器内 5432） |

数据库账号：`indigovat` / `indigovat` / 库名 `indigovat`

## 快速启动

```bash
cd IndigoVat/IndigoVat-01
docker compose up --build -d
```

浏览器打开：http://localhost:4720

演示账号（登录页已预填）：

- `admin` / `123456`（主管，可改挂染缸工坊）
- `worker` / `123456`（染缸工，仅登记浸染与改状态，发起改挂一律拒绝）

## 交互（信息架构）

1. **染缸还原台**：横滑缸位条，每缸显示状态、最近电位与 redox sparkline
2. **工坊 chip**：仅作缸位筛选，无独立工坊 CRUD 页
3. **点缸展开**：同页内登记浸染批次、改状态、看近几笔；无平行「染缸表 / 批次表」
4. **主管改挂工坊**：在展开区把染缸整笔改挂到另一工坊

**业务规则**：

- 状态改为 `ready`（可染色）时，最新批次 `redoxMv` 须已填且 ≤ -500（见 `vat_rules.py`）。
- **改挂工坊**（`POST /bay/vats/{id}/reassign`，仅主管）：
  - **缸号冲突整笔拒绝**：目标坊已占用相同缸号 `code` 时，不迁、不改号，整笔回退；
  - **可染色缸禁迁**：`ready` 状态正在作业，须先改回闲置或还原中；
  - **染缸工无权**：`worker` 发起改挂返回 403，会话保留、还原台仍可打开；
  - **并发兜底**：两主管近乎同时把不同闲置缸改挂进同一目标坊且撞同一缸号时，数据库唯一约束 `uniq_vat_code_per_workshop` 保证至多一笔成功，败者得到 409 冲突提示并回退；
  - **历史随缸**：浸染批次挂在染缸主键上，改挂只改 `workshop_id`，历史仍在原缸可查；
  - 成功后跳回目标坊 chip 并展开该缸，原坊 chip 下不再出现该缸，缸位条工坊名同步更新。

## 种子缸位布局（改挂演示）

| 工坊 | 缸号 | 状态 | 用途 |
|------|------|------|------|
| 蓝靛湾一号坊 | V-01 | 还原中 | 常规 |
| 蓝靛湾一号坊 | V-02 | 闲置 | 常规 |
| 蓝靛湾一号坊 | **V-03** | 闲置 | 试图迁入清水江会撞 **V-03** 冲突号 → 整笔拒绝 |
| 蓝靛湾一号坊 | **V-04** | 闲置（带浸染历史） | 清水江 V-04 号预留为空 → **可成功改挂**，历史随缸 |
| 清水江二号坊 | V-11 | 还原中 | 常规 / 可反向迁入 |
| 清水江二号坊 | V-12 | 可染色 | 演示可染色缸禁迁 |
| 清水江二号坊 | **V-03** | 闲置 | 预留冲突号（占用） |
| 清水江二号坊 | （V-04 空缺） | — | 预留可迁号 |

## 本地开发（可选）

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
set POSTGRES_HOST=localhost
set POSTGRES_PORT=6120
uvicorn app.main:app --host 0.0.0.0 --port 4720 --reload
```

## 业务模型

1. **Workshop**：`name`、`region`、`notes`（UI 上仅为筛选片）
2. **Vat**：归属工坊、`code`、`dyeType`、`volumeL`、状态 `idle|reducing|ready`
3. **DipLot**：归属染缸、`dippedAt`、`clothMeters`、`redoxMv`（可空）

## 目录结构

```
IndigoVat-01/
  Dockerfile
  entrypoint.sh
  docker-compose.yml
  requirements.txt
  app/
    main.py
    db.py
    models.py
    schemas.py
    auth.py
    seed.py
    routers/
    services/vat_rules.py
    templates/   # base / bay / login
```
