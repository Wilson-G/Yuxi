<template>
  <div v-if="topics.length" class="expert-topic-reader">
    <aside ref="navigatorRef" class="topic-navigator" aria-label="讲义章节目录">
      <div class="topic-navigator-heading">
        <span>章节目录</span>
        <span>{{ topicGroups.length }} 章 · {{ topics.length }} 篇</span>
      </div>
      <ExpertTopicOutline
        :model-value="selectedTopic?.topic_id || ''"
        :topics="topics"
        interactive
        compact
        @update:model-value="$emit('update:modelValue', $event)"
      />
    </aside>

    <article v-if="selectedTopic" class="topic-reader">
      <nav class="reader-paging" aria-label="讲义切换">
        <a-button
          type="text"
          size="small"
          :disabled="selectedIndex <= 0"
          @click="selectRelativeTopic(-1)"
        >
          <ArrowLeft :size="14" />
          上一篇
        </a-button>
        <span>第 {{ selectedIndex + 1 }} / {{ topics.length }} 篇</span>
        <a-button
          type="text"
          size="small"
          :disabled="selectedIndex >= topics.length - 1"
          @click="selectRelativeTopic(1)"
        >
          下一篇
          <ArrowRight :size="14" />
        </a-button>
      </nav>

      <header class="topic-reader-heading">
        <div>
          <span>{{ selectedTopic.section }}</span>
          <h3>{{ selectedTopic.title }}</h3>
          <p>{{ selectedTopic.reader_question }}</p>
        </div>
        <div class="topic-reader-actions">
          <span class="status-badge" :class="`tone-${getExpertStatusTone(selectedTopic.status)}`">
            {{ getExpertMaterialStatusLabel(selectedTopic.status) }}
          </span>
          <a-button
            v-if="allowRetry && selectedTopic.status === 'needs_attention'"
            size="small"
            :loading="retryingTopicId === selectedTopic.topic_id"
            :disabled="Boolean(retryingTopicId)"
            @click="$emit('retry', selectedTopic)"
          >
            重跑讲义
          </a-button>
        </div>
      </header>

      <p v-if="selectedTopic.error_message" class="reader-error">
        {{ selectedTopic.error_message }}
      </p>
      <div v-if="selectedTopic.handout" class="topic-document">
        <MarkdownPreview :content="selectedTopic.handout" compact />
      </div>
      <div v-else class="topic-reader-empty">
        <LoaderCircle v-if="selectedTopic.status !== 'needs_attention'" :size="18" class="spin" />
        <span>{{ selectedTopic.error_message || emptyText }}</span>
      </div>
      <details v-if="selectedTopic.review" class="review-disclosure">
        <summary>查看讲义检查结果</summary>
        <p>{{ selectedTopic.review.summary || '暂无检查摘要' }}</p>
      </details>
    </article>
  </div>

  <div v-else class="topic-reader-empty">{{ emptyText }}</div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from 'vue'
import { ArrowLeft, ArrowRight, LoaderCircle } from 'lucide-vue-next'
import MarkdownPreview from '@/components/common/MarkdownPreview.vue'
import ExpertTopicOutline from '@/components/extensions/ExpertTopicOutline.vue'
import {
  getExpertMaterialStatusLabel,
  getExpertStatusTone,
  groupExpertTopics
} from '@/utils/expertProject'

const props = defineProps({
  topics: {
    type: Array,
    default: () => []
  },
  modelValue: {
    type: String,
    default: ''
  },
  allowRetry: {
    type: Boolean,
    default: false
  },
  retryingTopicId: {
    type: String,
    default: ''
  },
  emptyText: {
    type: String,
    default: '当前阶段还没有正式讲义。'
  }
})

const emit = defineEmits(['update:modelValue', 'retry'])
const navigatorRef = ref(null)

const topicGroups = computed(() => groupExpertTopics(props.topics))
const selectedTopic = computed(
  () => props.topics.find((topic) => topic.topic_id === props.modelValue) || props.topics[0] || null
)
const selectedIndex = computed(() =>
  Math.max(
    0,
    props.topics.findIndex((topic) => topic.topic_id === selectedTopic.value?.topic_id)
  )
)

/** 按当前大纲顺序切换相邻讲义。 */
const selectRelativeTopic = (offset) => {
  const topic = props.topics[selectedIndex.value + offset]
  if (topic) emit('update:modelValue', topic.topic_id)
}

watch(
  () => props.modelValue,
  async () => {
    await nextTick()
    navigatorRef.value?.querySelector('[aria-current="true"]')?.scrollIntoView({ block: 'nearest' })
  }
)
</script>

<style scoped lang="less">
.expert-topic-reader {
  display: grid;
  min-height: 460px;
  grid-template-columns: minmax(240px, 0.82fr) minmax(0, 2.18fr);
  border-top: 1px solid var(--gray-150);
  border-bottom: 1px solid var(--gray-150);
}

.topic-navigator {
  position: sticky;
  top: 0;
  min-width: 0;
  max-height: calc(100vh - 210px);
  overflow-y: auto;
  border-right: 1px solid var(--gray-150);
  background: var(--gray-25);
  scrollbar-color: var(--gray-200) transparent;
  scrollbar-width: thin;
}

