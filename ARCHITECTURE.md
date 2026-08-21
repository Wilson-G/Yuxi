# ARCHITECTURE.md

本文档是知铸 ZhiForge 的代码地图（仓库、Python 包与环境变量等技术标识暂保留 `Yuxi` / `yuxi`）。它只描述相对稳定的系统边界、目录职责、核心运行链路和架构不变量，用于帮助贡献者判断“一个改动应该落在哪里”，不替代具体模块文档、测试规范或源码注释。

修改不熟悉的模块前，先阅读对应章节，再使用符号搜索定位具体类型、函数和路由。开发与运行拓扑始终以 `docker-compose.yml` 为准。

## 鸟瞰

知铸的默认产品路线是把一批可靠材料学习并编译为可启用、可检索、可核对依据的专家 Skill。管理员先在独立资料库上传和整理文件，得到可靠正文与自然材料单元，再选择就绪单元创建专家项目，经历项目定向、方法与小样确认、并行学习与 Review、人工确认或自动组织、项目内串行总编和成品预览，最后手动启用 Skill。前端通过 `/api` 调用 FastAPI，后端协调 PostgreSQL、Redis/ARQ、MinIO、模型运行时、文档解析和沙盒。

旧知识库、向量检索、评估和知识图谱仍作为兼容能力保留，但不是默认产品路线。新专家项目的材料、笔记、讲义和发布物不进入旧知识库的分块、Milvus 入库或 Neo4j 图谱链路。

资料库以 PostgreSQL 保存目录、文件、单元和处理状态，以私有 MinIO 保存原始文件、规范化 Markdown 与解析图片。新专家项目只保存材料单元关联和编译产物，不复制原始文件或正文；既有直接上传项目继续从现有项目记录和私有正文兼容读取。ARQ worker 根据 PostgreSQL 事实执行并在启动时重投未完成工作；任务中心只投影进度摘要，不参与恢复。

普通智能体请求仍先在 PostgreSQL 中保存为请求和消息，再立即派发或进入线程级 FIFO 队列。派发后的 `AgentRun` 通过 Redis/ARQ 交给独立 worker 执行，运行事件写入 Redis Stream，最终状态和业务记录写回 PostgreSQL，前端通过 SSE 消费排队与运行事件。

核心开发服务包括：

- `web-dev`：Vue 3 / Vite 前端，挂载 `web/src` 并热重载。
- `api-dev`：FastAPI API 服务，挂载 `backend/server`、`backend/package` 和测试目录并热重载。
- `worker-dev`：ARQ worker，执行 AgentRun、资料库文件/单元处理和专家项目阶段任务，并负责各链路的启动恢复扫描。
- `sandbox-provisioner`：为智能体工具执行提供隔离沙盒。
- `postgres`：专家项目事实、Skill 元数据、业务数据、请求队列、AgentRun 与 LangGraph checkpoint；兼容模式下也保存旧知识库元数据。
- `redis`：ARQ 投递、运行事件、取消信号以及跨进程配置和模型缓存。
- `minio`：附件、资料库原始文件、规范化 Markdown、私有解析图片和其他对象数据；兼容模式下也保存旧知识库对象。
- `milvus`、`etcd`：旧向量知识库的可选服务，通过 `legacy-knowledge` profile 启动。
- `graph`：旧知识库图谱的 Neo4j 可选服务，通过 `legacy-knowledge` profile 启动。
- `mineru-api`、`paddlex`：通过 `all` profile 可选启动的文档解析和 OCR 服务。

## 后端代码地图

后端分成两个顶层边界：`backend/server` 是 Web 应用入口与 HTTP 适配层，`backend/package/yuxi` 是业务和基础设施主体。新增领域逻辑通常优先放在 `yuxi` 包中，路由层只处理请求模型、认证上下文和响应装配。

### Web 与 worker 入口

