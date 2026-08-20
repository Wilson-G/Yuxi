<template>
  <div v-if="topics.length" class="expert-topic-reader">
    <aside class="topic-navigator" aria-label="讲义目录">
      <div class="topic-navigator-heading">
        <span>讲义目录</span>
        <span>{{ topics.length }} 篇</span>
      </div>
      <button
        v-for="topic in topics"
        :key="topic.topic_id"
        type="button"
        class="topic-nav-item"
        :class="{ selected: topic.topic_id === selectedTopic?.topic_id }"
        :aria-pressed="topic.topic_id === selectedTopic?.topic_id"
        @click="$emit('update:modelValue', topic.topic_id)"
      >
        <span class="topic-position">{{ topic.position }}</span>
        <span class="topic-nav-copy">
          <span>{{ topic.section }}</span>
          <strong>{{ topic.title }}</strong>
        </span>
        <span class="status-badge" :class="`tone-${getExpertStatusTone(topic.status)}`">
          {{ getExpertMaterialStatusLabel(topic.status) }}
        </span>
      </button>
    </aside>

    <article v-if="selectedTopic" class="topic-reader">
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
import { computed } from 'vue'
import { LoaderCircle } from 'lucide-vue-next'
import MarkdownPreview from '@/components/common/MarkdownPreview.vue'
import { getExpertMaterialStatusLabel, getExpertStatusTone } from '@/utils/expertProject'

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

defineEmits(['update:modelValue', 'retry'])

const selectedTopic = computed(
  () => props.topics.find((topic) => topic.topic_id === props.modelValue) || props.topics[0] || null
)
</script>

<style scoped lang="less">
.expert-topic-reader {
  display: grid;
  min-height: 460px;
  grid-template-columns: minmax(220px, 0.8fr) minmax(0, 2.2fr);
  border-top: 1px solid var(--gray-150);
  border-bottom: 1px solid var(--gray-150);
}

.topic-navigator {
  position: sticky;
  top: 0;
  min-width: 0;
  max-height: calc(100vh - 260px);
  overflow-y: auto;
  border-right: 1px solid var(--gray-150);
  background: var(--gray-25);
}

.topic-navigator-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--gray-150);
  color: var(--gray-700);
  font-size: 12px;
  font-weight: 600;

  span:last-child {
    color: var(--gray-500);
    font-weight: 400;
  }
}

.topic-nav-item {
  display: grid;
  width: 100%;
  min-width: 0;
  min-height: 56px;
  grid-template-columns: 28px minmax(0, 1fr);
  align-items: start;
  gap: 10px;
  padding: 14px 12px;
  border: 0;
  border-bottom: 1px solid var(--gray-150);
  background: transparent;
  color: inherit;
  cursor: pointer;
  text-align: left;

  &:hover,
  &.selected {
    background: var(--main-30);
  }

  &:focus-visible {
    outline: 2px solid var(--main-500);
    outline-offset: -2px;
  }

  .status-badge {
    grid-column: 2;
    justify-self: start;
  }
}

.topic-position {
  display: flex;
  width: 28px;
  height: 28px;
  flex: 0 0 28px;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--gray-200);
  border-radius: 6px;
  color: var(--gray-600);
  font-size: 12px;
}

.topic-nav-copy {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 3px;

  span {
    overflow: hidden;
    color: var(--gray-500);
    font-size: 12px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  strong {
    display: -webkit-box;
    overflow: hidden;
    color: var(--gray-900);
    font-size: 13px;
    font-weight: 600;
    line-height: 1.45;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
  }
}

.topic-reader {
  min-width: 0;
  padding: 24px 28px 40px;
}

.topic-reader-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
  padding-bottom: 18px;
  border-bottom: 1px solid var(--gray-150);

  h3,
  p {
    margin: 0;
  }

  h3 {
    margin-top: 4px;
    color: var(--gray-900);
    font-size: 19px;
    font-weight: 650;
    line-height: 1.4;
  }

  p {
    max-width: 720px;
    margin-top: 7px;
    color: var(--gray-600);
    font-size: 13px;
    line-height: 1.65;
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
  color: var(--color-warning-700);
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

.topic-document {
  max-width: 760px;
  margin-top: 24px;
}

.topic-reader-empty {
  display: flex;
  min-height: 220px;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: var(--gray-600);
  font-size: 13px;
}

.review-disclosure {
  max-width: 760px;
  margin-top: 24px;
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
    color: var(--gray-700);
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

@media (max-width: 768px) {
  .expert-topic-reader {
    min-height: 0;
    grid-template-columns: minmax(0, 1fr);
  }

  .topic-navigator {
    position: static;
    max-height: 280px;
    border-right: 0;
    border-bottom: 1px solid var(--gray-150);
  }

  .topic-reader {
    padding: 20px 0 32px;
  }

  .topic-reader-heading {
    align-items: stretch;
    flex-direction: column;
  }

  .topic-reader-actions {
    justify-content: flex-start;
  }

  .topic-document,
  .review-disclosure {
    max-width: 100%;
  }
}
</style>
