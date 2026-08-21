<template>
  <div v-if="topicGroups.length" class="expert-topic-outline" :class="{ compact, interactive }">
    <section v-for="group in topicGroups" :key="group.section" class="outline-chapter">
      <header class="outline-chapter-heading">
        <div>
          <BookOpen :size="16" />
          <h3>{{ group.section }}</h3>
        </div>
        <span>{{ group.topics.length }} 个主题</span>
      </header>

      <ol class="outline-topic-list">
        <li v-for="topic in group.topics" :key="topic.topic_id">
          <component
            :is="interactive ? 'button' : 'div'"
            :type="interactive ? 'button' : undefined"
            class="outline-topic"
            :class="{ selected: topic.topic_id === modelValue }"
            :aria-current="interactive && topic.topic_id === modelValue ? 'true' : undefined"
            @click="selectTopic(topic)"
          >
            <span class="outline-position">{{ topic.position }}</span>
            <span class="outline-copy">
              <strong :title="topic.title">{{ topic.title }}</strong>
              <span v-if="!compact && topic.reader_question" class="outline-question">
                {{ topic.reader_question }}
              </span>
            </span>
            <span class="outline-meta">
              <span v-if="topic.is_sample" class="sample-label">讲义小样</span>
              <span
                v-if="showStatus"
                class="topic-state"
                :class="`tone-${getExpertStatusTone(topic.status)}`"
              >
                <Check v-if="topic.status === 'passed'" :size="13" />
                <CircleAlert v-else-if="topic.status === 'needs_attention'" :size="13" />
                <LoaderCircle v-else :size="13" :class="{ spin: isActive(topic.status) }" />
                {{ getExpertMaterialStatusLabel(topic.status) }}
              </span>
            </span>
          </component>

          <details v-if="showBoundaries && topic.boundaries" class="outline-boundaries">
            <summary>主题边界</summary>
            <p>{{ topic.boundaries }}</p>
          </details>
        </li>
      </ol>
    </section>
  </div>

  <div v-else class="outline-empty">{{ emptyText }}</div>
</template>

<script setup>
import { computed } from 'vue'
import { BookOpen, Check, CircleAlert, LoaderCircle } from 'lucide-vue-next'
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
  interactive: {
    type: Boolean,
    default: false
  },
  compact: {
    type: Boolean,
    default: false
  },
  showBoundaries: {
    type: Boolean,
    default: false
  },
  showStatus: {
    type: Boolean,
    default: true
  },
  emptyText: {
    type: String,
    default: '当前还没有主题大纲。'
  }
})

const emit = defineEmits(['update:modelValue'])

const topicGroups = computed(() => groupExpertTopics(props.topics))

/** 只在主题大纲作为目录使用时切换当前讲义。 */
const selectTopic = (topic) => {
  if (!props.interactive) return
  emit('update:modelValue', topic.topic_id)
}

/** 标识后台仍会继续变化的讲义状态。 */
const isActive = (status) =>
  ['pending', 'preparing_evidence', 'evidence_ready', 'writing', 'review_revise'].includes(status)
</script>

<style scoped lang="less">
.expert-topic-outline {
  display: grid;
  gap: 28px;
}

.outline-chapter {
  min-width: 0;
}

.outline-chapter-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 0 4px 10px;
  border-bottom: 1px solid var(--gray-200);

  > div {
    display: flex;
    min-width: 0;
    align-items: center;
    gap: 8px;
    color: var(--main-700);
  }

  h3 {
    margin: 0;
    overflow-wrap: anywhere;
    color: var(--gray-900);
    font-size: 15px;
    font-weight: 650;
    line-height: 1.4;
  }

  > span {
    flex: 0 0 auto;
    color: var(--gray-500);
    font-size: 12px;
  }
}

.outline-topic-list {
  margin: 0;
  padding: 0;
  list-style: none;

  > li {
    position: relative;
    padding-left: 18px;
    border-bottom: 1px solid var(--gray-150);

    &::before {
      position: absolute;
      top: 0;
      bottom: 0;
      left: 3px;
      width: 1px;
      background: var(--gray-200);
      content: '';
    }

    &::after {
      position: absolute;
      top: 27px;
      left: 3px;
      width: 10px;
      height: 1px;
      background: var(--gray-200);
      content: '';
    }
  }
}

.outline-topic {
  display: grid;
  width: 100%;
  min-width: 0;
  grid-template-columns: 30px minmax(0, 1fr) auto;
  align-items: start;
  gap: 10px;
  padding: 15px 4px;
  border: 0;
  background: transparent;
  color: inherit;
  font: inherit;
  text-align: left;
}

button.outline-topic {
  cursor: pointer;

  &:hover,
  &.selected {
    background: var(--main-30);
  }

  &:focus-visible {
    outline: 2px solid var(--main-500);
    outline-offset: -2px;
  }
}

.outline-position {
  display: flex;
  width: 28px;
  height: 28px;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--gray-200);
  border-radius: 6px;
  background: var(--gray-0);
  color: var(--gray-600);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.selected .outline-position {
  border-color: var(--main-600);
  background: var(--main-600);
  color: var(--gray-0);
}

.outline-copy {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 5px;

  strong {
    overflow-wrap: anywhere;
    color: var(--gray-900);
    font-size: 14px;
    font-weight: 600;
    line-height: 1.5;
  }
}

.outline-question {
  max-width: 72ch;
  color: var(--gray-600);
  font-size: 13px;
  line-height: 1.65;
}

.outline-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
}

.sample-label {
  display: inline-flex;
  min-height: 24px;
  align-items: center;
  padding: 3px 8px;
  border: 1px solid var(--main-200);
  border-radius: 999px;
  background: var(--main-30);
  color: var(--main-700);
  font-size: 12px;
  white-space: nowrap;
}

.topic-state {
  display: inline-flex;
  min-height: 24px;
  align-items: center;
  gap: 5px;
  color: var(--gray-500);
  font-size: 12px;
  white-space: nowrap;
}

.tone-active {
  color: var(--color-info-700);
}

.tone-warning {
  color: var(--color-warning-900);
}

.tone-error {
  color: var(--color-error-700);
}

.tone-success {
  color: var(--color-success-700);
}

.outline-boundaries {
  margin: -4px 4px 12px 40px;
  color: var(--gray-600);
  font-size: 12px;

  summary {
    width: fit-content;
    color: var(--main-700);
    cursor: pointer;
  }

  p {
    max-width: 72ch;
    margin: 8px 0 0;
    line-height: 1.65;
  }
}

.outline-empty {
  padding: 32px 16px;
  color: var(--gray-500);
  font-size: 13px;
  text-align: center;
}

.compact {
  gap: 18px;

  .outline-chapter-heading {
    padding: 12px 14px 8px;

    h3 {
      font-size: 13px;
    }
  }

  .outline-topic-list > li {
    padding-left: 12px;

    &::before {
      left: 2px;
    }

    &::after {
      top: 25px;
      left: 2px;
      width: 7px;
    }
  }

  .outline-topic {
    grid-template-columns: 28px minmax(0, 1fr);
    padding: 12px 10px 12px 6px;
  }

  .outline-copy strong {
    display: -webkit-box;
    overflow: hidden;
    font-size: 13px;
    -webkit-box-orient: vertical;
    -webkit-line-clamp: 2;
  }

  .outline-meta {
    grid-column: 2;
    justify-content: flex-start;
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

@media (max-width: 640px) {
  .outline-topic {
    grid-template-columns: 30px minmax(0, 1fr);
  }

  .outline-meta {
    grid-column: 2;
    justify-content: flex-start;
  }
}
</style>
