<div align="center">
  <img src="web/public/favicon.svg" width="88" alt="ZhiForge logo" />
  <h1>ZhiForge 知铸</h1>
  <p><strong>Forge articles and experience into an expert mind</strong><br/>Create expert Agents that understand methods, make judgments, respect conditions, and explain evidence</p>

[[Docs]](docs/index.md) · [[Expert Agents]](docs/intro/expert-projects.md) · [[中文]](README.md)
</div>

## Introduction

ZhiForge is an expert-Agent foundry. Administrators first import courses, articles, handouts, cases, and organizational experience into an independent material library. The system turns those files into reliable text and natural topic units. An expert project then selects ready units and compiles their judgment methods, conditions, exceptions, and evidence into an expert Skill.

Traditional RAG answers “where is the relevant information?” ZhiForge continues with “how should this situation be judged?” A useful expert identifies important signals, applies conditional reasoning, explains why, and knows when the available information is insufficient.

Repository, Python package, environment variable, API, and CLI identifiers remain `Yuxi` / `yuxi` for compatibility. User-facing product copy uses ZhiForge or 知铸.

## Main workflow

```text
Upload and organize files in the material library
-> Parse them into reliable text
-> Confirm natural topic units
-> Select ready units for an expert project
-> Confirm the learning direction
-> Learn every material unit
-> Confirm the knowledge organization
-> Build an expert Skill
-> Preview and enable it manually
```

New expert projects never upload, OCR, or parse the same source again. Legacy knowledge bases, vector retrieval, and knowledge graphs remain explicit compatibility features, not dependencies of the new expert workflow.

## Core expert capabilities

- **Understand methods**: learn analytical paths, decision sequences, and practical experience rather than extracting facts alone.
- **Make judgments**: identify the signals that matter and move from observation to conclusion.
- **Respect conditions**: understand assumptions, exceptions, disagreements, and professional boundaries.
- **Explain evidence**: show why a judgment was made and connect important conclusions to reliable material.

## Beyond ordinary RAG

| Ordinary RAG | ZhiForge expert Agent |
| --- | --- |
| Retrieves passages similar to a question | Understands the methods and knowledge system in the material |
| Organizes an answer around retrieved text | Applies signals, conditions, and experience to form a judgment |
| Handles exceptions only when a passage covers them | Proactively explains scope, exceptions, and reversal conditions |
| Provides source citations | Explains reasoning, evidence, and professional boundaries |
| Produces a searchable knowledge base | Produces an expert Agent that can be enabled and used |

RAG, knowledge bases, and knowledge graphs remain useful retrieval capabilities. ZhiForge places reliable material inside an expert mind that can reason with it.

## Platform capabilities

- Expert creation: material libraries, natural material units, expert projects, preview, and Skill activation.
- Agent runtime: LangGraph, Skills, MCP, tools, sub-agents, middleware, and a sandbox file system.
- Teams and integrations: multi-tenant permissions, departments, sharing, API keys, CLI, and optional Langfuse observability.
- Compatible knowledge stack: Milvus, Dify / Notion connectors, knowledge graphs, and RAG evaluation.

## Tech stack

| Layer | Technologies |
| --- | --- |
| Frontend | Vue 3 · Vite · Pinia |
| Backend | FastAPI · LangGraph · ARQ |
| Core storage | PostgreSQL · Redis · MinIO |
| Compatible knowledge stack | Milvus · Neo4j · Dify · Notion |
| Document processing | MinerU · PaddleX · RapidOCR |
| Deployment | Docker Compose |

## Quick start

Prerequisites: Docker, Docker Compose, and at least one OpenAI-compatible model API.

```bash
git clone <repository-url> <repo-directory>
cd <repo-directory>

# Linux/macOS
./scripts/init.sh

# Windows PowerShell
.\scripts\init.ps1

docker compose up --build -d
```

Open `http://localhost:5173`, create the initial administrator, and configure a model. See the [Quick Start guide](docs/intro/quick-start.md) for complete instructions. The default Compose setup uses `LITE_MODE=true`; legacy knowledge features require the explicit `legacy-knowledge` profile.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE).
