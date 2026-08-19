<div align="center">
<h1>知铸 ZhiForge</h1>

<p><strong>把可靠材料学习成真正可用的专家 Skill</strong><br/>从确认怎么学，到并行学习、Review、总编成书和手动启用</p>

[![](https://img.shields.io/badge/Docker-2496ED?style=flat&logo=docker&logoColor=ffffff)](https://github.com/xerrors/Yuxi/blob/main/docker-compose.yml)
[![](https://img.shields.io/github/issues/xerrors/Yuxi?color=F48D73)](https://github.com/xerrors/Yuxi/issues)
[![License](https://img.shields.io/github/license/bitcookies/winrar-keygen.svg?logo=github)](https://github.com/xerrors/Yuxi/blob/main/LICENSE)
[![DeepWiki](https://img.shields.io/badge/DeepWiki-blue.svg)](https://deepwiki.com/xerrors/Yuxi)
[![Bilibili](https://img.shields.io/badge/历史知识库演示-00A1D6?logo=bilibili&logoColor=fff)](https://www.bilibili.com/video/BV1erE26iEgv/?share_source=copy_web&vd_source=37b0bdbf95b72ea38b2dc959cfadc4d8)


<a href="https://trendshift.io/repositories/24335" target="_blank"><img src="https://trendshift.io/api/badge/repositories/24335" alt="xerrors%2FYuxi | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/></a>

[[项目文档]](https://xerrors.github.io/Yuxi) · [[版本特性]](http://xhslink.com/o/5Y6QWnmjF2d)

</div>

## 简介

知铸（ZhiForge，仓库与技术标识暂保留 `Yuxi`）是一个专家学习与 Skill 编译平台。它先将 PDF、Office、Markdown 和图片等材料转为可靠正文，再让模型学习其中的判断方法、成立条件、例外与边界，最终编成可启用、可检索、可核对依据的独立专家 Skill。

默认用户流程是：

```text
材料转正文
-> 项目定向与 Prompt 设计
-> 用户确认“怎么学”
-> 逐份学习与 Review
-> 总编组织主题和讲义小样
-> 用户确认“怎么组织”
-> 总编成书
-> 预览并手动启用专家 Skill
```

普通模型任务负责并行阅读、笔记、Review 和证据准备；同一项目内的总编模型串行决定学习方案、主题结构、正式讲义和最终回答口径。用户在两个真正需要方向判断的位置确认，系统依据项目记录通过 ARQ 持续执行和恢复。

旧知识库、向量检索和知识图谱能力仍作为可选兼容模式保留，不是新专家项目的默认运行链路。

导航：[项目介绍](https://xerrors.github.io/Yuxi/) ｜ [快速开始](https://xerrors.github.io/Yuxi/intro/quick-start) ｜ [开发路线图](https://xerrors.github.io/Yuxi/develop-guides/roadmap) | [0.7 版本特性](http://xhslink.com/o/5Y6QWnmjF2d)；最新开发动态，详见 [changelog](https://xerrors.github.io/Yuxi/develop-guides/changelog)。

> 📢 求职：作者为江南大学软件工程博士研究生，研究方向 AI Agent、知识图谱与大模型应用，预计 2027 年毕业，现寻求实习/全职机会，欢迎联系：wenjie.zhang@stu.jiangnan.edu.cn

---

![image-20260606190609377](https://xerrors.oss-cn-shanghai.aliyuncs.com/github/image-20260606235615139.png)

## 技术栈

| 层 | 技术 |
| --- | --- |
| 前端 | Vue 3 · Vite · Pinia |
| 后端 | FastAPI · LangGraph · ARQ (异步 worker) |
| 存储 | PostgreSQL · Redis · MinIO（Milvus · Neo4j 为兼容模式可选依赖） |
| 文档解析 | MinerU · PaddleX · RapidOCR |
| 部署 | Docker Compose |
## 快速开始

**前置要求**：已安装 [Docker](https://docs.docker.com/get-docker/) 与 Docker Compose，并准备至少一个兼容 OpenAI 接口的大模型 API。

**1. 克隆代码并初始化**

```bash
git clone --depth 1 https://github.com/xerrors/Yuxi.git
cd Yuxi

# Linux/macOS
./scripts/init.sh

# Windows PowerShell
.\scripts\init.ps1
```

**2. 使用 Docker 启动**

```bash
docker compose up --build
```

**3. 访问平台**

等待启动完成后，浏览器打开 `http://localhost:5173`，使用初始化时生成的管理员账户登录即可。

> 💡 默认 Compose 已使用 `LITE_MODE=true`，不启动 Milvus、etcd 和 Neo4j。如需旧知识库兼容能力，使用 `LITE_MODE=false docker compose --profile legacy-knowledge up --build`。更多部署说明见 [项目文档](https://xerrors.github.io/Yuxi)。

## 致谢

本项目参考并引用了以下优秀开源项目，在此致以诚挚的感谢：

- [LightRAG](https://github.com/HKUDS/LightRAG) - 早期版本曾参考其图谱构建与检索思路；当前 Yuxi 已实现自研 Milvus 知识库/图谱链路以替换历史集成，降低兼容性问题
- [DeepAgents](https://github.com/langchain-ai/deepagents) - 直接引入作为深度智能体框架
- [DeerFlow](https://github.com/bytedance/deer-flow) - 参考了其 Sandbox 智能体架构的实现思路
- [RAGflow](https://github.com/infiniflow/ragflow) - 参考了其文档 Text Chunking 的分块策略
- [LangGraph](https://github.com/langchain-ai/langgraph) - 多智能体编排框架，本项目的核心架构基础
- [QwenPaw](https://github.com/agentscope-ai/QwenPaw) - 参考模型配置与个人文件区域设计

## 参与贡献

感谢所有贡献者的支持！

<a href="https://github.com/xerrors/Yuxi/contributors">
  <img src="https://contrib.rocks/image?repo=xerrors/Yuxi&max=100&columns=10" />
</a>


## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=xerrors/Yuxi)](https://star-history.com/#xerrors/Yuxi)

## 📄 许可证

本项目采用 MIT 许可证 - 查看 [LICENSE](LICENSE) 文件了解详情。

---

<div align="center">

**如果这个项目对您有帮助，请不要忘记给我们一个 ⭐️**

</div>
