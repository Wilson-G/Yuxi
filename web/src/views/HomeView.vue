<template>
  <div class="home-view">
    <div v-if="isLoading" class="page-state">
      <a-spin size="large" />
      <p>正在连接服务...</p>
    </div>

    <div v-else-if="error" class="page-state page-state--error">
      <a-result status="error" :title="error.title" :sub-title="error.message">
        <template #extra>
          <a-button type="primary" @click="loadData">重试</a-button>
        </template>
      </a-result>
    </div>

    <template v-else>
      <header class="site-header">
        <div class="header-inner">
          <button type="button" class="brand-lockup" aria-label="返回首页" @click="goHome">
            <img :src="brandLogo" :alt="`${displayName} Logo`" />
            <span class="brand-name">{{ displayName }}</span>
            <span v-if="englishName" class="brand-english">{{ englishName }}</span>
          </button>

          <div class="header-actions">
            <UserInfoComponent :show-button="true" />
          </div>
        </div>
      </header>

      <main>
        <section class="brand-hero">
          <img class="hero-image" :src="heroImage" alt="" aria-hidden="true" />
          <div class="hero-shade" aria-hidden="true"></div>

          <div class="hero-inner">
            <div class="hero-copy">
              <h1>
                {{ displayName }}
                <small v-if="englishName">{{ englishName }}</small>
              </h1>
              <p class="hero-lead">{{ productTitle }}</p>
              <p class="hero-support">{{ productSubtitle }}</p>

              <div class="hero-actions">
                <button type="button" class="primary-action" @click="goToWorkspace">
                  <span>{{ workspaceActionLabel }}</span>
                  <ArrowRight :size="18" />
                </button>
                <button type="button" class="secondary-action" @click="scrollToProduct">
                  <BookOpen :size="18" />
                  <span>了解知铸</span>
                </button>
              </div>
            </div>

            <ul class="learning-flow" aria-label="专家 Agent 核心能力">
              <li v-for="step in expertCapabilities" :key="step.title">
                <strong>{{ step.title }}</strong>
                <span>{{ step.detail }}</span>
              </li>
            </ul>
          </div>
        </section>

        <section class="product-definition" aria-labelledby="product-definition-title">
          <div class="definition-inner">
            <div class="definition-heading">
              <h2 id="product-definition-title">不只是能搜到，更是知道如何判断</h2>
            </div>

            <div class="definition-points">
              <article v-for="point in productPoints" :key="point.title">
                <span class="point-rule" aria-hidden="true"></span>
                <h3>{{ point.title }}</h3>
                <p>{{ point.detail }}</p>
              </article>
            </div>

            <p class="compatibility-note">
              新增专家项目只从资料库选择材料。旧知识库、向量检索和知识图谱继续作为独立兼容能力，
              不参与专家材料处理与编译。
            </p>
          </div>
        </section>
      </main>

      <footer class="site-footer">
        <span>{{ footerCopyright }}</span>
        <span>Expert Agent Foundry</span>
      </footer>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowRight, BookOpen } from 'lucide-vue-next'

import UserInfoComponent from '@/components/UserInfoComponent.vue'
import { healthApi } from '@/apis/system_api'
import { useInfoStore } from '@/stores/info'
import { useUserStore } from '@/stores/user'

const router = useRouter()
const infoStore = useInfoStore()
const userStore = useUserStore()

const expertCapabilities = [
  { title: '理解方法', detail: '读懂专家怎样思考' },
  { title: '形成判断', detail: '从现象走向结论' },
  { title: '懂得条件', detail: '识别前提、例外与边界' },
  { title: '解释依据', detail: '关键结论可以核验' }
]

const productPoints = [
  {
    title: '从材料中继承方法',
    detail: '不仅记住概念和事实，还能理解专家关注什么信号、按什么顺序分析问题。'
  },
  {
    title: '面对具体情境做判断',
    detail: '结合用户的实际条件给出结论，知道什么时候适用、什么时候需要改变判断。'
  },
  {
    title: '专业判断有据可查',
    detail: '回答能说明为什么这样判断，并把关键结论连接回可靠材料，而不是制造权威感。'
  }
]

const isLoading = ref(true)
const error = ref(null)

