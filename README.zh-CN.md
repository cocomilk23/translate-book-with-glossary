# translate-book-with-glossary

这是一个面向整书翻译的技能，适合把 `PDF / DOCX / EPUB` 转成 Markdown 分块后，结合术语表进行并行翻译、批次术语对齐、合并构建和术语 QA。它的重点不是“把一本书一次性机翻完”，而是把长文本翻译里最容易失控的术语一致性单独收紧，让结果更接近可直接交付的定稿。

## 它解决什么问题

普通整书机翻常见的问题有：

- 分块并行后，同一术语被译成多个版本
- 前面几章没出现的术语，后面各块各自做决定，越翻越漂
- 合并后很难快速定位哪些词还没定稿
- 最终产物能看，但不适合继续加工成 `docx / epub / pdf`

这个 skill 的做法是：

- 先把原书切成结构化 chunk
- 从前几个 chunk 启动一本书自己的 `glossary.csv`
- 每个并行子任务翻译时都受 glossary 约束
- 新术语必须做显式决策，并写成结构化 sidecar
- 每一批并行结束后统一裁决术语，再回写这一批 chunk
- 最后合并、构建成书，并做一次保守的术语 QA

## 当前工作流

完整逻辑定义在 [SKILL.md](./SKILL.md)。简化后是 7 步：

1. `convert.py`
   把输入书籍转换成 `chunk0001.md ...` 和 `manifest.json`

2. `extract_terms.py`
   从前几个 chunk 抽取 bootstrap 术语，生成 `glossary.csv` 和 `terminology_candidates.md`

3. 并行翻译
   每个子任务读取 `glossary.csv`，翻译自己的 chunk，并把新术语决策写入 `term_decisions_chunkNNNN.csv`

4. `reconcile_terms.py`
   对一批 chunk 的新术语做统一裁决，把赢家锁进 `glossary.csv`

5. `glossary_apply.py`
   把这一批已经翻好的 `output_chunkNNNN.md` 回写成统一术语

6. `merge_and_build.py`
   校验所有 chunk 后合并成 `output.md`，并构建 `book.docx / book.epub / book.pdf`

7. `glossary_check.py`
   输出 `terminology_report.md`，报告未解决草稿项、禁用变体和批次术语决策

## 主要产物

运行过程中，书的 temp 目录里通常会出现这些文件：

- `chunk0001.md ...`
- `output_chunk0001.md ...`
- `manifest.json`
- `glossary.csv`
- `terminology_candidates.md`
- `term_decisions_chunkNNNN.csv`
- `terminology_batch_report.md`
- `terminology_report.md`
- `output.md`
- `book.docx / book.epub / book.pdf`

其中最关键的是：

- `glossary.csv`
  一本书的术语源数据，也是后续翻译约束和术语回写的依据
- `term_decisions_chunkNNNN.csv`
  每个 chunk 对新增术语做出的显式决策
- `terminology_batch_report.md`
  每批并行后的术语裁决结果

## 适用内容

最适合：

- 英文技术书
- 教程、白皮书、说明文档
- 概念体系清晰、术语一致性要求高的长文本

也能用于宗教、社科、人文类书稿，但这类文本对语气、修辞和上下文承接更敏感，仍建议人工抽查关键章节。

不适合直接指望全自动高质量定稿的内容：

- 强文学性作品
- 高度依赖文风连续性的散文、小说
- OCR 质量很差的扫描版 PDF

## 依赖

系统依赖：

- `python3`
- `pandoc`
- `ebook-convert`（Calibre）

Python 依赖：

- `pypandoc`
- `markdown`
- `beautifulsoup4`

如果你要完整产出 `docx / epub / pdf`，Calibre 是必需的。

## 设计取舍

这个 skill 的核心取舍是：

- bootstrap 术语抽取只求“够干净、够保守”，不追求语义完美
- 真正的定稿动作发生在批次翻译之后的 `reconcile_terms.py`
- glossary 里的 `locked` 项才是稳定约束，`draft` 只是候选池
- 最终 auto-normalize 默认是保守串行，不做激进全文重写

现在的逻辑已经比早期版本更稳：

- bootstrap 会主动过滤部分页眉、标题页和半截短语噪声
- 新术语主流程已经改为结构化 sidecar，而不是自由文本候选
- 批次后会统一术语并回写本批 chunk，避免并行漂移累积

## 生产建议

如果要直接拿去跑生产，建议遵守这几条：

- 固定走新流程：
  `extract_terms.py -> term_decisions_chunk*.csv -> reconcile_terms.py -> glossary_apply.py`
- 不要把 bootstrap 结果直接当最终术语表
- 把 `glossary.csv` 里高频核心术语尽早锁成 `locked`
- 每批并行结束后都执行术语裁决和回写，不要跳过
- 最终交付前至少看一遍 `terminology_report.md`

## 仓库结构

- [SKILL.md](./SKILL.md)：技能入口和完整编排说明
- [scripts/convert.py](./scripts/convert.py)：输入文档转 Markdown 并切块
- [scripts/extract_terms.py](./scripts/extract_terms.py)：bootstrap 术语抽取
- [scripts/reconcile_terms.py](./scripts/reconcile_terms.py)：批次术语裁决
- [scripts/glossary_apply.py](./scripts/glossary_apply.py)：术语统一回写
- [scripts/glossary_check.py](./scripts/glossary_check.py)：术语 QA
- [scripts/merge_and_build.py](./scripts/merge_and_build.py)：合并并生成最终书稿
- [scripts/calibre_html_publish.py](./scripts/calibre_html_publish.py)：Calibre 发布封装

## 说明

README 只给出高层说明。真正执行时，以 [SKILL.md](./SKILL.md) 为准。