- `server/main.py` 创建 FastAPI 应用、注册中间件，并将业务路由统一挂载到 `/api`。
- `server/routers` 是 HTTP 路由边界，所有路由集中在 `server/routers/__init__.py` 注册。
- `server/utils/lifespan.py` 管理数据库、内置模型/MCP/Skills、Redis、沙盒、LangGraph checkpoint 和通用 Tasker 的启动与关闭；只有非 `LITE_MODE` 才初始化旧知识库。
- `server/worker_main.py` 是 ARQ worker 入口，实际执行设置位于 `yuxi.services.run_worker`。

`LITE_MODE` 是默认运行边界：保留资料库、专家项目、认证、智能体、聊天、Skills、MCP、模型、工作区和系统管理接口，但不注册 `external_kb`、`knowledge`、`evaluation` 和 `graph` 路由，也不初始化旧知识库管理器。

### `backend/package/yuxi`

- `material_library` 管理独立资料库的文件、目录、解析、规范化正文、自然材料单元、逐文件 ARQ 工作和恢复；只复用 `knowledge.parser` 的通用文档解析能力，不依赖旧知识库文件、分块、向量或图谱事实。
- `expert` 管理资料库材料单元接入、项目定向和 Prompt 包、逐份学习与 Review、主题与讲义组织、总编收口、候选 Skill 组装、ARQ 任务与项目恢复。`expert/compiler_profiles.py` 是静态内置编译方法注册表；现有 `expert/prompt_designer/` 和 `expert/prompt_designer_profiles/reasoning_assets/` 分别提供教材笔记法与领域推导资产法的中央 SKILL、材料类型模板和结构示例。它们不是隐藏的辅助资源。
- `agents` 定义 LangGraph 智能体体系。`BaseAgent` 是智能体基类，`BaseContext` 是运行上下文；`buildin/chatbot` 和 `buildin/subagent` 放内置智能体；`middlewares` 组合文件系统、Skills、SubAgent、摘要、审批、模型兼容和用量统计；`toolkits` 管理本地工具；`backends` 对接沙盒和 Skills 文件系统；`skills` 与 `mcp` 管理扩展能力及其运行时加载。
- `services` 是用例层。智能体主链路重点分为请求接入与排队、Run 生命周期、运行时配置、worker 执行和 SubAgent 调用；聊天历史、附件、工作区、文件预览、评估、认证和观测等跨模块流程也从这里找入口。
- `repositories` 是 PostgreSQL 访问边界，封装专家项目、业务对象、AgentRun、请求队列、Task 和扩展配置查询；兼容知识库元数据也保留在这一边界。路由不应绕过 repository 直接拼装持久化逻辑。
- `storage/postgres` 管理 SQLAlchemy 模型、业务连接池和 LangGraph checkpoint 连接池。
- `storage/redis` 管理同步/异步 Redis 客户端和 ARQ 连接参数；业务 key、事件格式和缓存语义留在各自服务中。
- `storage/minio` 管理对象上传、下载和临时文件访问。
- `storage/neo4j` 管理共享 Neo4j Driver、生命周期和图查询辅助。
- `knowledge` 保留两类能力：`parser` 中的 OCR/文档解析作为专家材料转正文的共享底层；`runtime`、`implementations`、`chunking`、`eval` 和 `graphs` 是旧知识库、向量检索、评估与图谱兼容领域。
- `models` 封装 chat、embedding 和 rerank 模型适配；`models/providers` 使用 PostgreSQL 保存模型供应商，并通过 Redis 缓存向 API 和 worker 提供一致视图。
- `config` 区分系统级配置和用户级配置。系统配置写入 `base.toml` 并同步 Redis 快照，用户配置保存在 PostgreSQL。
- `utils` 只放跨领域且足够通用的日志、时间、SSE 和轻量工具。

### 后台执行边界

项目中存在三种用途不同的后台边界，不应混用：