const displayName = computed(() => infoStore.organization?.name?.trim() || '知铸')
const englishName = computed(() => {
  const name = infoStore.branding?.name?.trim() || 'ZhiForge'
  return name === displayName.value ? '' : name
})
const brandLogo = computed(() => infoStore.organization?.logo || '/favicon.svg')
const heroImage = computed(() => infoStore.organization?.login_bg || '/login-bg.jpg')
const productTitle = computed(
  () => infoStore.branding?.title?.trim() || '把文章与经验，铸成专家的大脑'
)
const productSubtitle = computed(
  () => infoStore.branding?.subtitle?.trim() || '创建能理解方法、做出判断、解释依据的专家 Agent。'
)
const workspaceActionLabel = computed(() =>
  userStore.isLoggedIn && !userStore.isAdmin ? '使用专家 Agent' : '创建专家 Agent'
)
const footerCopyright = computed(
  () => infoStore.footer?.copyright || `© ${displayName.value} ${new Date().getFullYear()}`
)

const loadData = async () => {
  isLoading.value = true
  error.value = null

  try {
    const response = await healthApi.checkHealth()
    if (response.status !== 'ok') throw new Error('服务不可用')
    await infoStore.loadInfoConfig()
  } catch (loadError) {
    console.error('加载首页失败:', loadError)
    error.value = {
      title: '服务连接失败',
      message: '后端服务无法响应，请检查服务是否正常运行。'
    }
  } finally {
    isLoading.value = false
  }
}

const goHome = () => router.push('/')

const goToWorkspace = () => {
  if (!userStore.isLoggedIn) {
    sessionStorage.setItem('redirect', '/extensions')
    router.push('/login')
    return
  }

  router.push('/extensions')
}

const scrollToProduct = () => {
  document.getElementById('product-definition-title')?.scrollIntoView({ behavior: 'smooth' })
}

onMounted(loadData)
</script>

<style scoped lang="less">
.home-view {
  min-height: 100vh;
  color: var(--gray-1000);
  background: var(--gray-0);
}

.page-state {
  min-height: 100vh;
  display: grid;
  place-content: center;
  justify-items: center;
  gap: 14px;
  padding: 24px;
  color: var(--gray-600);
  background: var(--main-5);

  p {
    margin: 0;
  }
}

.page-state--error {
  display: flex;
  align-items: center;
  justify-content: center;
}

.site-header {
  position: fixed;
  inset: 0 0 auto;
  z-index: 20;
  height: 68px;
  border-bottom: 1px solid var(--dark-10);
  background: var(--light-95);
  backdrop-filter: blur(16px);
}

.header-inner {
  width: min(1240px, 100%);
  height: 100%;
  margin: 0 auto;
  padding: 0 28px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
}

.brand-lockup {
  min-width: 0;
  display: inline-flex;
  align-items: center;
  gap: 10px;
  padding: 0;
  border: 0;
  background: transparent;
  color: var(--main-900);
  cursor: pointer;

  img {
    width: 34px;
    height: 34px;
    flex: 0 0 34px;
  }
}

.brand-name {
  font-size: 20px;
  font-weight: 720;
  line-height: 1;
  white-space: nowrap;
}

.brand-english {
  padding-left: 10px;
  border-left: 1px solid var(--gray-300);
  color: var(--gray-600);
  font-size: 13px;
  line-height: 1;
  white-space: nowrap;
}

.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.brand-hero {
  min-height: calc(100svh - 48px);
  position: relative;
  display: flex;
  align-items: stretch;
  overflow: hidden;
  background: var(--main-1000);
}

.hero-image,
.hero-shade {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
}

.hero-image {
  object-fit: cover;
  object-position: center 38%;
}

.hero-shade {
  background: rgba(7, 26, 21, 0.82);
}

.hero-inner {
  position: relative;
  z-index: 1;
  width: min(1240px, 100%);
  min-height: 100%;
  margin: 0 auto;
  padding: 146px 28px 54px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 72px;
}

.hero-copy {
  max-width: 790px;
}

h1 {
  margin: 0;
  color: #ffffff;
  font-size: 68px;
  font-weight: 760;
  line-height: 1.06;

  small {
    display: block;
    margin-top: 10px;
    color: var(--brand-on-dark);
    font-size: 22px;
    font-weight: 600;
    line-height: 1.2;
  }
}

.hero-lead {
  max-width: 760px;
  margin: 30px 0 0;
  color: #ffffff;
  font-size: 28px;
  font-weight: 650;
  line-height: 1.35;
}

.hero-support {
  max-width: 680px;
  margin: 16px 0 0;
  color: rgba(255, 255, 255, 0.72);
  font-size: 17px;
  line-height: 1.75;
}

.hero-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 34px;
}

.primary-action,
.secondary-action {
  min-height: 46px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 0 20px;
  border-radius: 6px;
  font-size: 15px;
  font-weight: 650;
  text-decoration: none;
  cursor: pointer;
}

