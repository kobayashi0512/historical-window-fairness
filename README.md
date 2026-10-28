# Historical-Window Fairness for Dyadic Event Streams

中文题目：面向历史窗口公平的双端事件流增量责任分配。

该仓库提供论文的核心增量分配实现、独立核验、精确最少改派小规模 oracle、50 种子生成实验汇总结果，以及真实时间互动流的结果汇总。研究对象是**允许版本化回改的数字责任字段**；不用于推断真实责任主体，也不改变原始端点或时间戳。

## 方法概览

对每个端点的相邻事件进行两两配对，将“所有历史前缀的整数配额”转化为带符号约束图上的局部约束。每次新增事件后，算法仅在发生冲突时翻转较小相关分量。因此，在给定同一合法旧计划的条件下，算法实现该次更新的最少旧记录改派。

## 仓库内容

- src/：增量分配器、回执核验器、时间窗口审计、精确 oracle 与实验入口。
- tests/：枚举、随机流、回执一致性和 SQLite 条件更新测试。
- results/：论文使用的真实流汇总、50 种子结构实验汇总及冻结清单。
- data/DOWNLOAD_MANIFEST.json：公开输入的来源、校验和与再分发提示；原始互动记录不随仓库上传。

## 复现环境

核心实现只依赖 Python 标准库；精确混合整数规划 oracle 额外需要 numpy 和 scipy。建议使用 Python 3.10 或更高版本。

~~~
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m unittest discover -s tests -v
~~~

未下载公开数据时，涉及真实流的测试会被跳过，生成流、枚举和存储一致性测试仍可运行。下载时请遵从 data/DOWNLOAD_MANIFEST.json 中的来源与使用条件，将 email-Eu-core-temporal.txt.gz 和 CollegeMsg.txt.gz 放到 data/raw/，再执行：

~~~
PYTHONPATH=src python src/run_e2e.py --dataset emailEU --method StandardParityDSU --rep 0 --out runs/emailEU
~~~

## 已公开的结果范围

三条公开时间互动流的论文汇总见 results/REAL_THREE_STREAMS_TABLE.csv。生成结构矩阵使用 5 类流结构、3 个节点规模、3 个流长和每单元 50 个固定种子，共 2 250 条冻结生成流；汇总见 results/STRUCTURE_50SEED_TABLE.csv。

时间数值描述指定软硬件与存储设置下的端到端回放，不应解释为生产环境吞吐承诺或跨平台性能排序。

## 数据与伦理说明

仓库只提供代码、校验信息和派生汇总结果，不重新分发第三方原始互动数据。公开数据应从其原始发布方获取，并遵守相应的许可、引用和使用限制。医院接触数据仅用于事件结构回放，不用于临床人员责任判断。

## 作者与资助

邓凯文，刘立伟<br>
大连交通大学铁路智能工程学院

基金项目：辽宁省属高校基本科研业务费专项资金（LJ212410150016）。

## 引用

论文正式发表后将在此处补充期刊卷期、页码与 DOI。发表前请引用本仓库的具体 Git 提交版本。
