# CLI Commands

本文件汇总当前项目已经集成的命令，便于日后直接查找。

## 准备

先进入项目目录并激活虚拟环境：

```bash
cd /home/asteroid/stockagent
source .venv/bin/activate
```

如果 `stockagent` 命令还不可用，先执行：

```bash
pip install -e .
```

## 日常最常用

更新中证500候选池：

```bash
stockagent fetch csi500
```

生成今天的分析报告：

```bash
stockagent analyze today
```

查看最新一份报告：

```bash
stockagent latest
```

查看当前候选池：

```bash
stockagent show candidates
```

## 常见使用顺序

第一次或需要刷新股票池时：

```bash
stockagent fetch csi500
stockagent show candidates
stockagent analyze today
stockagent latest
```

平时如果候选池已经准备好：

```bash
stockagent analyze today
stockagent latest
```

## 命令说明

`stockagent fetch csi500`
- 从中证500成分股来源抓取股票列表。
- 会把结果写入 `inputs/candidates.json`。

`stockagent fetch csi500 --limit 50`
- 只抓取前 50 只，适合快速测试。

`stockagent analyze today`
- 基于当前持仓、候选池和默认配置生成今日分析。
- 当前支持空仓运行。

`stockagent latest`
- 查看数据库里最新保存的一份报告。

`stockagent show candidates`
- 列出当前候选池股票代码。

## 兼容的旧命令

这些命令仍然可以用：

```bash
stockagent report
stockagent baseline
stockagent backtest --start-date 2026-03-01 --end-date 2026-04-01
stockagent plan-orders --report-id <report_id>
stockagent execute-mock --report-id <report_id>
```

## 如果命令失败

查看帮助：

```bash
stockagent --help
stockagent fetch --help
stockagent analyze --help
```

如果提示找不到命令，优先检查：

```bash
source .venv/bin/activate
pip install -e .
```