.primary-action {
  border: 1px solid var(--main-300);
  color: var(--main-1000);
  background: var(--main-200);

  &:hover {
    border-color: var(--main-100);
    background: var(--main-100);
  }
}

.secondary-action {
  border: 1px solid rgba(255, 255, 255, 0.28);
  color: #ffffff;
  background: rgba(255, 255, 255, 0.06);

  &:hover {
    border-color: rgba(255, 255, 255, 0.55);
    background: rgba(255, 255, 255, 0.12);
  }
}

.learning-flow {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  margin: 0;
  padding: 0;
  border-top: 1px solid rgba(255, 255, 255, 0.3);
  list-style: none;

  li {
    min-width: 0;
    padding: 18px 16px 0 0;
    display: grid;
    gap: 5px;
    border-top: 3px solid transparent;
  }

  li:first-child {
    border-top-color: var(--brand-accent);
  }

  strong {
    color: #ffffff;
    font-size: 14px;
    line-height: 1.35;
  }

  li > span:last-child {
    color: rgba(255, 255, 255, 0.58);
    font-size: 12px;
    line-height: 1.45;
  }
}

.product-definition {
  padding: 88px 28px 74px;
  background: var(--gray-0);
}

.definition-inner {
  width: min(1120px, 100%);
  margin: 0 auto;
}

.definition-heading {
  max-width: 760px;

  h2 {
    margin: 0;
    color: var(--gray-1000);
    font-size: 34px;
    font-weight: 700;
    line-height: 1.35;
  }
}

.definition-points {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0;
  margin-top: 48px;
  border-top: 1px solid var(--gray-200);

  article {
    position: relative;
    padding: 28px 32px 10px 0;
  }

  article + article {
    padding-left: 32px;
    border-left: 1px solid var(--gray-200);
  }

  h3 {
    margin: 0 0 10px;
    color: var(--main-800);
    font-size: 18px;
    font-weight: 680;
  }

  p {
    margin: 0;
    color: var(--gray-600);
    font-size: 14px;
    line-height: 1.75;
  }
}

.point-rule {
  position: absolute;
  top: -1px;
  left: 0;
  width: 52px;
  height: 3px;
  background: var(--main-600);
}

.definition-points article + article .point-rule {
  left: 32px;
}

.compatibility-note {
  margin: 46px 0 0;
  padding-top: 20px;
  border-top: 1px solid var(--gray-150);
  color: var(--gray-600);
  font-size: 13px;
  line-height: 1.7;
}

.site-footer {
  min-height: 64px;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 18px max(28px, calc((100% - 1120px) / 2));
  border-top: 1px solid var(--gray-150);
  color: var(--gray-600);
  background: var(--gray-25);
  font-size: 12px;

  a {
    color: var(--main-700);
  }
}

@media (max-width: 900px) {
  .hero-inner {
    gap: 54px;
  }

  h1 {
    font-size: 54px;
  }

  .learning-flow {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    row-gap: 22px;
  }

  .definition-points {
    grid-template-columns: 1fr;

    article,
    article + article {
      padding: 24px 0;
      border-left: 0;
      border-bottom: 1px solid var(--gray-150);
    }
  }

  .definition-points article + article .point-rule {
    left: 0;
  }
}

@media (max-width: 640px) {
  .site-header {
    height: 60px;
  }

  .header-inner {
    padding: 0 16px;
  }

  .brand-lockup img {
    width: 30px;
    height: 30px;
    flex-basis: 30px;
  }

  .brand-name {
    font-size: 18px;
  }

  .brand-english {
    display: none;
  }

  .brand-hero {
    min-height: auto;
  }

  .hero-inner {
    padding: 104px 18px 30px;
    gap: 36px;
  }

  h1 {
    font-size: 44px;

    small {
      font-size: 17px;
    }
  }

  .hero-lead {
    margin-top: 24px;
    font-size: 23px;
  }

  .hero-support {
    font-size: 15px;
  }

  .hero-actions {
    align-items: stretch;
  }

  .primary-action,
  .secondary-action {
    flex: 1 1 100%;
  }

  .learning-flow {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 16px 10px;

    li {
      min-height: 92px;
      padding-top: 14px;
      padding-right: 4px;
    }

    strong {
      font-size: 13px;
    }

    li > span:last-child {
      font-size: 11px;
    }
  }

  .product-definition {
    padding: 66px 18px 54px;
  }

  .definition-heading h2 {
    font-size: 27px;
  }

  .site-footer {
    align-items: flex-start;
    flex-direction: column;
    padding: 18px;
  }
}

@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    scroll-behavior: auto !important;
    transition: none !important;
  }
}
</style>