.topic-navigator-heading {
  position: sticky;
  z-index: 1;
  top: 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--gray-150);
  background: var(--gray-25);
  color: var(--gray-700);
  font-size: 12px;
  font-weight: 600;

  span:last-child {
    color: var(--gray-500);
    font-weight: 400;
  }
}

.topic-reader {
  min-width: 0;
  padding: 18px 32px 48px;
}

.reader-paging {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  gap: 12px;
  margin-bottom: 14px;
  color: var(--gray-500);
  font-size: 12px;
  font-variant-numeric: tabular-nums;

  :deep(.ant-btn) {
    display: inline-flex;
    align-items: center;
    gap: 5px;
  }

  :deep(.ant-btn:first-child) {
    justify-self: start;
  }

  :deep(.ant-btn:last-child) {
    justify-self: end;
  }
}

.topic-reader-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
  padding-bottom: 20px;
  border-bottom: 1px solid var(--gray-150);

  h3,
  p {
    margin: 0;
  }

  h3 {
    max-width: 28ch;
    margin-top: 5px;
    color: var(--gray-900);
    font-size: 21px;
    font-weight: 650;
    line-height: 1.4;
  }

  p {
    max-width: 70ch;
    margin-top: 8px;
    color: var(--gray-600);
    font-size: 13px;
    line-height: 1.7;
  }

  > div > span:first-child {
    color: var(--main-700);
    font-size: 12px;
    font-weight: 600;
  }
}

.topic-reader-actions {
  display: flex;
  flex: 0 0 auto;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
}

.status-badge {
  display: inline-flex;
  min-height: 24px;
  align-items: center;
  justify-content: center;
  padding: 3px 9px;
  border: 1px solid var(--gray-200);
  border-radius: 999px;
  background: var(--gray-25);
  color: var(--gray-700);
  font-size: 12px;
  line-height: 1.2;
  white-space: nowrap;
}

.tone-active {
  border-color: var(--color-info-100);
  background: var(--color-info-50);
  color: var(--color-info-700);
}

.tone-warning {
  border-color: var(--color-warning-100);
  background: var(--color-warning-50);
  color: var(--color-warning-900);
}

.tone-error {
  border-color: var(--color-error-100);
  background: var(--color-error-50);
  color: var(--color-error-700);
}

.tone-success {
  border-color: var(--color-success-100);
  background: var(--color-success-50);
  color: var(--color-success-700);
}

.topic-document {
  max-width: 74ch;
  margin-top: 24px;

  :deep(.yk-markdown-preview) {
    max-width: 74ch;

    h1,
    h2,
    h3,
    h4,
    h5,
    h6 {
      color: var(--gray-900);
      line-height: 1.4;
    }

    h1 {
      margin: 0 0 26px;
      font-size: 22px;
      font-weight: 700;
    }

    h2 {
      margin: 30px 0 10px;
      font-size: 17px;
      font-weight: 700;
    }

    h3,
    h4 {
      margin: 22px 0 8px 14px;
      font-size: 15px;
      font-weight: 650;
    }

    > p,
    > ul,
    > ol,
    > blockquote {
      margin-left: 28px;
    }

    > p {
      max-width: 70ch;
      margin-top: 0;
      margin-bottom: 14px;
      line-height: 1.85;
    }

    > ul,
    > ol {
      max-width: 70ch;
      margin-top: 0.4rem;
      margin-bottom: 18px;
      line-height: 1.8;
    }
  }
}

.topic-reader-empty {
  display: flex;
  min-height: 180px;
  align-items: center;
  justify-content: center;
  gap: 9px;
  color: var(--gray-500);
  font-size: 13px;
}

.reader-error {
  margin: 16px 0 0;
  padding: 10px 12px;
  border: 1px solid var(--color-error-100);
  border-radius: 6px;
  background: var(--color-error-10);
  color: var(--color-error-700);
  font-size: 13px;
  line-height: 1.6;
}

.review-disclosure {
  max-width: 74ch;
  margin-top: 26px;
  padding-top: 14px;
  border-top: 1px solid var(--gray-150);

  summary {
    width: fit-content;
    color: var(--main-700);
    cursor: pointer;
    font-size: 12px;
  }

  p {
    margin: 10px 0 0;
    color: var(--gray-600);
    font-size: 13px;
    line-height: 1.65;
  }
}

.spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 820px) {
  .expert-topic-reader {
    display: flex;
    flex-direction: column;
    grid-template-columns: 1fr;
  }

  .topic-reader {
    order: 1;
    padding: 18px 18px 34px;
  }

  .topic-navigator {
    position: static;
    order: 2;
    max-height: 360px;
    border-top: 1px solid var(--gray-150);
    border-right: 0;
  }

  .topic-reader-heading {
    flex-direction: column;
  }

  .topic-reader-actions {
    justify-content: flex-start;
  }
}

@media (max-width: 480px) {
  .topic-reader {
    padding-right: 14px;
    padding-left: 14px;
  }

  .topic-reader-heading h3 {
    font-size: 19px;
  }
}
</style>
