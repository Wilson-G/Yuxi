<script setup>
import { ref, computed } from 'vue'
import { withBase } from 'vitepress'

// 专家学习主流程
const stats = [
  { value: '2 次', label: '关键用户确认' },
  { value: '并行', label: '材料学习与 Review' },
  { value: '串行', label: '项目内总编' },
  { value: 'Skill', label: '独立专家交付' }
]

// 专家学习与运行底座
const capabilities = [
  {
    icon: 'box', span: true,
    title: '可靠材料入口',
    desc: 'PDF、Office、Markdown 和图片先转换为模型可完整阅读的正文，超长材料必须形成有意义的自然单元，不静默截断。',
    tags: ['文档解析', '自然材料单元', '完整输入']
  },
  {
    icon: 'sparkles',
    title: '项目专属 Prompt',
    desc: '总编模型根据材料目录和代表性样本，生成只属于当前项目的学习方案、编译底稿和阶段 Prompt 包。',
    tags: ['项目定向', '小样', '第一次确认']
  },
  {
    icon: 'plug',
    title: '并行学习与 Review',
    desc: '不同材料独立学习、复核和定点修订，单份失败不重跑已完成材料，没有迁移价值也是正常结果。',
    tags: ['独立任务', '有限修订']
  },
  {
    icon: 'wrench',
    title: '项目内串行总编',
    desc: '普通助手并行准备候选主题和证据，同一项目的总编模型统一决定主题结构、讲义正式稿和回答口径。',
    tags: ['单一写入者', '第二次确认']
  },
  {
    icon: 'fork',
    title: '独立专家 Skill',
    desc: '最终交付仅包含正式讲义、导航、精选证据和 SKILL.md，不携带原始材料、Prompt、Review 和进度记录。',
    tags: ['讲义', '导航', '精选证据']
  },
  {
    icon: 'layers',
    title: '项目事实与恢复',
    desc: '项目记录保存阶段、材料结果、正式产物和下一步；ARQ 负责执行，任务中心只负责展示。',
    tags: ['PostgreSQL', 'ARQ', '断点继续']
  },
  {
    icon: 'cpu',
    title: '专家回答',
    desc: 'Agent 从已启用 Skill 中查找判断方法、正式讲义和带来源的精选证据，在资料不足时明确边界。',
    tags: ['方法', '条件', '边界']
  }
]

// 共享底层能力与旧知识兼容模式
const engineTabs = [
  {
    key: 'parse', icon: 'scan', title: '材料转可靠正文',
    desc: 'MinerU、PaddleX、RapidOCR 等解析能力为专家学习提供完整、可读的 Markdown 正文。'
  },
  {
    key: 'skill', icon: 'sparkles', title: 'Skill 内独立检索',
    desc: '已启用专家 Skill 在自己的讲义、导航和精选证据中检索，运行时不访问编译项目原始材料。'
  },
  {
    key: 'retrieval', icon: 'database', title: '兼容向量知识库',
    desc: '需要历史 RAG 流程时可显式启用 Milvus 与只读知识源；新专家项目不进入该分块和入库链路。'
  },
  {
    key: 'graph', icon: 'share', title: '兼容知识图谱',
    desc: 'Neo4j 实体关系与子图检索作为旧知识库的可选增强能力保留，默认 Compose 不启动。'
  },
  {
    key: 'eval', icon: 'chart', title: '兼容 RAG 评估',
    desc: '旧知识库的检索指标和评估运行继续可用，不作为专家学习主流程的发布门禁。'
  }
]
const activeEngine = ref(0)
const currentEngine = computed(() => engineTabs[activeEngine.value])

// 模型供应商墙（横向跑马灯，两行错位反向滚动）
const ICON_BASE = 'https://registry.npmmirror.com/@lobehub/icons-static-svg/latest/files/icons'
const providers = [
  { name: 'OpenAI', icon: `${ICON_BASE}/openai.svg` },
  { name: 'DeepSeek', icon: `${ICON_BASE}/deepseek-color.svg` },
  { name: '通义千问', icon: `${ICON_BASE}/bailian-color.svg` },
  { name: '智谱 AI', icon: `${ICON_BASE}/zhipu-color.svg` },
  { name: 'Moonshot', icon: `${ICON_BASE}/moonshot.svg` },
  { name: 'MiniMax', icon: `${ICON_BASE}/minimax-color.svg` },
  { name: 'SiliconFlow', icon: `${ICON_BASE}/siliconcloud-color.svg` },
  { name: 'OpenRouter', icon: `${ICON_BASE}/openrouter.svg` },
  { name: 'ModelScope', icon: `${ICON_BASE}/modelscope-color.svg` },
  { name: 'OpenCode', icon: `${ICON_BASE}/opencode.svg` },
  { name: '小米 MiMo', icon: `${ICON_BASE}/xiaomimimo.svg` }
]
// 第二行换个起始顺序以形成错位；每行内容复制一份用于无缝循环
const providersTop = [...providers, ...providers]
const providersBottom = (() => {
  const rotated = [...providers.slice(5), ...providers.slice(0, 5)]
  return [...rotated, ...rotated]
})()