- 资料库：PostgreSQL 中的 `material_libraries`、`material_directories`、`material_source_files` 和 `material_units` 保存事实，Redis/ARQ 按来源文件稳定标识投递解析和单元确认工作。Worker 启动时只重投未完成文件或单元。
- 专家项目：PostgreSQL 中的 `expert_projects`、`expert_materials`、`expert_research_batches` 和 `expert_topics` 保存事实，Redis/ARQ 投递阶段任务到 `worker-dev`。逐份学习按“项目 + 材料 + 方法版本”使用稳定工作标识；Worker 启动时从项目和材料记录重投未完成工作，不从 Tasker 恢复。
- AgentRun：通过 PostgreSQL 保存事实状态，使用 Redis/ARQ 投递到 `worker-dev`，支持运行事件、取消、恢复和线程请求队列。
- `services/task_service.py` 中的 Tasker：运行在 API 进程内，用于旧知识库解析、评估和图谱构建等兼容后台任务；任务摘要持久化到 PostgreSQL，但可执行 coroutine 和内存队列不具备跨进程重建能力。专家项目只向任务中心提供读取投影，不把任务中心当作事实来源。

测试代码位于 `backend/test`，按 `unit`、`integration`、`e2e` 分层。新增或修改后端行为时，测试应放在最能覆盖真实风险的层级。

## 前端代码地图

前端是 Vue 3 + Vite 应用，业务入口集中在 `web/src`。

- `main.js` 挂载应用，`App.vue` 是根组件。
- `router` 定义公开首页、登录、智能体、工作区、专家项目、Skill 与其他扩展和仪表盘路由，并负责认证、管理员和超级管理员守卫。
- `apis` 是后端接口封装边界。新增接口应在这里定义，复用 `base.js` 的请求、鉴权和错误处理。
- `stores` 保存用户、智能体配置、主题和其他跨页面状态。
- `views` 是页面级入口，`components` 是可复用界面块。智能体对话的主要交互位于 `AgentChatComponent`，由 `AgentView` 负责页面组合。
- `composables` 封装请求排队、Run SSE、流式消息、审批、线程状态、提及和其他可组合逻辑。
- `utils` 放轻量转换和展示辅助；全局样式集中在 `assets/css`，颜色和基础规范优先复用 `base.css`。

`/` 是公开首页；登录后的对话工作区是 `/agent`。`/extensions` 对所有登录用户开放：管理员可见资料库、专家项目、Skills 和其他管理入口，专家项目详情展示方法与小样确认、人工/自动推进选择、阶段明细、人工模式的组织确认、候选 Skill 预览和手动启用；普通用户只看和使用已有共享权限允许的 Skills。Dashboard 仅超级管理员可访问。后端权限检查始终是最终边界，前端守卫只负责页面体验。

## 专家学习与编译链路

一个新专家项目经过以下边界：

1. 管理员在资料库逐文件或按文件夹上传材料；每个文件保留相对路径，经独立 ARQ 工作解析为 MinIO 中的完整规范化 Markdown，再由模型确认带来源行范围的自然材料单元。
2. 前端向 `POST /api/expert-projects` 提交项目基础信息、创建时选择的 `compiler_profile_id` 和就绪 `material_unit_ids`；`compiler_profile_id` 在项目创建后不可修改，旧 API 缺省为现有教材笔记法。`yuxi.expert.service` 只创建项目、材料单元关联和编译产物目录，不创建 `raw/`、`text/` 材料副本，也不再次 OCR 或解析。同一批材料可以创建多个独立项目进行方法比较。
3. 可用材料少于 4 份时，用户必须全选；否则用户从项目材料中明确选定 4～20 份、覆盖不同方向的代表材料。总编模型根据项目固定的编译方法资产形成项目定向、学习方案、编译底稿、阶段 Prompt 包，并从代表材料中选取 4～5 份（少于 4 份时全部）并行生成笔记小样和 Review。中央资产只参与定向和方法生成；校准及后续阶段复用项目冻结的 `prompt_pack`。项目停在第一次确认；新页面默认开启“自动推进”，确认请求把选择持久化为项目事实。历史项目和旧 API 默认关闭。
4. 用户确认项目方法版本后，ARQ worker 完成有限方法校准，再按自然材料单元分别执行笔记、Review 和有限定点修订。人工模式保留方法 V2 确认和材料问题处理；自动模式由模型最终判定材料采用或 `no_value`，只有运行条件失效或零份材料通过 Review 时停止。
5. 材料范围明确后，并行助手准备候选主题、证据和冲突清单，总编模型串行形成主题地图、编辑方案和讲义小样。人工模式停在第二个确认点；自动模式在小样通过且仍有可信主题时自动固定组织版本。全部通过笔记被总编明确列为未分配时停止，不按材料数量设阈值。
6. 人工模式由用户确认组织版本后、自动模式由系统按首次授权固定组织版本后，助手并行准备证据包，同一项目的总编模型串行编写和有限修订正式讲义，再执行全书一致性检查与收口。自动修订不会变成无限循环。
7. `yuxi.expert.publisher` 将正式讲义、导航、精选证据、来源映射和 `SKILL.md` 组装为候选 Skill，候选目录只允许 `SKILL.md`、`nav/`、`references/`、`wiki/`、`search/` 和 `sources.jsonl`。
8. 前端先预览候选成品；只有用户手动启用后，候选 Skill 才通过现有 Skill 安装边界进入 Agent 可用范围。

