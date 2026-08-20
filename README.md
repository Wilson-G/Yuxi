<div align="center">
  <img src="web/public/favicon.svg" width="88" alt="知铸 ZhiForge Logo" />
  <h1>知铸 ZhiForge</h1>
  <p><strong>把文章与经验，铸成专家的大脑</strong><br/>创建能理解方法、做出判断、说明条件并解释依据的专家 Agent</p>

[[项目文档]](docs/index.md) · [[专家 Agent]](docs/intro/expert-projects.md) · [[English README]](README.en.md)
</div>

## 简介

知铸是一个专家 Agent 制造平台。管理员先在独立资料库导入课程、文章、讲义、案例和企业经验，系统将材料处理为可靠正文和自然主题单元；随后从就绪单元创建专家项目，把材料中的判断方法、适用条件、例外边界和依据编成可启用的专家 Skill。

传统 RAG 擅长回答“相关内容在哪里”；知铸继续回答“面对这个具体问题应该怎样判断”。最终专家会识别关键信号，结合条件形成结论，解释为什么，也知道什么时候不能轻易下结论。

仓库、Python 包、环境变量、API 和 CLI 等技术标识暂时继续使用 `Yuxi` / `yuxi`；面向用户的默认产品名称使用“知铸”或“ZhiForge”。

## 主流程

```text
资料库上传与目录整理
-> OCR / 解析并形成可靠正文
-> 确认自然主题材料单元
-> 选择就绪单元创建专家项目
-> 确认学习方向
-> 逐份学习材料
-> 确认知识组织方式
-> 形成专家 Skill
-> 预览并手动启用
```

新增专家项目只从资料库选择已经处理完成的材料单元，不在项目内重复上传、OCR 或解析。旧知识库、向量检索和知识图谱继续作为显式兼容能力，不是新增专家项目的材料来源或运行依赖。

## 专家能力

- **理解方法**：学习材料中的分析路径、判断顺序和实践经验，而不是只抽取事实。
- **形成判断**：面对具体问题识别关键信号，从现象走向结论。
- **懂得条件**：理解前提、例外、分歧和专业边界，知道何时改变判断。
- **解释依据**：说明为什么这样判断，并让关键结论回到可靠材料核验。

## 比普通 RAG 更进一步

| 普通 RAG | 知铸专家 Agent |
| --- | --- |
| 检索与问题相似的知识片段 | 理解材料形成的知识体系与方法 |
| 围绕召回结果整理答案 | 结合信号、条件和经验形成判断 |
| 依赖片段是否覆盖例外 | 主动说明适用范围、例外与反转条件 |
| 给出来源引用 | 同时解释判断逻辑、材料依据与专业边界 |
| 最终得到可搜索的知识库 | 最终得到可以启用和使用的专家 Agent |

RAG、知识库和知识图谱仍可继续承担资料检索；知铸让可靠材料进入一个真正会判断的专家大脑。

## 平台能力

- 专家制造：资料库、自然材料单元、专家项目、预览与 Skill 启用。
- 智能体运行时：LangGraph、Skills、MCP、工具、子智能体、中间件与沙盒文件系统。
- 团队与集成：多租户权限、部门、共享范围、API Key、CLI 与可选 Langfuse 观测。
- 兼容知识能力：Milvus、Dify / Notion 连接器、知识图谱与 RAG 评估。

## 技术栈

| 层 | 技术 |
| --- | --- |
| 前端 | Vue 3 · Vite · Pinia |
| 后端 | FastAPI · LangGraph · ARQ |
| 核心存储 | PostgreSQL · Redis · MinIO |
| 兼容知识链路 | Milvus · Neo4j · Dify · Notion |
| 文档处理 | MinerU · PaddleX · RapidOCR |
| 部署 | Docker Compose |

## 快速开始

前置要求：Docker、Docker Compose，以及至少一个兼容 OpenAI 接口的模型 API。

```bash
git clone <repository-url> <repo-directory>
cd <repo-directory>

# Linux/macOS
./scripts/init.sh

# Windows PowerShell
.\scripts\init.ps1

docker compose up --build -d
```

浏览器打开 `http://localhost:5173`，按页面引导创建管理员并配置模型。完整说明见[快速开始](docs/intro/quick-start.md)。默认 Compose 使用 `LITE_MODE=true`；旧知识库兼容能力通过 `legacy-knowledge` profile 显式启动。

## 参与贡献

贡献流程和开发约定见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可证

本项目采用 MIT 许可证，详见 [LICENSE](LICENSE)。
