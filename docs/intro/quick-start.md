# 快速开始指南

欢迎使用知铸 ZhiForge。本指南帮助你启动系统、创建管理员并配置模型，为制造第一个专家 Agent 做好准备。

知铸先在资料库处理文章、课程和经验材料，再让专家项目选择就绪材料单元。知识库、RAG 和知识图谱继续作为显式兼容能力保留。

## 环境要求

项目通过 Docker Compose 管理。默认主链无需 GPU；如果需要本地 OCR，可以显式启动对应 profile 或配置外部解析服务。

## 快速安装

### 步骤一：获取项目代码

```bash
# 使用维护方提供的仓库地址
git clone <repository-url> ZhiForge
cd ZhiForge
```

### 步骤二：配置环境变量

**方式一：使用初始化脚本（推荐）**

我们提供了自动化脚本，帮你完成环境配置和 Docker 镜像拉取：

```bash
# Linux/macOS
./scripts/init.sh

# Windows PowerShell
.\scripts\init.ps1
```

脚本会引导你完成以下配置：
- 创建 `.env` 配置文件
- 设置 `SILICONFLOW_API_KEY`（必需，用于调用大模型）
- 设置 `TAVILY_API_KEY`（可选，用于搜索服务）
- 自动拉取必需的 Docker 镜像

::: tip API Key 获取
- **硅基流动**：访问 [cloud.siliconflow.cn](https://cloud.siliconflow.cn/i/Eo5yTHGJ)，注册认证即送 16 元额度
- **Tavily**：访问 [app.tavily.com](https://app.tavily.com/) 获取搜索 API Key（可选）
:::

**方式二：手动配置**

如果偏好手动配置：

```bash
# 复制环境变量模板
cp .env.template .env

# 编辑 .env 文件，填入你的 API Key
```

### 步骤三：启动服务

```bash
# 构建并启动知铸默认服务
docker compose up --build -d
```

服务首次启动需要等待镜像拉取和编译，请耐心等待 2-3 分钟。

::: tip 默认专家模式
开发 Compose 默认使用 `LITE_MODE=true`，启动前端、API、ARQ worker、PostgreSQL、Redis、MinIO 和沙盒服务，不启动 Milvus、Neo4j 和 etcd。这是专家学习与 Skill 运行的默认边界。

如需使用旧知识库、向量检索和知识图谱兼容能力，显式启动：

```bash
LITE_MODE=false docker compose --profile legacy-knowledge up --build -d
```
:::

### 步骤四：访问系统

服务启动后，访问以下地址：

| 服务 | 地址 |
|------|------|
| Web 界面 | http://localhost:5173 |
| API 文档 | http://localhost:5050/docs |

首次访问时，系统会要求你设置超级管理员账号和密码，请妥善保存。

## 创建第一个专家项目

1. 在设置中配置并启用至少一个对话模型。
2. 进入「专家 · 技能」中的「资料库」，创建资料库并批量上传文件或目录。
3. 等待文件处理完成，核对完整正文和自然主题材料单元；失败文件可单独重试。
4. 切换到「专家项目」，填写项目信息并选择已经就绪的材料单元。项目不会重复上传、OCR 或解析来源文件。
5. 选择最多 20 份、覆盖不同方向的代表材料，确认专家应该怎样学习这批材料。
6. 等待系统逐份学习；材料进入“需要处理”时，根据具体原因重跑、排除或暂停项目。
7. 确认知识组织方式，等待专家 Skill 形成后预览成品，并手动启用。

专家训练只对管理员开放。普通用户通过已有 Skill 共享权限使用管理员分发的成品，不能读取资料库或编译中的专家项目。

## 故障排除

### 查看服务状态

```bash
# 查看所有容器状态
docker ps

# 实时查看后端日志
docker logs api-dev -f

# 实时查看前端日志
docker logs web-dev -f
```

### 部署故障排查

<details>
<summary><strong>Docker 镜像拉取失败</strong></summary>

如果网络原因导致镜像拉取失败，可以尝试：

```bash
# 手动拉取基础镜像
bash scripts/pull_image.sh python:3.13-slim
```

**离线环境部署方案**：

```bash
# 在有网络的环境导出镜像，注意检查镜像列表，不一定是最新的。
bash docker/save_docker_images.sh

# 传输到目标机器
scp docker_images_xxx.tar user@host:/path/

# 导入镜像
docker load -i docker_images_xxx.tar
```
</details>

<details>
<summary><strong>构建失败</strong></summary>

多数构建失败是由于网络问题。尝试配置代理：

```bash
# Linux/macOS
export HTTP_PROXY=http://IP:PORT
export HTTPS_PROXY=http://IP:PORT

# Windows PowerShell
$env:HTTP_PROXY="http://IP:PORT"
$env:HTTPS_PROXY="http://IP:PORT"
```

如果配置代理后反而失败，尝试移除代理后重试。
</details>

<details>
<summary><strong>Milvus 服务启动失败</strong></summary>

```bash
# 重启 Milvus 服务
LITE_MODE=false docker compose --profile legacy-knowledge up -d etcd milvus
docker restart api-dev
```
</details>

::: tip 调试面板
前端提供了调试面板（在头像菜单中可找到），可以查看详细的请求和响应信息。生产环境建议关闭此特性。
:::

## 下一步

- 制造专家 Agent：阅读 [专家 Agent](./expert-projects.md)
- 了解如何配置模型：阅读 [模型配置](./model-config.md)
- 了解产品能力：阅读 [知铸 ZhiForge](./project-overview.md)
- 学习智能体开发：阅读 [智能体开发](../agents/agents-config.md)
- 使用旧知识兼容能力：阅读 [知识库与知识图谱](./knowledge-base.md)
- 深入了解配置系统：阅读 [配置系统详解](../advanced/configuration.md)