第一版总编直接使用系统已配置模型的受控无状态调用。Prime 工作台和 Prime 高级问答属于后续可选增强，不是当前运行依赖。

## 智能体运行链路

一次普通智能体请求经过以下边界：

1. `AgentView` 和 `AgentChatComponent` 收集文本、图片、附件、模型与审批配置。
2. `web/src/apis/agent_api.js` 调用 `POST /api/agent/runs`。
3. `server/routers/agent_router.py` 校验用户和智能体，将请求交给 `agent_request_queue_service`。
4. 服务在同一数据库事务中创建用户消息和 AgentRunRequest，并按用户、智能体和线程检查活跃 Run 与 FIFO 队头。
5. 请求可以立即派发、进入等待队列或按 `reject` 策略拒绝；只有数据库提交成功后才向 ARQ 投递 Run。
6. `worker-dev` 中的 `run_worker` 加载 AgentRun、智能体配置和运行上下文，执行对应 LangGraph。
7. 智能体通过 middleware 组合沙盒文件系统、附件、Skills、MCP、SubAgent、审批、摘要和工具能力。已启用专家 Skill 在线程只读投影中向 Agent 提供讲义、导航和精选证据；兼容知识库能力由内置 `knowledge-base` Skill 及其依赖工具按需开放。
8. Run 事件写入 Redis Stream，取消通过 Redis key/pubsub 传递；AgentRun、消息投递状态和最终结果写入 PostgreSQL。
9. 前端在排队阶段消费 Request SSE，派发后切换到 Run SSE，并根据数据库状态处理断线恢复和终态补偿。
10. 附件和对象数据保存在 MinIO；智能体需要操作的文件映射到线程隔离的沙盒路径，生成物写入用户可见的输出目录。

审批或人机输入产生的 resume 请求会从 LangGraph checkpoint 恢复，并创建新的 AgentRun；它不重新进入普通消息 FIFO 接入流程。

## 架构不变量

