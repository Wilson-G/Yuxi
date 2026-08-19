# 快速开始指南

欢迎使用知铸 ZhiForge。本指南将帮助你启动默认的专家学习与 Skill 编译环境，把一批可靠材料经过项目定向、两次确认、并行学习与 Review、总编成书，生成可手动启用的专家 Skill。


::: tip 提示
除了此文档网站外，你还可以访问 [Zread](https://zread.ai/xerrors/Yuxi) 或 [DeepWiki](https://deepwiki.com/xerrors/Yuxi) 查看自动生成的详细项目文档。
:::

## 环境要求

项目通过 Docker Compose 管理。默认主链无需 GPU；如果需要本地 OCR，可以显式启动对应 profile 或配置外部解析服务。

## 快速安装

### 步骤一：获取项目代码

```bash
# 克隆最新版本
git clone --depth 1 https://github.com/xerrors/Yuxi.git
cd Yuxi
```

`--depth 1` 标志会创建一个浅克隆，仅包含最新的提交，从而显著减少下载时间和磁盘使用量。下表提供了版本选择的指导。

| 版本 | 适用场景 |
|------|----------|
| v0.7.1 | 旧知识库产品路线的历史稳定版本 |
| main | 当前开发版本，包含知铸专家学习主线 |

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
2. 进入左侧导航的「专家 · 技能」，在「专家项目」中导入需要学习的可靠材料。
3. 材料转换为可读正文后，指定最多 20 份、覆盖不同方向的代表性材料，生成项目学习方案、Prompt 包、笔记小样和 Review。
4. 确认学习方向后，系统在后台逐份学习材料；有“需要处理”的材料时，根据具体原因修复、排除或暂停。
5. 查看主题地图和讲义小样，完成第二次确认后，再让总编模型批量定稿讲义和收口全书。
6. 预览候选 Skill 和未纳入材料清单，确认成品可用后手动启用。

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

- 了解如何配置模型：阅读 [模型配置](./model-config.md)
- 了解专家学习全流程：阅读 [知铸 ZhiForge](./project-overview.md)
- 学习智能体开发：阅读 [智能体开发](../agents/agents-config.md)
- 使用旧知识兼容能力：阅读 [知识库与知识图谱](./knowledge-base.md)
- 深入了解配置系统：阅读 [配置系统详解](../advanced/configuration.md)
