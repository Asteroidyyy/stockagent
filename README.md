# StockAgent

面向 A 股的股票市场分析 agent，第一版目标是生成收盘后的日报和仓位建议，不直接执行下单。

## MVP 目标

- 市场范围：A 股
- 交易周期：日线波段
- 更新频率：每日收盘后
- 输出内容：
  - 市场状态摘要
  - 行业/板块观察
  - 持仓分析
  - 加仓/减仓/维持建议
  - 候选股票观察名单
  - 风险提示

## 设计原则

- 规则和数据决定建议，LLM 负责解释和整合
- 第一版先做研究辅助，不做自动下单
- 先支持收盘后分析，再逐步扩展到盘中预警
- 先支持有限股票池，再扩展到全市场扫描

## 技术栈

- Python 3.11+
- FastAPI：API 服务
- Pydantic Settings：配置管理
- SQLAlchemy：数据库访问
- PostgreSQL：结构化数据存储
- Redis：缓存与任务状态
- pandas / numpy：数据处理
- AkShare / Tushare：数据源
- OpenAI API：日报解释与建议生成

## 快速开始

1. 创建虚拟环境并安装依赖
2. 复制 `.env.example` 为 `.env`
3. 配置 `OPENAI_API_KEY` 和数据源凭证
4. 运行 API 或命令行任务

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
pip install -e ".[dev]"
cp .env.example .env
PYTHONPATH=src python -m stockagent.cli
uvicorn stockagent.api:app --reload --app-dir src
```

如果要启用真实 A 股数据，把 `.env` 里的 `DATA_PROVIDER=akshare`。
默认仍然使用 `mock`，这样在没安装依赖、没联网或 AkShare 不可用时，仓库也能先跑通。
默认股票池配置为 `UNIVERSE_NAME=csi500`，并通过 `UNIVERSE_LIMIT` 控制每次加载的候选数量。

真实数据模式示例：

```bash
cp .env.example .env
PYTHONPATH=src DATA_PROVIDER=akshare UNIVERSE_NAME=csi500 UNIVERSE_LIMIT=20 python -m stockagent.cli
```

## 当前状态

当前仓库包含：

- 可扩展的 Python 包结构
- 一条最小日报生成链路
- Mock 数据提供器
- 基础规则打分逻辑
- FastAPI 接口和命令行入口
- AkShare 与 Tushare 两种行情 provider
- 公告事件归一化、日报历史回放、评估、PDF 导出
- 历史日报模拟和窗口回测
- Redis 优先、JSON 回退的缓存与任务状态记录
- mock/paper 订单计划与执行链路

当前仓库还不包含：

- 真实券商下单适配器
- 异步任务队列
- 完整数据库迁移体系
- 覆盖这些链路的自动化测试

## 下一步优先级

1. 接入指定券商 API，替换 mock 执行器
2. 增加异步任务和前端控制台
3. 补齐自动化测试与 CI
4. 扩展 Tushare 财务/估值因子
5. 增加真实交易前的风控审批流