// 工作原理
const steps = [
  { n: '01', title: '导入可靠材料', desc: '原始文件先转成完整可读正文，再由用户选定代表性材料。' },
  { n: '02', title: '确认怎么学', desc: '总编形成项目学习方案、Prompt 包和笔记小样，用户确认后才批量学习。' },
  { n: '03', title: '学习并确认组织', desc: '助手并行学材料与 Review，总编形成主题地图和讲义小样，用户完成第二次确认。' },
  { n: '04', title: '总编成书并启用', desc: '总编逐篇定稿、全书收口并组装专家 Skill，用户预览成品后手动启用。' }
]

// 产品截图一览
const shots = [
  { title: '专家项目', desc: '导入材料并查看真实阶段与进度' },
  { title: '学习方案', desc: '查看项目定向、Prompt 和笔记小样' },
  { title: '主题与讲义', desc: '确认组织方式并跟踪总编定稿' },
  { title: '专家 Skill', desc: '预览正式讲义、导航与精选证据' }
]

// 企业级
const enterprise = [
  { icon: 'shield', title: '项目与 Skill 权限', desc: '专家项目保持用户边界，发布后的 Skill 按现有权限模型控制读取和管理范围。' },
  { icon: 'key', title: 'API Key 集成', desc: '签发独立密钥，供外部系统以 API 方式安全调用平台能力。' },
  { icon: 'rocket', title: '专家主链默认启动', desc: 'Docker Compose 默认跳过 Milvus、etcd 和 Neo4j，旧知识库能力通过兼容 profile 显式开启。' }
]

// 应用场景
const cases = [
  { title: '老师与顾问经验传承', desc: '从课程、访谈和案例中学习判断顺序、条件意识和真实停止线。' },
  { title: '专业操作与决策手册', desc: '把分散的流程、案例和复盘编成能够处理具体条件和例外的专家 Skill。' },
  { title: '团队专家能力复用', desc: '通过普通 Skill 的权限、安装和 Agent 运行边界，将经过确认的专家方法稳定复用。' }
]

// 技术栈分层
const techStack = [
  { group: '前端', items: ['Vue 3', 'Vite', 'Pinia'] },
  { group: '后端', items: ['FastAPI', 'LangGraph', 'ARQ'] },
  { group: '存储', items: ['PostgreSQL', 'Redis', 'MinIO', 'Milvus（兼容）', 'Neo4j（兼容）'] },
  { group: '解析', items: ['MinerU', 'PaddleX', 'RapidOCR'] },
  { group: '部署', items: ['Docker Compose'] }
]

// 开源致谢
const credits = [
  { name: 'LightRAG', url: 'https://github.com/HKUDS/LightRAG' },
  { name: 'DeepAgents', url: 'https://github.com/langchain-ai/deepagents' },
  { name: 'DeerFlow', url: 'https://github.com/bytedance/deer-flow' },
  { name: 'RAGflow', url: 'https://github.com/infiniflow/ragflow' },
  { name: 'LangGraph', url: 'https://github.com/langchain-ai/langgraph' },
  { name: 'QwenPaw', url: 'https://github.com/agentscope-ai/QwenPaw' }
]

// lucide 风格图标路径（stroke 1.5）
const icons = {
  box: '<path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="m3.3 7 8.7 5 8.7-5"/><path d="M12 22V12"/>',
  sparkles: '<path d="M9.94 15.5A2 2 0 0 0 8.5 14.06l-6.14-1.58a.5.5 0 0 1 0-.96L8.5 9.94A2 2 0 0 0 9.94 8.5l1.58-6.14a.5.5 0 0 1 .96 0L14.06 8.5A2 2 0 0 0 15.5 9.94l6.14 1.58a.5.5 0 0 1 0 .96L15.5 14.06a2 2 0 0 0-1.44 1.44l-1.58 6.14a.5.5 0 0 1-.96 0z"/>',
  plug: '<path d="M12 22v-5"/><path d="M9 7V2"/><path d="M15 7V2"/><path d="M6 13V8h12v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4Z"/>',
  wrench: '<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"/>',
  fork: '<circle cx="12" cy="18" r="3"/><circle cx="6" cy="6" r="3"/><circle cx="18" cy="6" r="3"/><path d="M18 9v2c0 .6-.4 1-1 1H7c-.6 0-1-.4-1-1V9"/><path d="M12 12v3"/>',
  layers: '<path d="M12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z"/><path d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65"/><path d="m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65"/>',
  cpu: '<rect width="16" height="16" x="4" y="4" rx="2"/><rect width="6" height="6" x="9" y="9" rx="1"/><path d="M15 2v2"/><path d="M15 20v2"/><path d="M2 15h2"/><path d="M2 9h2"/><path d="M20 15h2"/><path d="M20 9h2"/><path d="M9 2v2"/><path d="M9 20v2"/>',
  database: '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5"/><path d="M3 12a9 3 0 0 0 18 0"/>',
  share: '<circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><line x1="8.59" x2="15.42" y1="13.51" y2="17.49"/><line x1="15.41" x2="8.59" y1="6.51" y2="10.49"/>',
  scan: '<path d="M3 7V5a2 2 0 0 1 2-2h2"/><path d="M17 3h2a2 2 0 0 1 2 2v2"/><path d="M21 17v2a2 2 0 0 1-2 2h-2"/><path d="M7 21H5a2 2 0 0 1-2-2v-2"/><path d="M7 12h10"/>',
  shield: '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/>',
  key: '<path d="m15.5 7.5 2.3 2.3a1 1 0 0 0 1.4 0l2.1-2.1a1 1 0 0 0 0-1.4L19 4"/><path d="m21 2-9.6 9.6"/><circle cx="7.5" cy="15.5" r="5.5"/>',
  rocket: '<path d="M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z"/><path d="m12 15-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35 0 0 1-4 2z"/><path d="M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0"/><path d="M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5"/>',
  chart: '<path d="M3 3v18h18"/><path d="M7 16v-5"/><path d="M12 16V8"/><path d="M17 16v-3"/>'
}