- Docker Compose 是开发环境的事实来源。开发时先检查容器、日志和热重载，不默认要求本地裸跑服务。
- HTTP 路由保持薄；专家学习用例放在 `yuxi.expert`，通用智能体与跨领域用例放在 `yuxi.services`，持久化查询放在 `yuxi.repositories`。
- 专家项目记录是学习与编译进度的唯一事实来源；ARQ 负责执行，任务中心只负责展示，API 进程内 Tasker 不恢复专家项目。
- `compiler_profile_id` 是创建时固定的项目事实，静态注册表只允许 `apprenticeship` 和 `reasoning_assets` 两个内置方法；未知方法或必需中央资产缺失直接失败，不静默回退。Worker 恢复和重试从 PostgreSQL 读取该事实，不把 profile 作为 ARQ 参数。
- 资料库是新增专家材料的唯一原文件和规范化正文事实来源；新专家项目只关联就绪材料单元。旧项目只保留现有记录的兼容读取，不恢复新增、替换或再次上传材料的入口。
- 项目定向只读用户确认的代表性材料；逐份学习、主题规划和全书收口都不得静默截断或暗中抽样。
- 第 0 阶段确认“怎么学”始终必需。人工模式在第 2 阶段继续确认“怎么组织”；自动模式只有在用户第一次确认时明确提交 `auto_advance=true` 才能跳过方法 V2 和组织确认。自动模式的内容采用、修订和排除由模型决定，人工模式保留用户修复、明确排除和暂停入口。
- `auto_advance` 是 PostgreSQL 中的项目事实，不是 Redis 或 ARQ 参数。数据库与旧 API 默认关闭，新页面开关默认开启；校准开始后冻结。运行条件失效、零份材料通过 Review 或全部通过笔记被总编明确列为未分配时停止，不新增机械材料阈值或另一套状态机。
- 自动推进最多生成 `release/ready` 候选 Skill，绝不自动启用。预览和手动启用是人工与自动模式共同的发布边界。
- 普通助手可以并行阅读、Review 和准备证据；会改变编辑方案、正式讲义和最终 Skill 的总编写入在同一项目内保持串行。
- 最终专家 Skill 只包含运行所需的讲义、导航、精选证据、来源映射和 `SKILL.md`；运行时不绕过 Skill 访问项目内部原始材料。
- 请求接入与 Run 执行是两个阶段：先提交 PostgreSQL 事实，再投递 ARQ，不能让队列消息先于数据库状态可见。
- 同一用户、智能体和线程的普通请求通过 FIFO 队列串行派发；排队请求与运行中的 Run 使用不同状态模型和 SSE。
- PostgreSQL 保存业务事实状态；Redis 承担投递、事件、取消和缓存，不作为 AgentRun 最终状态的唯一来源。
- 前端 API 调用集中在 `web/src/apis`，组件不要散落拼接普通 HTTP 接口。
- 智能体能力通过 context、middleware、toolkits、Skills、MCP 和 backends 组合；不要把知识库、沙盒或扩展逻辑硬编码进单个页面或路由。
- Skill 依赖工具只有在对应 Skill 激活后才对模型开放；基础工具与受 Skill 门控的工具要保持边界。
- LITE 模式是专家主链的默认运行边界，必须允许跳过旧知识库、图谱和评估等重依赖；兼容能力通过显式 profile 和 `LITE_MODE=false` 开启。
- 沙盒虚拟路径以 `SANDBOX_VIRTUAL_PATH_PREFIX` 为边界，用户可见路径、对象存储 URL 与宿主机真实路径不能混用。
- 面向用户和外部系统的输入在边界校验；内部服务优先依赖已有类型、事务和仓储约束，避免用静默回退掩盖设计错误。

## 跨切面关注点

- **配置**：Compose 和 `.env` 提供部署配置；管理员系统配置写入 `base.toml` 并通过 Redis 快照同步；用户配置与模型供应商以 PostgreSQL 为事实来源。
- **权限**：前端路由和页面标签提供体验级约束，FastAPI 认证依赖和 repository 可见性查询提供最终授权。
- **状态与存储**：PostgreSQL 保存资料库、专家项目、Skill 元数据、请求、Run、消息和业务事实；LangGraph checkpoint 使用 PostgreSQL，必要时可回退 SQLite/内存；Redis 保存短期事件、取消信号、ARQ 和跨进程缓存；MinIO、项目编译目录、沙盒与共享 Skill 目录分别承载不同生命周期的文件。
- **文档处理**：资料库原始文件进入私有 MinIO，经现有文档解析入口生成完整规范化 Markdown 和私有图片，再形成自然材料单元；新专家项目按关联读取单元完整正文，不写旧知识库 chunk。旧知识库的上传、分块和入库链路只在兼容模式中使用。
- **观测与调试**：优先查看 `api-dev`、`worker-dev` 和相关依赖日志；Langfuse 集中在服务层和 AgentRun 上下文；SSE 问题同时检查 Redis 事件与 PostgreSQL 终态。