// 滚动进场指令
const vReveal = {
  mounted(el) {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    el.classList.add('reveal')
    const io = new IntersectionObserver((entries) => {
      for (const e of entries) {
        if (e.isIntersecting) {
          el.classList.add('in-view')
          io.unobserve(el)
        }
      }
    }, { threshold: 0.1 })
    io.observe(el)
  }
}
</script>

<template>
  <div class="yx-home">
    <!-- ===== Hero ===== -->
    <section class="yx-hero">
      <div class="yx-ambient" aria-hidden="true">
        <span class="yx-orb yx-orb--1"></span>
        <span class="yx-orb yx-orb--2"></span>
        <span class="yx-orb yx-orb--3"></span>
        <div class="yx-grid"></div>
      </div>
      <div class="yx-container yx-hero__inner">
        <span class="yx-badge">MIT 开源 · LangGraph · 专家 Skill</span>
        <h1 class="yx-hero__title">知铸 <span class="yx-accent">ZhiForge</span></h1>
        <p class="yx-hero__subtitle">把可靠材料学习并编译为专家 Skill</p>
        <p class="yx-hero__desc">
          普通助手并行扩大材料覆盖面，项目内串行的总编模型负责学习方案、主题结构、
          讲义作者性和最终收口；用户在两个关键确认点决定方向，
          最终得到有判断方法、条件意识和可核对依据的专家 Skill。
        </p>
        <div class="yx-hero__actions">
          <a class="yx-btn yx-btn--primary" :href="withBase('/intro/quick-start')">快速开始</a>
        </div>
      </div>
    </section>

    <!-- ===== 数据条 ===== -->
    <section class="yx-stats">
      <div class="yx-container yx-stats__inner">
        <div v-for="s in stats" :key="s.label" class="yx-stat">
          <div class="yx-stat__value">{{ s.value }}</div>
          <div class="yx-stat__label">{{ s.label }}</div>
        </div>
      </div>
    </section>

    <!-- ===== 专家学习与运行底座 ===== -->
    <section class="yx-section">
      <div class="yx-container">
        <header v-reveal class="yx-head">
          <span class="yx-head__eyebrow">专家学习主链</span>
          <h2 class="yx-head__title">从一批材料，到一位有判断边界的专家</h2>
          <p class="yx-head__sub">知铸把项目定向、并行学习、独立 Review、总编成书和 Skill 运行放在同一条可恢复流程中。</p>
        </header>
        <div class="yx-bento">
          <article
            v-for="cap in capabilities"
            :key="cap.title"
            v-reveal
            class="yx-cap"
            :class="{ 'yx-cap--lg': cap.span }"
          >
            <span class="yx-cap__icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" v-html="icons[cap.icon]" />
            </span>
            <h3 class="yx-cap__title">{{ cap.title }}</h3>
            <p class="yx-cap__desc">{{ cap.desc }}</p>
            <div class="yx-cap__tags">
              <span v-for="t in cap.tags" :key="t" class="yx-tag">{{ t }}</span>
            </div>
            <img v-if="cap.shot" class="yx-cap__shot-img" :src="cap.shot" :alt="cap.title" loading="lazy" />
          </article>
        </div>
      </div>
    </section>

    <!-- ===== 共享底层与兼容能力 ===== -->
    <section class="yx-section yx-section--soft">
      <div class="yx-container yx-split">
        <div v-reveal class="yx-split__text">
          <span class="yx-head__eyebrow">共享底层与兼容能力</span>
          <h2 class="yx-head__title">文档解析服务专家主链，旧知识能力按需开启</h2>
          <ul class="yx-tabs">
            <li
              v-for="(t, i) in engineTabs"
              :key="t.key"
              class="yx-tab"
              :class="{ 'yx-tab--active': activeEngine === i }"
              @click="activeEngine = i"
            >
              <span class="yx-list__ic" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" v-html="icons[t.icon]" />
              </span>
              <div class="yx-tab__body">
                <strong>{{ t.title }}</strong>
                <div class="yx-tab__desc"><p>{{ t.desc }}</p></div>
              </div>
            </li>
          </ul>
        </div>
        <div v-reveal class="yx-split__media">
          <div class="yx-engine-media">
            <Transition name="yx-fade" mode="out-in">
              <img
                v-if="currentEngine.shot"
                :key="currentEngine.key"
                class="yx-engine-frame"
                :src="currentEngine.shot"
                :alt="currentEngine.title"
                loading="lazy"
              />
              <div
                v-else
                :key="currentEngine.key"
                class="yx-engine-frame yx-engine-ph"
                role="img"
                :aria-label="currentEngine.title + ' 预览'"
              >
                <span>{{ currentEngine.title }} · 预览</span>
              </div>
            </Transition>
          </div>
        </div>
      </div>
    </section>

    <!-- ===== 模型供应商墙 ===== -->
    <section class="yx-section">
      <div class="yx-container">
        <header v-reveal class="yx-head">
          <span class="yx-head__eyebrow">模型供应商</span>
          <h2 class="yx-head__title">一处接入，随处切换</h2>
          <p class="yx-head__sub">统一 <code>provider_id:model_id</code> 配置，覆盖主流模型供应商，并支持自定义 provider。</p>
        </header>
        <div v-reveal class="yx-marquee">
          <div class="yx-marquee__row">
            <div class="yx-marquee__track">
              <div v-for="(p, i) in providersTop" :key="'t' + i" class="yx-mq-item">
                <img :src="p.icon" :alt="p.name" loading="lazy" />
                <span>{{ p.name }}</span>
              </div>
            </div>
          </div>
          <div class="yx-marquee__row yx-marquee__row--rev">
            <div class="yx-marquee__track">
              <div v-for="(p, i) in providersBottom" :key="'b' + i" class="yx-mq-item">
                <img :src="p.icon" :alt="p.name" loading="lazy" />
                <span>{{ p.name }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- ===== 工作原理 ===== -->
    <section class="yx-section yx-section--soft">
      <div class="yx-container">
        <header v-reveal class="yx-head">
          <span class="yx-head__eyebrow">工作原理</span>
          <h2 class="yx-head__title">四步完成专家学习与编译</h2>
        </header>
        <div class="yx-steps">
          <div v-for="(st, i) in steps" :key="st.n" v-reveal class="yx-step">
            <div class="yx-step__n">{{ st.n }}</div>
            <h3 class="yx-step__title">{{ st.title }}</h3>
            <p class="yx-step__desc">{{ st.desc }}</p>
            <span v-if="i < steps.length - 1" class="yx-step__arrow" aria-hidden="true">→</span>
          </div>
        </div>
      </div>
    </section>

    <!-- ===== 产品一览 ===== -->
    <section class="yx-section">
      <div class="yx-container">
        <header v-reveal class="yx-head">
          <span class="yx-head__eyebrow">产品一览</span>
          <h2 class="yx-head__title">一个专家项目，串起全部正式产物</h2>
        </header>
        <div class="yx-shots">
          <figure v-for="sh in shots" :key="sh.title" v-reveal class="yx-shot">
            <div class="yx-placeholder" role="img" :aria-label="sh.title + ' 截图占位'">
              <span>{{ sh.title }} · 16:9</span>
            </div>
            <figcaption>
              <strong>{{ sh.title }}</strong>
              <span>{{ sh.desc }}</span>
            </figcaption>
          </figure>
        </div>
      </div>
    </section>

    <!-- ===== 企业级 ===== -->
    <section class="yx-section yx-section--soft">
      <div class="yx-container">
        <header v-reveal class="yx-head">
          <span class="yx-head__eyebrow">企业级与可集成</span>
          <h2 class="yx-head__title">从原型验证到团队落地</h2>
        </header>
        <div class="yx-cards3">
          <article v-for="e in enterprise" :key="e.title" v-reveal class="yx-card">
            <span class="yx-cap__icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" v-html="icons[e.icon]" />
            </span>
            <h3>{{ e.title }}</h3>
            <p>{{ e.desc }}</p>
          </article>
        </div>
      </div>
    </section>

    <!-- ===== 应用场景 ===== -->
    <section class="yx-section">
      <div class="yx-container">
        <header v-reveal class="yx-head">
          <span class="yx-head__eyebrow">应用场景</span>
          <h2 class="yx-head__title">适配你的真实业务</h2>
        </header>
        <div class="yx-cards3">
          <article v-for="c in cases" :key="c.title" v-reveal class="yx-card yx-card--case">
            <h3>{{ c.title }}</h3>
            <p>{{ c.desc }}</p>
          </article>
        </div>
      </div>
    </section>

    <!-- ===== 技术栈 ===== -->
    <section class="yx-section yx-section--soft">
      <div class="yx-container">
        <header v-reveal class="yx-head">
          <span class="yx-head__eyebrow">技术栈</span>
          <h2 class="yx-head__title">现代而稳健的工程基座</h2>
        </header>
        <div class="yx-tech">
          <div v-for="t in techStack" :key="t.group" v-reveal class="yx-tech__row">
            <div class="yx-tech__group">{{ t.group }}</div>
            <div class="yx-tech__items">
              <span v-for="it in t.items" :key="it" class="yx-chip">{{ it }}</span>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- ===== 快速开始 ===== -->
    <section class="yx-section">
      <div class="yx-container">
        <header v-reveal class="yx-head">
          <span class="yx-head__eyebrow">快速开始</span>
          <h2 class="yx-head__title">三步本地跑起来</h2>
        </header>
        <div v-reveal class="yx-quick">
          <pre class="yx-code"><code><span class="yx-c-cmt"># 1. 克隆并初始化</span>
git clone --depth 1 &lt;repository-url&gt; &lt;repo-directory&gt;
cd &lt;repo-directory&gt; && ./scripts/init.sh

<span class="yx-c-cmt"># 2. 启动默认专家学习环境</span>
docker compose up --build

<span class="yx-c-cmt"># 3. 浏览器访问</span>
open http://localhost:5173</code></pre>
          <p class="yx-quick__tip">默认不启动 Milvus、etcd 和 Neo4j；旧知识兼容能力使用 <code>legacy-knowledge</code> profile 显式开启。</p>
        </div>
      </div>
    </section>

    <!-- ===== 开源致谢 ===== -->
    <section class="yx-section yx-section--soft">
      <div class="yx-container yx-center">
        <header v-reveal class="yx-head">
          <span class="yx-head__eyebrow">开源致谢</span>
          <h2 class="yx-head__title">感谢这些开源项目</h2>
        </header>
        <p v-reveal class="yx-credits">
          站在巨人的肩上 ——
          <template v-for="(c, i) in credits" :key="c.name">
            <a :href="c.url" target="_blank" rel="noreferrer">{{ c.name }}</a><span v-if="i < credits.length - 1"> · </span>
          </template>
        </p>
      </div>
    </section>

    <!-- ===== 最终 CTA ===== -->
    <section class="yx-cta">
      <div class="yx-container yx-cta__inner" v-reveal>
        <h2>立即开始你的第一个专家项目</h2>
        <p>开源、可自托管，从可靠材料中学习真正可复用的判断方法。</p>
        <div class="yx-hero__actions yx-cta__actions">
          <a class="yx-btn yx-btn--primary" :href="withBase('/intro/quick-start')">快速开始</a>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
.yx-home {
  --yx-max: 1152px;
  --yx-gap: 24px;
  --yx-brand: var(--main-700);
  --yx-brand-hover: var(--main-bright);
  --yx-brand-soft: rgba(4, 106, 130, .12);
  --yx-lime: #c6f43a;
  --yx-lime-strong: #6f9300;
  --yx-lime-soft: rgba(198, 244, 58, .24);
  color: var(--vp-c-text-1);
  font-size: 16px;
  line-height: 1.6;
}
.yx-container {
  max-width: var(--yx-max);
  margin: 0 auto;
  padding: 0 24px;
}
.yx-center { text-align: center; }
.yx-accent { color: var(--yx-brand); }

/* 段落节奏 */
.yx-section { padding: 96px 0; }
.yx-section--soft { background: var(--vp-c-bg-soft); }

/* 标题块 */
.yx-head { max-width: 720px; margin: 0 auto 48px; text-align: center; }
.yx-head__eyebrow {
  display: inline-block;
  font-size: 13px;
  font-weight: 600;
  letter-spacing: .08em;
  text-transform: uppercase;
  color: var(--yx-brand);
  margin-bottom: 12px;
}
.yx-head__title { font-size: 34px; font-weight: 700; line-height: 1.25; margin: 0; letter-spacing: -.01em; }
.yx-head__sub { margin: 16px auto 0; color: var(--vp-c-text-2); font-size: 17px; }
.yx-head__sub code, .yx-quick__tip code {
  font-size: .85em; padding: 2px 6px; border-radius: 6px;
  background: var(--vp-c-bg-alt); color: var(--yx-brand);
}

/* ===== Hero ===== */
.yx-hero { padding: 88px 0 64px; text-align: center; position: relative; overflow: hidden; }
.yx-hero__inner { position: relative; z-index: 1; }

/* 氛围背景：浮动光球 + 网格 mesh */
.yx-ambient { position: absolute; inset: 0; z-index: 0; overflow: hidden; pointer-events: none; }
.yx-orb { position: absolute; border-radius: 50%; filter: blur(70px); will-change: transform; }
.yx-orb--1 {
  width: 460px; height: 460px; top: -180px; right: -80px;
  background: var(--yx-brand-soft); opacity: .6;
  animation: yxOrbFloat 18s ease-in-out infinite;
}
.yx-orb--2 {
  width: 380px; height: 380px; top: -120px; left: -120px;
  background: var(--yx-lime-soft); opacity: .5;
  animation: yxOrbFloat 22s ease-in-out infinite reverse;
}
.yx-orb--3 {
  width: 320px; height: 320px; top: 30px; left: 46%;
  background: var(--yx-brand-soft); opacity: .5;
  animation: yxOrbFloat 26s ease-in-out infinite;
}
.yx-grid {
  position: absolute; inset: 0;
  background-image:
    linear-gradient(to right, var(--vp-c-divider) 1px, transparent 1px),
    linear-gradient(to bottom, var(--vp-c-divider) 1px, transparent 1px);
  background-size: 60px 60px; opacity: .5;
  -webkit-mask-image: radial-gradient(ellipse 75% 60% at 50% 0%, #000, transparent 72%);
  mask-image: radial-gradient(ellipse 75% 60% at 50% 0%, #000, transparent 72%);
}
@keyframes yxOrbFloat {
  0%, 100% { transform: translate(0, 0) scale(1); }
  50% { transform: translate(0, -26px) scale(1.04); }
}
.yx-badge {
  display: inline-block; font-size: 13px; font-weight: 500;
  padding: 6px 14px; border-radius: 999px;
  border: 1px solid var(--vp-c-divider);
  background: var(--vp-c-bg); color: var(--vp-c-text-2);
  margin-bottom: 24px;
}
.yx-hero__title { font-size: 60px; font-weight: 800; line-height: 1.05; margin: 0; letter-spacing: -.03em; }
.yx-hero__subtitle { font-size: 24px; font-weight: 600; margin: 18px 0 0; color: var(--vp-c-text-1); }
.yx-hero__desc { max-width: 640px; margin: 18px auto 0; color: var(--vp-c-text-2); font-size: 17px; }
.yx-hero__actions { display: flex; flex-wrap: wrap; gap: 14px; justify-content: center; margin-top: 32px; }

/* 按钮 */
.yx-btn {
  display: inline-flex; align-items: center; gap: 6px;
  height: 44px; padding: 0 22px; border-radius: 10px;
  font-size: 15px; font-weight: 600; cursor: pointer;
  transition: transform .2s ease, background-color .2s ease, border-color .2s ease, color .2s ease;
  border: 1px solid transparent; text-decoration: none;
}
.yx-btn--primary { background: var(--yx-brand); color: #fff; }
.yx-btn--primary:hover { background: var(--yx-brand-hover); transform: translateY(-1px); }

/* 占位图 */
.yx-placeholder {
  aspect-ratio: 16 / 9; width: 100%;
  display: flex; align-items: center; justify-content: center;
  border: 1px dashed var(--vp-c-divider); border-radius: 14px;
  background:
    linear-gradient(var(--vp-c-bg-soft), var(--vp-c-bg-soft)) padding-box,
    var(--vp-c-bg);
  color: var(--vp-c-text-3); font-size: 14px; letter-spacing: .02em;
}
.yx-placeholder--hero {
  border-style: solid;
  box-shadow: 0 24px 60px -28px rgba(0, 0, 0, .25);
}

/* ===== 数据条 ===== */
.yx-stats { border-top: 1px solid var(--vp-c-divider); border-bottom: 1px solid var(--vp-c-divider); }
.yx-stats__inner { display: grid; grid-template-columns: repeat(4, 1fr); }
.yx-stat { text-align: center; padding: 32px 16px; }
.yx-stat + .yx-stat { border-left: 1px solid var(--vp-c-divider); }
.yx-stat__value {
  font-size: 36px; font-weight: 800; color: var(--yx-brand);
  font-variant-numeric: tabular-nums; letter-spacing: -.02em;
}
.yx-stat__label { margin-top: 6px; font-size: 14px; color: var(--vp-c-text-2); }

/* ===== Bento ===== */
.yx-bento {
  display: grid; gap: var(--yx-gap);
  grid-template-columns: repeat(3, 1fr);
}
.yx-cap {
  border: 1px solid var(--vp-c-divider); border-radius: 16px;
  padding: 28px; background: var(--vp-c-bg);
  transition: transform .2s ease, border-color .2s ease;
}
.yx-cap:hover { transform: translateY(-3px); border-color: var(--yx-brand); }
.yx-cap--lg { grid-column: span 1; grid-row: span 2; display: flex; flex-direction: column; }
.yx-cap__icon {
  display: inline-flex; align-items: center; justify-content: center;
  width: 44px; height: 44px; border-radius: 12px;
  background: var(--yx-brand-soft); color: var(--yx-brand);
  margin-bottom: 18px;
}
.yx-cap__icon svg { width: 22px; height: 22px; }
.yx-cap__title { font-size: 18px; font-weight: 700; margin: 0 0 8px; }
.yx-cap__desc { color: var(--vp-c-text-2); font-size: 14.5px; margin: 0; }
.yx-cap__tags { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 16px; }
.yx-tag {
  font-size: 12px; padding: 3px 10px; border-radius: 999px;
  background: var(--vp-c-bg-soft); border: 1px solid var(--vp-c-divider);
  color: var(--vp-c-text-2);
}
.yx-cap__shot-img {
  display: block; width: 100%; margin-top: 20px;
  border-radius: 12px; border: 1px solid var(--vp-c-divider);
}

/* ===== Split（知识引擎）===== */
.yx-split { display: grid; grid-template-columns: 0.82fr 1.18fr; gap: 48px; align-items: start; }
.yx-split .yx-head__eyebrow { display: inline-block; }
.yx-split .yx-head__title { text-align: left; font-size: 30px; }
.yx-tabs { list-style: none; margin: 28px 0 0; padding: 0; display: grid; gap: 8px; }
.yx-tab {
  display: flex; align-items: center; gap: 14px; padding: 12px 14px;
  border-radius: 12px; border: 1px solid transparent; cursor: pointer;
  transition: background-color .2s ease, border-color .2s ease;
}
.yx-tab:hover { background: var(--vp-c-bg); }
.yx-tab--active { background: var(--vp-c-bg); border-color: var(--vp-c-divider); }
.yx-tab .yx-list__ic { opacity: .55; transition: opacity .2s ease; }
.yx-tab--active .yx-list__ic { opacity: 1; }
.yx-list__ic {
  flex: none; width: 38px; height: 38px; border-radius: 10px;
  display: inline-flex; align-items: center; justify-content: center;
  background: var(--yx-brand-soft); color: var(--yx-brand);
}
.yx-list__ic svg { width: 20px; height: 20px; }
.yx-tab--active .yx-list__ic { background: var(--yx-lime-soft); color: var(--yx-lime-strong); }
.yx-tab__body { min-width: 0; }
.yx-tab strong { display: block; font-size: 16px; }
.yx-tab__desc {
  display: grid; grid-template-rows: 0fr;
  transition: grid-template-rows .25s ease;
}
.yx-tab--active .yx-tab__desc { grid-template-rows: 1fr; }
.yx-tab__desc p {
  overflow: hidden; margin: 0; color: var(--vp-c-text-2); font-size: 14px; line-height: 1.55;
}
.yx-tab--active .yx-tab__desc p { margin-top: 4px; }

/* 知识引擎媒体区（4:3，随 tab 切换） */
.yx-engine-media {
  position: relative; width: 100%; aspect-ratio: 4 / 3; margin-top: 40px;
  border-radius: 14px; overflow: hidden; border: 1px solid var(--vp-c-divider);
}
.yx-engine-frame { position: absolute; inset: 0; width: 100%; height: 100%; display: block; }
img.yx-engine-frame { object-fit: cover; }
.yx-engine-ph {
  display: flex; align-items: center; justify-content: center;
  background: var(--vp-c-bg-soft); color: var(--vp-c-text-3);
  font-size: 14px; letter-spacing: .02em;
}
.yx-fade-enter-active, .yx-fade-leave-active { transition: opacity .25s ease; }
.yx-fade-enter-from, .yx-fade-leave-to { opacity: 0; }

/* ===== 模型供应商墙（跑马灯）===== */
.yx-marquee {
  display: flex; flex-direction: column; gap: 18px;
  -webkit-mask-image: linear-gradient(90deg, transparent, #000 8%, #000 92%, transparent);
  mask-image: linear-gradient(90deg, transparent, #000 8%, #000 92%, transparent);
}
.yx-marquee__row { display: flex; overflow: hidden; }
.yx-marquee__track {
  display: flex; gap: 16px; flex: none; width: max-content;
  padding-right: 16px;
  animation: yxMarquee 42s linear infinite;
}
.yx-marquee__row--rev .yx-marquee__track { animation-direction: reverse; }
.yx-marquee:hover .yx-marquee__track { animation-play-state: paused; }
.yx-mq-item {
  display: flex; align-items: center; gap: 10px; flex: none;
  padding: 10px 20px; border-radius: 999px;
  border: 1px solid var(--vp-c-divider); background: var(--vp-c-bg);
  white-space: nowrap;
}
.yx-mq-item img { width: 24px; height: 24px; object-fit: contain; flex: none; }
.yx-mq-item span { font-weight: 600; font-size: 15px; color: var(--vp-c-text-1); }
@keyframes yxMarquee {
  from { transform: translateX(0); }
  to { transform: translateX(-50%); }
}

/* ===== 工作原理 ===== */
.yx-steps { display: grid; grid-template-columns: repeat(4, 1fr); gap: var(--yx-gap); }
.yx-step { position: relative; padding: 28px 24px; border: 1px solid var(--vp-c-divider); border-radius: 16px; background: var(--vp-c-bg); }
.yx-step__n { font-size: 26px; font-weight: 800; color: var(--yx-brand); font-variant-numeric: tabular-nums; }
.yx-step__title { font-size: 17px; font-weight: 700; margin: 10px 0 8px; }
.yx-step__desc { margin: 0; color: var(--vp-c-text-2); font-size: 14px; }
.yx-step__arrow {
  position: absolute; right: -16px; top: 50%; transform: translateY(-50%);
  color: var(--vp-c-text-3); font-size: 18px; z-index: 1;
}

/* ===== 产品一览 ===== */
.yx-shots { display: grid; grid-template-columns: repeat(2, 1fr); gap: 32px; }
.yx-shot { margin: 0; }
.yx-shot figcaption { margin-top: 14px; }
.yx-shot figcaption strong { font-size: 16px; }
.yx-shot figcaption span { display: block; color: var(--vp-c-text-2); font-size: 14px; margin-top: 2px; }

/* ===== 三卡（企业级 / 场景）===== */
.yx-cards3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: var(--yx-gap); }
.yx-card {
  border: 1px solid var(--vp-c-divider); border-radius: 16px;
  padding: 28px; background: var(--vp-c-bg);
  transition: transform .2s ease, border-color .2s ease;
}
.yx-card:hover { transform: translateY(-3px); border-color: var(--yx-brand); }
.yx-card h3 { font-size: 18px; font-weight: 700; margin: 0 0 8px; }
.yx-card p { margin: 0; color: var(--vp-c-text-2); font-size: 14.5px; }
.yx-card--case { border-left: 3px solid var(--yx-lime); }

/* ===== 技术栈 ===== */
.yx-tech { max-width: 860px; margin: 0 auto; }
.yx-tech__row { display: grid; grid-template-columns: 100px 1fr; gap: 24px; align-items: center; padding: 18px 0; }
.yx-tech__row + .yx-tech__row { border-top: 1px solid var(--vp-c-divider); }
.yx-tech__group { font-weight: 600; color: var(--vp-c-text-2); font-size: 14px; }
.yx-tech__items { display: flex; flex-wrap: wrap; gap: 10px; }
.yx-chip {
  font-size: 14px; font-weight: 500; padding: 6px 14px; border-radius: 8px;
  background: var(--vp-c-bg-soft); border: 1px solid var(--vp-c-divider);
}

/* ===== 快速开始 ===== */
.yx-quick { max-width: 760px; margin: 0 auto; }
.yx-code {
  margin: 0; padding: 24px; border-radius: 14px;
  background: #1b1b1f;
  border: 1px solid var(--vp-c-divider);
  overflow-x: auto; font-size: 14px; line-height: 1.8;
  font-family: var(--vp-font-family-mono);
  color: #e6e6e6;
}
.yx-c-cmt { color: #6a9955; }
.yx-quick__tip { margin: 18px 0 0; text-align: center; color: var(--vp-c-text-2); font-size: 14px; }

/* ===== 开源致谢 ===== */
.yx-credits { margin: 0; color: var(--vp-c-text-2); font-size: 14.5px; }
.yx-credits a { color: var(--yx-brand); text-decoration: none; }
.yx-credits a:hover { text-decoration: underline; }

/* ===== 最终 CTA ===== */
.yx-cta { padding: 96px 0; text-align: center; border-top: 1px solid var(--vp-c-divider); }
.yx-cta__inner h2 { font-size: 34px; font-weight: 800; margin: 0; letter-spacing: -.01em; }
.yx-cta__inner p { margin: 14px 0 0; color: var(--vp-c-text-2); font-size: 17px; }
.yx-cta__actions { margin-top: 28px; }

/* ===== 进场动画 ===== */
.reveal { opacity: 0; transform: translateY(18px); transition: opacity .6s ease, transform .6s ease; }
.reveal.in-view { opacity: 1; transform: none; }

/* ===== 响应式 ===== */
@media (max-width: 1024px) {
  .yx-bento { grid-template-columns: repeat(2, 1fr); }
  .yx-cap--lg { grid-column: span 2; grid-row: auto; }
}
@media (max-width: 768px) {
  .yx-section, .yx-hero, .yx-cta { padding: 64px 0; }
  .yx-hero { padding-top: 64px; }
  .yx-hero__title { font-size: 44px; }
  .yx-hero__subtitle { font-size: 20px; }
  .yx-head__title { font-size: 28px; }
  .yx-bento, .yx-cards3, .yx-steps, .yx-shots { grid-template-columns: 1fr; }
  .yx-cap--lg { grid-column: auto; }
  .yx-split { grid-template-columns: 1fr; gap: 32px; }
  .yx-split .yx-head__title { text-align: center; }
  .yx-split__text { text-align: center; }
  .yx-tab { text-align: left; }
  .yx-stats__inner { grid-template-columns: repeat(2, 1fr); }
  .yx-stat:nth-child(3) { border-left: none; }
  .yx-stat:nth-child(odd) { border-left: none; }
  .yx-stat:nth-child(even) { border-left: 1px solid var(--vp-c-divider); }
  .yx-stat:nth-child(n+3) { border-top: 1px solid var(--vp-c-divider); }
  .yx-step__arrow { display: none; }
}
@media (prefers-reduced-motion: reduce) {
  .reveal { opacity: 1; transform: none; transition: none; }
  .yx-btn:hover, .yx-cap:hover, .yx-card:hover, .yx-provider:hover { transform: none; }
  .yx-orb, .yx-marquee__track { animation: none; }
}
</style>
