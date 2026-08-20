<template>
  <div class="expert-project-view">
    <div v-if="loading && project" class="loading-bar-wrapper">
      <div class="loading-bar"></div>
    </div>

    <header class="project-topbar">
      <button type="button" class="back-button" @click="goBack">
        <ArrowLeft :size="16" />
        <span>专家项目</span>
      </button>

      <div class="project-title-block">
        <div class="project-title-copy">
          <h1>{{ project?.name || '专家项目' }}</h1>
          <p v-if="project">
            {{ project.description || currentStageMessage }}
          </p>
        </div>
      </div>

      <div v-if="project" class="project-topbar-actions">
        <span class="status-badge" :class="`tone-${getExpertStatusTone(project.status)}`">
          {{ getExpertStatusLabel(project.status) }}
        </span>
        <a-tooltip :title="project.status === 'paused' ? '恢复项目' : '暂停项目'">
          <a-button
            v-if="project.status === 'paused' || canPauseProject"
            class="icon-button"
            :aria-label="project.status === 'paused' ? '恢复专家项目' : '暂停专家项目'"
            :loading="actionKey === 'pause' || actionKey === 'resume'"
            :disabled="Boolean(actionKey)"
            @click="project.status === 'paused' ? resumeProject() : pauseProject()"
          >
            <Play v-if="project.status === 'paused'" :size="15" />
            <Pause v-else :size="15" />
          </a-button>
        </a-tooltip>
        <a-dropdown>
          <a-button class="icon-button" aria-label="更多项目操作" :disabled="Boolean(actionKey)">
            <MoreHorizontal :size="16" />
          </a-button>
          <template #overlay>
            <a-menu @click="handleProjectMenu">
              <a-menu-item v-if="project.status === 'enabled'" key="uninstall">
                <PackageMinus :size="15" />
                卸载专家 Skill
              </a-menu-item>
              <a-menu-divider v-if="project.status === 'enabled'" />
              <a-menu-item key="delete" danger :disabled="!canDeleteProject">
                <Trash2 :size="15" />
                {{ deleteProjectLabel }}
              </a-menu-item>
            </a-menu>
          </template>
        </a-dropdown>
        <a-tooltip title="刷新项目">
          <a-button
            class="icon-button"
            aria-label="刷新专家项目"
            :loading="loading"
            @click="loadProject"
          >
            <RefreshCw :size="15" />
          </a-button>
        </a-tooltip>
      </div>
    </header>

    <div v-if="loading && !project" class="project-loading">
      <a-spin />
    </div>

    <ResourceEmptyState
      v-else-if="loadError && !project"
      title="无法打开专家项目"
      :description="loadError"
      :icon="CircleAlert"
      full-height
    >
      <template #actions>
        <a-button @click="loadProject">重试</a-button>
        <a-button type="primary" @click="goBack">返回项目列表</a-button>
      </template>
    </ResourceEmptyState>

    <template v-else-if="project">
      <nav class="stage-strip" aria-label="专家学习主流程">
        <ol class="stage-list">
          <li
            v-for="(stage, index) in EXPERT_STAGE_ITEMS"
            :key="stage.key"
            :class="{
              active: index === displayedStageIndex,
              current: index === currentStageIndex,
              completed: index < currentStageIndex,
              reviewable: index <= currentStageIndex,
              attention: index === currentStageIndex && project.status === 'needs_attention'
            }"
          >
            <button
              type="button"
              class="stage-step"
              :disabled="index > currentStageIndex"
              :aria-pressed="index === displayedStageIndex"
              @click="selectStage(stage.key, index)"
            >
              <span class="stage-marker">
                <Check v-if="index < currentStageIndex" :size="13" />
                <span v-else>{{ index + 1 }}</span>
              </span>
              <span>{{ stage.label }}</span>
            </button>
          </li>
        </ol>
      </nav>

      <div class="project-summary-band">
        <div class="summary-progress">
          <div>
            <strong>{{ getExpertStageLabel(project.stage) }}</strong>
            <span>{{ currentStageMessage }}</span>
          </div>
          <span>{{ overallProgress }}%</span>
        </div>
        <a-progress :percent="overallProgress" :show-info="false" :stroke-width="5" />
        <div v-if="showProjectError" class="actionable-error" role="alert">
          <CircleAlert :size="16" />
          <span>{{ project.error_message }}</span>
        </div>
      </div>

      <main class="project-content">
        <a-alert
          v-if="project.status === 'paused'"
          type="warning"
          show-icon
          class="paused-alert"
          message="项目已暂停"
          description="后台恢复扫描不会继续投递这个项目。恢复后，系统会从当前项目事实继续必要工作。"
        >
          <template #action>
            <a-button size="small" :loading="actionKey === 'resume'" @click="resumeProject">
              恢复项目
            </a-button>
          </template>
        </a-alert>
        <section v-if="isReviewingHistory" class="project-section stage-history-section">
          <div class="history-toolbar">
            <div>
              <span class="section-kicker">阶段回看</span>
              <h2>{{ getExpertStageLabel(reviewStage) }}</h2>
              <p>这里展示该阶段已保存的产物，不会改变当前项目进度。</p>
            </div>
            <a-button type="text" @click="returnToCurrentStage">
              回到当前进度
              <ArrowRight :size="14" />
            </a-button>
          </div>

          <template v-if="reviewStage === 'orientation'">
            <div v-if="project.learning_plan" class="document-block">
              <h3>学习方案</h3>
              <MarkdownPreview :content="project.learning_plan" compact />
            </div>
            <details v-if="project.orientation_report" class="disclosure-block">
              <summary>查看定向依据</summary>
              <MarkdownPreview :content="project.orientation_report" compact />
            </details>
            <div v-if="project.method_samples?.length" class="artifact-list stage-history-list">
              <article
                v-for="sample in project.method_samples"
                :key="sample.material_id"
                class="artifact-row"
              >
                <header>
                  <div>
                    <strong>{{ sample.title }}</strong>
                    <span>{{ sample.source }}</span>
                  </div>
                  <span class="status-badge" :class="`tone-${getExpertStatusTone(sample.status)}`">
                    {{ getExpertMaterialStatusLabel(sample.status) }}
                  </span>
                </header>
                <details>
                  <summary>查看笔记与 Review</summary>
                  <MarkdownPreview :content="sampleNote(sample)" compact />
                  <p class="review-summary">
                    {{ sampleReview(sample)?.summary || '暂无 Review 摘要' }}
                  </p>
                </details>
              </article>
            </div>
          </template>

          <template v-else-if="reviewStage === 'material_learning'">
            <div class="count-strip history-count-strip">
              <span>全部 {{ project.material_summary?.total || project.materials.length }}</span>
              <span>通过 {{ materialStatusCount('passed') }}</span>
              <span>不入选 {{ materialStatusCount('no_value') }}</span>
              <span>需要处理 {{ materialStatusCount('needs_attention') }}</span>
            </div>
            <div class="material-list section-list">
              <article
                v-for="material in project.materials"
                :key="material.material_id"
                class="material-row learning-row"
              >
                <div class="material-copy">
                  <strong>{{ material.title }}</strong>
                  <span>{{ material.source_path }}</span>
                </div>
                <span class="status-badge" :class="`tone-${getExpertStatusTone(material.status)}`">
                  {{ getExpertMaterialStatusLabel(material.status) }}
                </span>
                <details v-if="material.note || material.review" class="row-details">
                  <summary>查看笔记与 Review</summary>
                  <MarkdownPreview v-if="material.note" :content="material.note" compact />
                  <p v-if="material.review" class="review-summary">
                    Review：{{ material.review.summary || '暂无摘要' }}
                  </p>
                </details>
              </article>
            </div>
          </template>

          <template v-else-if="reviewStage === 'organization'">
            <div v-if="project.topic_map?.plan_markdown" class="document-block">
              <h3>主题与讲义规划</h3>
              <MarkdownPreview :content="project.topic_map.plan_markdown" compact />
            </div>
            <details v-if="project.editorial_plan" class="disclosure-block">
              <summary>当前编辑方案</summary>
              <MarkdownPreview :content="project.editorial_plan" compact />
            </details>
            <div class="topic-list stage-history-list">
              <article v-for="topic in project.topics" :key="topic.topic_id" class="topic-row">
                <div class="topic-position">{{ topic.position }}</div>
                <div class="topic-copy">
                  <span>{{ topic.section }}</span>
                  <strong>{{ topic.title }}</strong>
                  <p>{{ topic.reader_question }}</p>
                </div>
                <span v-if="topic.is_sample" class="sample-label">讲义小样</span>
              </article>
            </div>
          </template>

          <template v-else-if="reviewStage === 'handout_compilation'">
            <ExpertTopicReader
              v-model="selectedTopicId"
              :topics="project.topics || []"
              empty-text="这个项目没有保存正式讲义。"
            />
            <details v-if="project.final_review" class="disclosure-block history-final-review">
              <summary>查看全书检查结果</summary>
              <MarkdownPreview :content="finalReviewMarkdown" compact />
            </details>
          </template>
        </section>

        <section v-else-if="project.status === 'paused'" class="project-section paused-section">
          <Pause :size="22" />
          <div>
            <h2>当前阶段已停止后台推进</h2>
            <p>{{ project.message || '项目已暂停，等待显式恢复。' }}</p>
          </div>
        </section>

        <template v-else>
          <template v-if="project.stage === 'orientation'">
            <section
              v-if="hasMethodArtifacts && project.status !== 'running'"
              class="project-section"
            >
              <div class="section-heading">
                <div>
                  <span class="section-kicker">方法版本 {{ project.method_version }}</span>
                  <h2>这样学习这批材料，对不对？</h2>
                  <p>确认后，当前学习方向会固定并用于后续逐份学习。</p>
                </div>
                <span class="status-badge" :class="`tone-${getExpertStatusTone(project.status)}`">
                  {{ getExpertStatusLabel(project.status) }}
                </span>
              </div>

              <div class="document-block">
                <h3>学习方案</h3>
                <MarkdownPreview :content="project.learning_plan || ''" compact />
              </div>

              <details v-if="project.orientation_report" class="disclosure-block">
                <summary>查看定向依据</summary>
                <MarkdownPreview :content="project.orientation_report" compact />
              </details>

              <div class="subsection-heading">
                <div>
                  <h3>真实材料笔记小样</h3>
                  <p>每份小样都经过独立 Review；具体意见默认收起。</p>
                </div>
              </div>
              <div class="artifact-list">
                <article
                  v-for="sample in project.method_samples || []"
                  :key="sample.material_id"
                  class="artifact-row"
                >
                  <header>
                    <div>
                      <strong>{{ sample.title }}</strong>
                      <span>{{ sample.source }}</span>
                    </div>
                    <span
                      class="status-badge"
                      :class="`tone-${getExpertStatusTone(sample.status)}`"
                    >
                      {{ getExpertMaterialStatusLabel(sample.status) }}
                    </span>
                  </header>
                  <MarkdownPreview :content="sampleNote(sample)" compact />
                  <details class="review-disclosure">
                    <summary>查看 Review</summary>
                    <p class="review-summary">
                      {{ sampleReview(sample)?.summary || '暂无 Review 摘要' }}
                    </p>
                    <ul v-if="sampleReview(sample)?.issues?.length" class="issue-list">
                      <li v-for="(issue, index) in sampleReview(sample).issues" :key="index">
                        <strong>{{ issue.problem }}</strong>
                        <span>{{ issue.revision_direction }}</span>
                      </li>
                    </ul>
                  </details>
                </article>
              </div>

              <details class="disclosure-block prompt-disclosure">
                <summary>查看阶段指令（高级检查）</summary>
                <div class="prompt-list">
                  <details v-for="(prompt, key) in project.prompt_pack || {}" :key="key">
                    <summary>{{ promptLabel(key) }}</summary>
                    <pre>{{ prompt }}</pre>
                  </details>
                </div>
              </details>

              <div class="decision-panel">
                <a-textarea
                  v-if="project.status === 'awaiting_confirmation'"
                  v-model:value="methodFeedback"
                  :rows="3"
                  placeholder="如果方向还需要调整，请写明哪里没有贴合材料、希望怎样改变学习方法"
                />
                <div class="decision-actions">
                  <a-button
                    v-if="project.status === 'needs_attention'"
                    :loading="actionKey === 'retry-method'"
                    :disabled="Boolean(actionKey)"
                    @click="retryMethod"
                  >
                    <RotateCcw :size="14" />
                    重新试跑当前方法
                  </a-button>
                  <a-button
                    v-if="project.status === 'awaiting_confirmation'"
                    :loading="actionKey === 'revise-method'"
                    :disabled="Boolean(actionKey) || !methodFeedback.trim()"
                    @click="reviseMethod"
                  >
                    <RotateCcw :size="14" />
                    按意见调整学习方向
                  </a-button>
                  <a-button
                    v-if="project.status === 'awaiting_confirmation'"
                    type="primary"
                    :loading="actionKey === 'confirm-method'"
                    :disabled="Boolean(actionKey)"
                    @click="confirmMethod"
                  >
                    <Check :size="14" />
                    确认学习方向
                  </a-button>
                </div>
              </div>
            </section>

            <section
              v-else-if="isExpertMethodPreparationRunning(project)"
              class="project-section processing-section"
            >
              <LoaderCircle :size="22" class="spin" />
              <div>
                <h2>正在形成项目学习方案并试跑小样</h2>
                <p>{{ project.message }}</p>
              </div>
            </section>

            <section v-else-if="canChooseRepresentatives" class="project-section">
              <div class="section-heading">
                <div>
                  <span class="section-kicker">项目定向</span>
                  <h2>选择 1～20 份代表性材料</h2>
                  <p>系统会完整阅读所选材料，用它们试跑项目专属学习方法。选择由你明确完成。</p>
                </div>
                <span class="selection-count"
                  >已选 {{ selectedRepresentativeIds.length }} /
                  {{ maxRepresentativeMaterials }}</span
                >
              </div>

              <div class="material-list">
                <div
                  v-for="material in selectableMaterials"
                  :key="material.material_id"
                  class="material-row selectable"
                  :class="{ selected: selectedRepresentativeIds.includes(material.material_id) }"
                  role="checkbox"
                  tabindex="0"
                  :aria-checked="selectedRepresentativeIds.includes(material.material_id)"
                  @click="toggleRepresentative(material.material_id)"
                  @keydown.enter.prevent="toggleRepresentative(material.material_id)"
                  @keydown.space.prevent="toggleRepresentative(material.material_id)"
                >
                  <a-checkbox
                    :checked="selectedRepresentativeIds.includes(material.material_id)"
                    tabindex="-1"
                    aria-hidden="true"
                  />
                  <div class="material-copy">
                    <strong>{{ material.title }}</strong>
                    <span>{{ material.source_path }} · {{ material.line_count }} 行</span>
                  </div>
                  <a-button
                    v-if="canPreviewExpertMaterial(material)"
                    type="text"
                    size="small"
                    @click.stop="openMaterialPreview(material)"
                  >
                    <Eye :size="14" />
                    正文
                  </a-button>
                </div>
              </div>

              <div class="decision-panel">
                <a-textarea
                  v-model:value="methodFeedback"
                  :rows="2"
                  placeholder="可补充希望重点继承的判断方式或需要保留的材料边界"
                />
                <div class="decision-actions">
                  <a-button
                    type="primary"
                    :disabled="selectedRepresentativeIds.length < 1 || Boolean(actionKey)"
                    :loading="actionKey === 'orientation'"
                    @click="startOrientation"
                  >
                    <Compass :size="14" />
                    {{
                      project.status === 'needs_attention'
                        ? '重新生成学习方案与小样'
                        : '生成学习方案与小样'
                    }}
                  </a-button>
                </div>
              </div>
            </section>

            <section v-else class="project-section">
              <div class="section-heading">
                <div>
                  <span class="section-kicker">材料准备</span>
                  <h2>把材料转换为可靠正文</h2>
                  <p>准备完成后，由你从可见材料清单中选择代表样本。</p>
                </div>
                <span>{{ materialProgress }}%</span>
              </div>
              <a-progress :percent="materialProgress" :show-info="false" :stroke-width="6" />

              <div class="material-list section-list">
                <div
                  v-for="material in project.materials"
                  :key="material.material_id"
                  class="material-row"
                >
                  <FileText :size="16" />
                  <div class="material-copy">
                    <strong>{{ material.title }}</strong>
                    <span>{{ material.source_path }}</span>
                    <span v-if="material.error_message" class="row-error">{{
                      material.error_message
                    }}</span>
                  </div>
                  <span
                    class="status-badge"
                    :class="`tone-${getExpertStatusTone(material.status)}`"
                  >
                    {{ getExpertMaterialStatusLabel(material.status) }}
                  </span>
                  <div v-if="material.status === 'needs_attention'" class="row-actions">
                    <a-button
                      size="small"
                      :loading="actionKey === `retry-conversion:${material.material_id}`"
                      :disabled="Boolean(actionKey)"
                      @click="retryMaterialConversion(material)"
                    >
                      重试转换
                    </a-button>
                    <a-button
                      size="small"
                      danger
                      :disabled="Boolean(actionKey) || !canExcludeMaterial(material)"
                      :title="excludeButtonTitle(material)"
                      @click="openExclude(material)"
                    >
                      排除
                    </a-button>
                  </div>
                </div>
              </div>
            </section>
          </template>

          <section v-else-if="project.stage === 'material_learning'" class="project-section">
            <div class="section-heading">
              <div>
                <span class="section-kicker">逐份学习与 Review</span>
                <h2>批量学习材料</h2>
                <p>
                  每份材料在后台完成笔记、Review
                  和最多两次自动修订；仍未通过的材料会保留具体意见，等待人工处理。
                </p>
              </div>
              <span>{{ materialProgress }}%</span>
            </div>
            <a-progress :percent="materialProgress" :show-info="false" :stroke-width="6" />

            <div class="count-strip">
              <button
                v-for="option in materialFilterOptions"
                :key="option.key"
                type="button"
                class="material-filter"
                :class="{
                  active: materialFilter === option.key,
                  attention: option.key === 'needs_attention' && materialStatusCount(option.key) > 0
                }"
                :aria-pressed="materialFilter === option.key"
                @click="materialFilter = option.key"
              >
                {{ option.label }} {{ materialStatusCount(option.key) }}
              </button>
            </div>

            <div class="stage-next-step">
              <span>下一步</span>
              <strong
                >全部材料通过、不入选或完成必要处理后，系统自动生成主题方案与讲义小样。</strong
              >
            </div>

            <div v-if="filteredMaterials.length" class="material-list section-list">
              <article
                v-for="material in filteredMaterials"
                :key="material.material_id"
                class="material-row learning-row"
              >
                <div class="material-copy">
                  <strong>{{ material.title }}</strong>
                  <span>{{ material.source_path }}</span>
                  <span v-if="material.error_message" class="row-error">{{
                    material.error_message
                  }}</span>
                  <span v-if="material.exclusion_reason" class="row-muted">
                    排除原因：{{ material.exclusion_reason }}
                  </span>
                </div>
                <span class="status-badge" :class="`tone-${getExpertStatusTone(material.status)}`">
                  {{ getExpertMaterialStatusLabel(material.status) }}
                </span>
                <div class="row-actions">
                  <a-button
                    v-if="canPreviewExpertMaterial(material)"
                    type="text"
                    size="small"
                    @click="openMaterialPreview(material)"
                  >
                    <Eye :size="14" />
                    正文
                  </a-button>
                  <a-button
                    v-if="material.status === 'needs_attention'"
                    size="small"
                    :loading="actionKey === `retry-learning:${material.material_id}`"
                    :disabled="Boolean(actionKey)"
                    @click="retryMaterialLearning(material)"
                  >
                    重跑学习
                  </a-button>
                  <a-button
                    v-if="material.status === 'needs_attention'"
                    size="small"
                    danger
                    :disabled="Boolean(actionKey) || !canExcludeMaterial(material)"
                    :title="excludeButtonTitle(material)"
                    @click="openExclude(material)"
                  >
                    明确排除
                  </a-button>
                </div>
                <details v-if="material.note || material.review" class="row-details">
                  <summary>查看笔记与 Review</summary>
                  <MarkdownPreview v-if="material.note" :content="material.note" compact />
                  <div v-if="material.review" class="review-box">
                    <strong>Review：{{ material.review.summary || '暂无摘要' }}</strong>
                    <ul v-if="material.review.issues?.length" class="issue-list">
                      <li v-for="(issue, index) in material.review.issues" :key="index">
                        <strong>{{ issue.problem }}</strong>
                        <span>{{ issue.revision_direction }}</span>
                      </li>
                    </ul>
                  </div>
                </details>
              </article>
            </div>
            <a-empty v-else class="filtered-material-empty" description="当前筛选没有材料" />
          </section>

          <template v-else-if="project.stage === 'organization'">
            <section v-if="project.topic_map?.topics?.length" class="project-section">
              <div class="section-heading">
                <div>
                  <span class="section-kicker">组织版本 {{ project.organization_version }}</span>
                  <h2>
                    {{
                      project.status === 'running'
                        ? '正在生成主题方案和讲义小样'
                        : '这样组织和表达已经学到的知识，对不对？'
                    }}
                  </h2>
                  <p>
                    {{
                      project.status === 'running'
                        ? project.message
                        : '确认后，主题地图、编辑方案和讲义小样会固定并进入批量讲义编写。'
                    }}
                  </p>
                </div>
                <span class="status-badge" :class="`tone-${getExpertStatusTone(project.status)}`">
                  {{ getExpertStatusLabel(project.status) }}
                </span>
              </div>

              <div class="document-block">
                <h3>主题与讲义规划</h3>
                <MarkdownPreview :content="project.topic_map.plan_markdown || ''" compact />
              </div>

              <details v-if="project.editorial_plan" class="disclosure-block">
                <summary>当前编辑方案</summary>
                <MarkdownPreview :content="project.editorial_plan" compact />
              </details>

              <div class="subsection-heading">
                <div>
                  <h3>主题关系与覆盖范围</h3>
                  <p>{{ project.topic_map.sample_selection_reason }}</p>
                </div>
              </div>
              <div class="topic-list">
                <article v-for="topic in project.topics" :key="topic.topic_id" class="topic-row">
                  <div class="topic-position">{{ topic.position }}</div>
                  <div class="topic-copy">
                    <span>{{ topic.section }}</span>
                    <strong>{{ topic.title }}</strong>
                    <p>{{ topic.reader_question }}</p>
                    <details>
                      <summary>查看主题边界</summary>
                      <p>{{ topic.boundaries }}</p>
                    </details>
                  </div>
                  <span v-if="topic.is_sample" class="sample-label">讲义小样</span>
                </article>
              </div>

              <div
                v-if="excludedMaterials.length || project.topic_map.unassigned_notes?.length"
                class="excluded-band"
              >
                <h3>本轮未纳入</h3>
                <ul>
                  <li v-for="material in excludedMaterials" :key="material.material_id">
                    <strong>{{ material.title }}</strong>
                    <span>{{
                      material.exclusion_reason || material.error_message || 'Review 判定不入选'
                    }}</span>
                  </li>
                  <li
                    v-for="item in project.topic_map.unassigned_notes || []"
                    :key="item.note_alias"
                  >
                    <strong>{{ item.note_alias }}</strong>
                    <span>{{ item.reason }}</span>
                  </li>
                </ul>
              </div>

              <div class="subsection-heading">
                <div>
                  <h3>讲义小样</h3>
                  <p>总编模型定稿，Reviewer 只给出具体问题。</p>
                </div>
              </div>
              <div class="artifact-list">
                <article
                  v-for="sample in project.organization_samples || []"
                  :key="sample.topic_id"
                  class="artifact-row"
                >
                  <header>
                    <strong>{{ sample.title }}</strong>
                    <span
                      class="status-badge"
                      :class="`tone-${getExpertStatusTone(sample.status)}`"
                    >
                      {{ getExpertMaterialStatusLabel(sample.status) }}
                    </span>
                  </header>
                  <MarkdownPreview :content="sample.handout || ''" compact />
                  <details class="review-disclosure">
                    <summary>查看 Review</summary>
                    <p class="review-summary">{{ sample.review?.summary || '暂无 Review 摘要' }}</p>
                    <ul v-if="sample.review?.issues?.length" class="issue-list">
                      <li v-for="(issue, index) in sample.review.issues" :key="index">
                        <strong>{{ issue.problem }}</strong>
                        <span>{{ issue.revision_direction }}</span>
                      </li>
                    </ul>
                  </details>
                </article>
              </div>

              <div v-if="canReviseExpertOrganization(project.status)" class="decision-panel">
                <a-textarea
                  v-model:value="organizationFeedback"
                  :rows="3"
                  placeholder="需要调整时，说明主题边界、覆盖关系或讲义表达应如何变化"
                />
                <div class="decision-actions">
                  <a-button
                    :loading="actionKey === 'revise-organization'"
                    :disabled="Boolean(actionKey)"
                    @click="reviseOrganization"
                  >
                    <RotateCcw :size="14" />
                    按意见重新生成
                  </a-button>
                  <a-button
                    v-if="project.status === 'awaiting_confirmation'"
                    type="primary"
                    :loading="actionKey === 'confirm-organization'"
                    :disabled="Boolean(actionKey)"
                    @click="confirmOrganization"
                  >
                    <Check :size="14" />
                    确认组织方式
                  </a-button>
                </div>
              </div>
            </section>

            <section
              v-else-if="project.status === 'needs_attention'"
              class="project-section organization-attention"
            >
              <div class="section-heading">
                <div>
                  <span class="section-kicker">需要处理</span>
                  <h2>主题方案尚未形成</h2>
                  <p>{{ project.error_message || project.message }}</p>
                </div>
              </div>
              <div class="decision-panel">
                <a-textarea
                  v-model:value="organizationFeedback"
                  :rows="3"
                  placeholder="说明应怎样调整主题范围、会改变判断的来源差异或组织方式"
                />
                <div class="decision-actions">
                  <a-button
                    type="primary"
                    :loading="actionKey === 'revise-organization'"
                    :disabled="Boolean(actionKey)"
                    @click="reviseOrganization"
                  >
                    <RotateCcw :size="14" />
                    按意见重新生成
                  </a-button>
                </div>
              </div>
            </section>

            <section v-else class="project-section processing-section">
              <LoaderCircle :size="22" class="spin" />
              <div>
                <h2>总编模型正在形成主题方案</h2>
                <p>{{ project.message }}</p>
              </div>
            </section>
          </template>

          <section v-else-if="project.stage === 'handout_compilation'" class="project-section">
            <div class="section-heading">
              <div>
                <span class="section-kicker">总编成书</span>
                <h2>批量编写讲义并完成全书收口</h2>
                <p>证据准备可以并行；正式讲义、关键修订和最终回答口径由项目内总编模型统一完成。</p>
              </div>
              <span>{{ topicProgress }}%</span>
            </div>
            <a-progress :percent="topicProgress" :show-info="false" :stroke-width="6" />

            <ExpertTopicReader
              v-model="selectedTopicId"
              class="section-list"
              :topics="project.topics || []"
              allow-retry
              :retrying-topic-id="
                actionKey.startsWith('retry-topic:') ? actionKey.slice('retry-topic:'.length) : ''
              "
              @retry="retryTopic"
            />

            <details v-if="project.final_review" class="disclosure-block">
              <summary>查看全书一致性结论</summary>
              <MarkdownPreview :content="finalReviewMarkdown" compact />
            </details>

            <div v-if="showCompilationRetry" class="decision-panel compact-decision">
              <span>{{ project.error_message || '全书收口需要重新执行。' }}</span>
              <a-button
                type="primary"
                :loading="actionKey === 'retry-compilation'"
                :disabled="Boolean(actionKey)"
                @click="retryCompilation"
              >
                <RotateCcw :size="14" />
                按意见继续收口
              </a-button>
            </div>
          </section>

          <section v-else-if="project.stage === 'release'" class="project-section release-section">
            <div class="section-heading release-heading">
              <div>
                <span class="section-kicker">专家 Skill 成品</span>
                <h2>
                  {{ project.status === 'enabled' ? '专家 Skill 已启用' : '预览成品并手动启用' }}
                </h2>
                <p>运行时只读取下方讲义、导航和精选证据，不访问项目内部原始材料。</p>
              </div>
              <div class="release-actions">
                <a-button
                  v-if="project.status === 'enabled' && project.skill_slug"
                  @click="openInstalledSkill"
                >
                  查看已启用 Skill
                  <ArrowRight :size="14" />
                </a-button>
                <a-button
                  v-if="project.status === 'ready'"
                  type="primary"
                  :loading="actionKey === 'activate'"
                  :disabled="Boolean(actionKey) || previewLoading || !preview"
                  @click="activateProject"
                >
                  <Power :size="14" />
                  启用专家 Skill
                </a-button>
              </div>
            </div>

            <div v-if="previewLoading" class="preview-loading"><a-spin /></div>
            <a-alert v-else-if="previewError" type="error" show-icon :message="previewError">
              <template #action
                ><a-button size="small" @click="loadPreview">重试</a-button></template
              >
            </a-alert>

            <a-tabs v-else-if="preview" v-model:activeKey="previewTab" class="preview-tabs">
              <a-tab-pane key="skill" tab="专家说明">
                <div class="document-block preview-document primary-preview-document">
                  <MarkdownPreview :content="preview.skill_md || ''" compact />
                </div>
              </a-tab-pane>
              <a-tab-pane key="navigation" tab="使用导航">
                <div class="document-block preview-document primary-preview-document">
                  <MarkdownPreview :content="preview.navigation || ''" compact />
                </div>
              </a-tab-pane>
              <a-tab-pane key="handouts" :tab="`讲义 ${preview.handouts?.length || 0}`">
                <a-collapse class="preview-collapse">
                  <a-collapse-panel
                    v-for="item in preview.handouts || []"
                    :key="item.path"
                    :header="displayFileName(item.path)"
                  >
                    <MarkdownPreview :content="item.content || ''" compact />
                  </a-collapse-panel>
                </a-collapse>
              </a-tab-pane>
              <a-tab-pane key="references" :tab="`精选证据 ${preview.references?.length || 0}`">
                <a-collapse class="preview-collapse">
                  <a-collapse-panel
                    v-for="item in preview.references || []"
                    :key="item.path"
                    :header="displayFileName(item.path)"
                  >
                    <MarkdownPreview :content="item.content || ''" compact />
                  </a-collapse-panel>
                </a-collapse>
              </a-tab-pane>
              <a-tab-pane key="sources" :tab="`来源 ${preview.sources?.length || 0}`">
                <div class="source-list">
                  <div
                    v-for="source in preview.sources || []"
                    :key="source.evidence_id"
                    class="source-row"
                  >
                    <strong>{{ source.evidence_id }} · {{ source.source_title }}</strong>
                    <span
                      >{{ source.source_path }} · L{{ source.start_line }}–L{{
                        source.end_line
                      }}</span
                    >
                    <p>{{ (source.supports || []).join('；') }}</p>
                  </div>
                </div>
              </a-tab-pane>
              <a-tab-pane key="excluded" :tab="`未纳入 ${preview.excluded_materials?.length || 0}`">
                <div v-if="preview.excluded_materials?.length" class="source-list">
                  <div
                    v-for="material in preview.excluded_materials"
                    :key="material.material_id"
                    class="source-row"
                  >
                    <strong>{{ material.title }}</strong>
                    <span>{{ material.source_path }}</span>
                    <p>{{ material.reason }}</p>
                  </div>
                </div>
                <a-empty v-else description="本项目没有未纳入材料" />
              </a-tab-pane>
            </a-tabs>
          </section>
        </template>
      </main>
    </template>

    <a-modal
      v-model:open="materialPreviewOpen"
      :title="materialPreview?.title || '可靠正文'"
      width="900px"
      :footer="null"
      :destroy-on-close="true"
    >
      <div v-if="materialPreviewLoading" class="preview-loading"><a-spin /></div>
      <MarkdownPreview v-else :content="materialPreview?.text || ''" compact />
    </a-modal>

    <a-modal
      v-model:open="excludeOpen"
      title="明确排除材料"
      ok-text="排除后继续"
      ok-type="danger"
      cancel-text="取消"
      :confirm-loading="actionKey === `exclude:${excludeTarget?.material_id}`"
      @ok="submitExclusion"
      @cancel="resetExclusion"
    >
      <p class="modal-copy">
        {{ excludeTarget?.title }} 将不参与后续主题规划，并持续出现在未纳入清单中。
      </p>
      <a-textarea
        v-model:value="exclusionReason"
        :rows="3"
        placeholder="说明为什么本轮明确排除这份材料"
      />
    </a-modal>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import {
  ArrowLeft,
  ArrowRight,
  Check,
  CircleAlert,
  Compass,
  Eye,
  FileText,
  LoaderCircle,
  MoreHorizontal,
  PackageMinus,
  Pause,
  Play,
  Power,
  RefreshCw,
  RotateCcw,
  Trash2
} from 'lucide-vue-next'

import { expertApi } from '@/apis/expert_api'
import MarkdownPreview from '@/components/common/MarkdownPreview.vue'
import ExpertTopicReader from '@/components/extensions/ExpertTopicReader.vue'
import ResourceEmptyState from '@/components/shared/ResourceEmptyState.vue'
import {
  EXPERT_STAGE_ITEMS,
  canDeleteExpertProject,
  canExcludeExpertMaterial,
  canPauseExpertProject,
  canPreviewExpertMaterial,
  canReviseExpertOrganization,
  formatExpertFinalReview,
  getExcludedExpertMaterials,
  getExpertMaterialProgress,
  getExpertMaterialStatusLabel,
  getExpertProjectProgress,
  getExpertStageIndex,
  getExpertStageLabel,
  getExpertStatusLabel,
  getExpertStatusTone,
  getExpertTopicProgress,
  isExpertMethodPreparationRunning,
  shouldPollExpertProject
} from '@/utils/expertProject'

const route = useRoute()
const router = useRouter()
const projectId = computed(() => String(route.params.projectId || ''))
const project = ref(null)
const loading = ref(false)
const loadError = ref('')
const actionKey = ref('')
const selectedRepresentativeIds = ref([])
const maxRepresentativeMaterials = 20
const representativeTouched = ref(false)
const methodFeedback = ref('')
const organizationFeedback = ref('')
const preview = ref(null)
const previewLoading = ref(false)
const previewError = ref('')
const previewTab = ref('skill')
const materialPreviewOpen = ref(false)
const materialPreviewLoading = ref(false)
const materialPreview = ref(null)
const excludeOpen = ref(false)
const excludeTarget = ref(null)
const exclusionReason = ref('')
const reviewStage = ref('')
const selectedTopicId = ref('')
const materialFilter = ref('all')
let pollingTimer = null
let projectRequestInFlight = false

const currentStageIndex = computed(() => getExpertStageIndex(project.value?.stage))
const displayedStageIndex = computed(() =>
  getExpertStageIndex(reviewStage.value || project.value?.stage)
)
const isReviewingHistory = computed(
  () => Boolean(reviewStage.value) && reviewStage.value !== project.value?.stage
)
const overallProgress = computed(() => Math.round(getExpertProjectProgress(project.value || {})))
const materialProgress = computed(() => getExpertMaterialProgress(project.value?.materials || []))
const topicProgress = computed(() => getExpertTopicProgress(project.value?.topics || []))
const currentStageMessage = computed(() => {
  const currentProject = project.value
  if (!currentProject) return ''
  if (currentProject.stage === 'material_learning' && currentProject.status === 'running') {
    const active = (currentProject.materials || []).filter((material) =>
      ['pending', 'learning', 'review_revise'].includes(material.status)
    ).length
    return active ? `正在学习 ${active} 份材料` : currentProject.message
  }
  return currentProject.message || getExpertStatusLabel(currentProject.status)
})
const showProjectError = computed(
  () => project.value?.status === 'needs_attention' && Boolean(project.value?.error_message)
)
const materialFilterOptions = [
  { key: 'all', label: '全部' },
  { key: 'passed', label: '通过' },
  { key: 'no_value', label: '不入选' },
  { key: 'review_revise', label: '自动修订' },
  { key: 'needs_attention', label: '需要处理' }
]
const filteredMaterials = computed(() => {
  const materials = project.value?.materials || []
  if (materialFilter.value === 'all') return materials
  return materials.filter((material) => material.status === materialFilter.value)
})
const hasMethodArtifacts = computed(
  () => Boolean(project.value?.learning_plan) || Boolean(project.value?.method_samples?.length)
)
const selectableMaterials = computed(() =>
  (project.value?.materials || []).filter((material) => material.status === 'ready')
)
const canChooseRepresentatives = computed(
  () =>
    ['ready', 'needs_attention'].includes(project.value?.status) &&
    selectableMaterials.value.length > 0 &&
    !(project.value?.materials || []).some((material) => material.status === 'needs_attention')
)
const excludedMaterials = computed(() => getExcludedExpertMaterials(project.value?.materials || []))
const showCompilationRetry = computed(
  () =>
    project.value?.status === 'needs_attention' &&
    !(project.value?.topics || []).some((topic) => topic.status === 'needs_attention')
)
const finalReviewMarkdown = computed(() => formatExpertFinalReview(project.value?.final_review))
const canPauseProject = computed(() => canPauseExpertProject(project.value))
const canDeleteProject = computed(() => canDeleteExpertProject(project.value))
const deleteProjectLabel = computed(() => {
  if (project.value?.status === 'enabled') return '删除项目（先卸载 Skill）'
  if (['running', 'preparing'].includes(project.value?.status)) return '删除项目（先暂停）'
  return '删除专家项目'
})

/** 切换已完成阶段的只读回看，不修改项目当前阶段。 */
const selectStage = (stage, index) => {
  if (index > currentStageIndex.value) return
  reviewStage.value = stage === project.value?.stage ? '' : stage
}

/** 返回后台当前正在推进的阶段。 */
const returnToCurrentStage = () => {
  reviewStage.value = ''
}

const promptLabels = {
  note_author: '逐份学习',
  note_reviewer: '笔记 Review',
  note_reviser: '笔记定点修订',
  topic_researcher: '候选主题研究',
  topic_editor: '主题地图与编辑方案',
  handout_editor: '讲义总编',
  handout_reviewer: '讲义 Review',
  handout_reviser: '讲义定点修订',
  book_editor: '全书收口',
  navigation_author: '读者导航',
  skill_author: '最终专家身份'
}

/** 读取项目正式事实，并在进入发布阶段时加载候选成品。 */
const loadProject = async ({ silent = false } = {}) => {
  if (!projectId.value || projectRequestInFlight) return
  projectRequestInFlight = true
  if (!silent) loading.value = true
  loadError.value = ''
  try {
    const response = await expertApi.get(projectId.value)
    const nextProject = response?.item
    if (!nextProject) throw new Error('项目响应缺少正式记录')
    project.value = nextProject
    if (!representativeTouched.value) {
      selectedRepresentativeIds.value = [...(nextProject.representative_material_ids || [])]
    }
    if (
      nextProject.stage === 'release' &&
      ['ready', 'enabled'].includes(nextProject.status) &&
      !preview.value &&
      !previewLoading.value
    ) {
      void loadPreview()
    }
  } catch (error) {
    loadError.value = error.message || '加载专家项目失败'
    if (!silent) message.error(loadError.value)
  } finally {
    if (!silent) loading.value = false
    projectRequestInFlight = false
  }
}

/** 根据项目是否仍在后台推进来启停轮询。 */
const syncPolling = () => {
  if (shouldPollExpertProject(project.value) && !pollingTimer) {
    pollingTimer = setInterval(() => void loadProject({ silent: true }), 4000)
    return
  }
  if (!shouldPollExpertProject(project.value) && pollingTimer) {
    clearInterval(pollingTimer)
    pollingTimer = null
  }
}

/** 执行一个项目动作并重新读取正式项目记录。 */
const runAction = async (key, action, successMessage) => {
  if (actionKey.value) return false
  actionKey.value = key
  try {
    await action()
    message.success(successMessage)
    await loadProject({ silent: true })
    return true
  } catch (error) {
    message.error(error.message || '操作失败')
    return false
  } finally {
    actionKey.value = ''
  }
}

/** 返回方法小样最终采用的笔记正文。 */
const sampleNote = (sample) =>
  sample?.revision?.note_markdown || sample?.note?.note_markdown || sample?.note || ''

/** 返回方法小样最终一次 Review。 */
const sampleReview = (sample) => sample?.final_review || sample?.review || null

/** 返回项目专属 Prompt 的阶段名称。 */
const promptLabel = (key) => promptLabels[key] || key

/** 统计某类材料当前有多少份。 */
const materialStatusCount = (status) => {
  const materials = project.value?.materials || []
  if (status === 'all') return materials.length
  return materials.filter((material) => material.status === status).length
}

/** 当前材料被排除后，项目是否仍保留可进入学习的材料。 */
const canExcludeMaterial = (material) =>
  canExcludeExpertMaterial(project.value?.materials || [], material.material_id)

/** 说明为什么最后一份可用材料不能被排除。 */
const excludeButtonTitle = (material) =>
  canExcludeMaterial(material) ? '' : '项目至少需要保留一份可学习材料'

/** 切换代表材料，同时严格限制为 1～20 份。 */
const toggleRepresentative = (materialId) => {
  representativeTouched.value = true
  if (selectedRepresentativeIds.value.includes(materialId)) {
    selectedRepresentativeIds.value = selectedRepresentativeIds.value.filter(
      (id) => id !== materialId
    )
    return
  }
  if (selectedRepresentativeIds.value.length >= maxRepresentativeMaterials) {
    message.warning(`最多选择 ${maxRepresentativeMaterials} 份代表性材料`)
    return
  }
  selectedRepresentativeIds.value = [...selectedRepresentativeIds.value, materialId]
}

/** 使用用户明确选择的样本生成项目方法和真实小样。 */
const startOrientation = async () => {
  if (!selectedRepresentativeIds.value.length) {
    message.warning('请选择 1～20 份代表性材料')
    return
  }
  const completed = await runAction(
    'orientation',
    () =>
      expertApi.startOrientation(
        projectId.value,
        selectedRepresentativeIds.value,
        methodFeedback.value.trim()
      ),
    '已开始生成项目学习方案与小样'
  )
  if (completed) methodFeedback.value = ''
}

/** 重新生成当前方法版本，不复用已经失败的模型输出。 */
const retryMethod = () =>
  runAction(
    'retry-method',
    () =>
      expertApi.startOrientation(projectId.value, project.value.representative_material_ids || []),
    '已开始重新生成项目学习方案与小样'
  )

/** 按用户反馈重新生成方法版本，不直接进入批量学习。 */
const reviseMethod = () => {
  const feedback = methodFeedback.value.trim()
  if (!feedback) return
  runAction(
    'revise-method',
    () =>
      expertApi.startOrientation(
        projectId.value,
        project.value.representative_material_ids || [],
        feedback
      ),
    '已按意见重新生成项目学习方案与小样'
  ).then((completed) => {
    if (completed) methodFeedback.value = ''
  })
}

/** 第一次确认当前完整方法版本。 */
const confirmMethod = () => {
  const isCalibratedMethod = Boolean(project.value?.method_calibration_completed_at)
  Modal.confirm({
    title: '确认这样学习这批材料？',
    content: isCalibratedMethod
      ? '这是校准后的学习方案、阶段指令和小样结果。确认后才会开始逐份学习全部材料。'
      : '确认后会根据小样 Review 中的可泛化问题校准一次并复跑原小样；如果方法发生变化，系统会再次停下等待确认。',
    okText: isCalibratedMethod ? '确认并开始学习' : '确认并开始校准',
    cancelText: '继续检查',
    onOk: () =>
      runAction(
        'confirm-method',
        () => expertApi.confirmMethod(projectId.value),
        isCalibratedMethod
          ? '校准后的学习方向已确认，正在开始逐份学习'
          : '学习方向已确认，正在校准方法并重新试跑小样'
      )
  })
}

/** 只重跑一份失败的正文转换。 */
const retryMaterialConversion = (material) =>
  runAction(
    `retry-conversion:${material.material_id}`,
    () => expertApi.retryMaterialConversion(projectId.value, material.material_id),
    `正在重新转换《${material.title}》`
  )

/** 只重跑一份需要处理的材料学习。 */
const retryMaterialLearning = (material) =>
  runAction(
    `retry-learning:${material.material_id}`,
    () => expertApi.retryMaterialLearning(projectId.value, material.material_id),
    `正在重新学习《${material.title}》`
  )

/** 打开明确排除材料的理由输入。 */
const openExclude = (material) => {
  if (!canExcludeMaterial(material)) {
    message.warning('项目至少需要保留一份可学习材料')
    return
  }
  excludeTarget.value = material
  exclusionReason.value = ''
  excludeOpen.value = true
}

/** 清空排除材料弹窗状态。 */
const resetExclusion = () => {
  if (actionKey.value.startsWith('exclude:')) return
  excludeTarget.value = null
  exclusionReason.value = ''
}

/** 保存用户明确给出的材料排除决定。 */
const submitExclusion = async () => {
  if (!excludeTarget.value) return
  if (!exclusionReason.value.trim()) {
    message.warning('请说明排除这份材料的原因')
    return
  }
  const target = excludeTarget.value
  const completed = await runAction(
    `exclude:${target.material_id}`,
    () =>
      expertApi.excludeMaterial(projectId.value, target.material_id, exclusionReason.value.trim()),
    `《${target.title}》已明确排除`
  )
  if (completed) {
    excludeOpen.value = false
    excludeTarget.value = null
    exclusionReason.value = ''
  }
}

/** 读取并展示一份材料转换后的完整可靠正文。 */
const openMaterialPreview = async (material) => {
  materialPreview.value = { title: material.title, text: '' }
  materialPreviewOpen.value = true
  materialPreviewLoading.value = true
  try {
    const response = await expertApi.getMaterialText(projectId.value, material.material_id)
    materialPreview.value = response?.item || materialPreview.value
  } catch (error) {
    message.error(error.message || '读取材料正文失败')
  } finally {
    materialPreviewLoading.value = false
  }
}

/** 按用户反馈创建新的组织版本。 */
const reviseOrganization = async () => {
  if (!canReviseExpertOrganization(project.value?.status)) {
    message.warning('当前组织方案仍在生成，请完成后再提交调整意见')
    return
  }
  if (!organizationFeedback.value.trim()) {
    message.warning('请先写明需要调整的组织方式')
    return
  }
  const completed = await runAction(
    'revise-organization',
    () => expertApi.reviseOrganization(projectId.value, organizationFeedback.value.trim()),
    '已按反馈创建新的组织版本'
  )
  if (completed) organizationFeedback.value = ''
}

/** 第二次确认主题地图、编辑方案和讲义小样。 */
const confirmOrganization = () => {
  Modal.confirm({
    title: '确认这样组织和表达知识？',
    content: '确认后将固定当前组织版本，并由总编模型批量形成正式讲义和最终 Skill。',
    okText: '确认并开始编讲义',
    cancelText: '继续检查',
    onOk: () =>
      runAction(
        'confirm-organization',
        () => expertApi.confirmOrganization(projectId.value),
        '组织方式已确认，正在编写正式讲义'
      )
  })
}

/** 只重跑一篇需要处理的正式讲义。 */
const retryTopic = (topic) =>
  runAction(
    `retry-topic:${topic.topic_id}`,
    () => expertApi.retryTopic(projectId.value, topic.topic_id),
    `正在重新编写《${topic.title}》`
  )

/** 在讲义均通过后重新执行全书收口。 */
const retryCompilation = () =>
  runAction(
    'retry-compilation',
    () => expertApi.retryCompilation(projectId.value),
    '正在重新执行全书收口'
  )

/** 读取候选 Skill 中允许发布的成品文件。 */
const loadPreview = async () => {
  if (previewLoading.value) return
  previewLoading.value = true
  previewError.value = ''
  try {
    const response = await expertApi.preview(projectId.value)
    preview.value = response?.item || null
    if (!preview.value) throw new Error('成品预览为空')
  } catch (error) {
    previewError.value = error.message || '加载成品预览失败'
  } finally {
    previewLoading.value = false
  }
}

/** 在用户预览后手动启用专家 Skill。 */
const activateProject = () => {
  Modal.confirm({
    title: '启用这个专家 Skill？',
    content: '启用后，Agent 可以读取当前正式讲义、导航与精选证据。',
    okText: '确认启用',
    cancelText: '继续预览',
    onOk: () =>
      runAction('activate', () => expertApi.activate(projectId.value), '专家 Skill 已启用')
  })
}

/** 暂停后台推进，保留项目当前阶段和全部正式产物。 */
const pauseProject = () => {
  Modal.confirm({
    title: '暂停这个专家项目？',
    content: '项目记录和已完成产物会保留；恢复后只继续当前阶段仍需要执行的工作。',
    okText: '暂停项目',
    cancelText: '取消',
    onOk: () => runAction('pause', () => expertApi.pause(projectId.value), '专家项目已暂停')
  })
}

/** 依据项目正式事实恢复暂停项目。 */
const resumeProject = () =>
  runAction('resume', () => expertApi.resume(projectId.value), '专家项目已恢复')

/** 卸载通用 Skill，候选成品仍留在项目中。 */
const uninstallProject = () => {
  Modal.confirm({
    title: '卸载这个专家 Skill？',
    content: 'Agent 将不再使用已安装 Skill；项目和候选成品会保留，可再次启用。',
    okText: '确认卸载',
    okType: 'danger',
    cancelText: '取消',
    onOk: () =>
      runAction('uninstall', () => expertApi.uninstall(projectId.value), '专家 Skill 已卸载')
  })
}

/** 删除项目事实和编译产物，不触碰资料库中的来源材料。 */
const deleteProject = () => {
  if (!canDeleteProject.value) return
  Modal.confirm({
    title: '删除这个专家项目？',
    content: '项目记录、笔记、Review、讲义和候选成品将被删除；资料库材料不会删除。',
    okText: '删除项目',
    okType: 'danger',
    cancelText: '取消',
    onOk: async () => {
      if (actionKey.value) return
      actionKey.value = 'delete'
      try {
        await expertApi.delete(projectId.value)
        message.success('专家项目已删除')
        goBack()
      } catch (error) {
        message.error(error.message || '删除专家项目失败')
      } finally {
        actionKey.value = ''
      }
    }
  })
}

const handleProjectMenu = ({ key }) => {
  if (key === 'uninstall') uninstallProject()
  if (key === 'delete') deleteProject()
}

/** 打开当前项目已经启用的通用 Skill 管理页。 */
const openInstalledSkill = () => {
  router.push({
    name: 'ExtensionSkillDetail',
    params: { slug: project.value.skill_slug }
  })
}

/** 只展示发布物中的用户可读文件名。 */
const displayFileName = (path) =>
  String(path || '')
    .split('/')
    .pop() || path

/** 返回专家项目列表。 */
const goBack = () => router.push({ path: '/extensions', query: { tab: 'expert-projects' } })

watch(() => [project.value?.stage, project.value?.status], syncPolling, { immediate: true })
watch(
  () => project.value?.topics,
  (topics) => {
    if (!topics?.length) {
      selectedTopicId.value = ''
      return
    }
    if (!topics.some((topic) => topic.topic_id === selectedTopicId.value)) {
      selectedTopicId.value = topics[0].topic_id
    }
  },
  { immediate: true }
)

onMounted(loadProject)
onBeforeUnmount(() => {
  if (pollingTimer) clearInterval(pollingTimer)
})
</script>

<style scoped lang="less">
.expert-project-view {
  display: flex;
  width: 100%;
  max-width: 100%;
  min-width: 0;
  min-height: 0;
  flex: 1;
  flex-direction: column;
  overflow: hidden;
  background: var(--gray-0);
}

.loading-bar-wrapper {
  position: fixed;
  z-index: 20;
  top: 0;
  right: 0;
  left: 0;
  height: 2px;
  overflow: hidden;
}

.loading-bar {
  width: 36%;
  height: 100%;
  background: var(--main-color);
  animation: loading-slide 1.2s ease-in-out infinite;
}

.project-topbar {
  display: grid;
  width: 100%;
  max-width: 100%;
  min-width: 0;
  min-height: 68px;
  grid-template-columns: auto minmax(0, 1fr) auto;
  align-items: center;
  gap: 18px;
  padding: 10px var(--page-padding);
  border-bottom: 1px solid var(--gray-150);
  background: var(--gray-0);
}

.back-button,
.icon-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  border: 0;
  background: transparent;
  color: var(--gray-700);
  cursor: pointer;
}

.back-button {
  width: fit-content;
  padding: 6px 0;
  font-size: 13px;
}

.project-title-block,
.project-topbar-actions,
.section-heading,
.subsection-heading,
.summary-progress,
.decision-actions,
.release-actions {
  display: flex;
  align-items: center;
}

.project-title-block {
  min-width: 0;
}

.project-title-copy {
  min-width: 0;

  h1,
  p {
    margin: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  h1 {
    color: var(--gray-900);
    font-size: 16px;
    font-weight: 650;
    letter-spacing: 0;
  }

  p {
    margin-top: 2px;
    color: var(--gray-600);
    font-size: 12px;
  }
}

.project-topbar-actions {
  justify-content: flex-end;
  gap: 10px;
}

.stage-strip {
  width: 100%;
  max-width: 100%;
  min-width: 0;
  overflow-x: auto;
  border-bottom: 1px solid var(--gray-150);
  background: var(--gray-25);
}

.stage-list {
  display: grid;
  width: min(100%, 1080px);
  min-width: 720px;
  grid-template-columns: repeat(5, minmax(120px, 1fr));
  margin: 0 auto;
  padding: 0 var(--page-padding);
  list-style: none;

  li {
    position: relative;
    display: flex;
    min-height: 52px;
    align-items: center;
    justify-content: center;
    gap: 8px;
    color: var(--gray-500);
    font-size: 12px;

    &::after {
      position: absolute;
      right: -12%;
      width: 24%;
      height: 1px;
      background: var(--gray-200);
      content: '';
    }

    &:last-child::after {
      display: none;
    }

    &.active {
      color: var(--gray-900);
      font-weight: 600;
    }

    &.completed {
      color: var(--main-700);
    }

    &.attention {
      color: var(--color-error-700);
    }
  }
}

.stage-step {
  display: inline-flex;
  min-width: 0;
  min-height: 52px;
  align-items: center;
  justify-content: center;
  gap: 8px;
  border: 0;
  background: transparent;
  color: inherit;
  cursor: default;
  font: inherit;

  &:disabled {
    cursor: default;
  }

  .reviewable & {
    cursor: pointer;

    &:hover {
      color: var(--main-700);
    }

    &:focus-visible {
      outline: 2px solid var(--main-500);
      outline-offset: -3px;
    }
  }
}

.stage-marker {
  display: inline-flex;
  width: 24px;
  height: 24px;
  flex: 0 0 24px;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--gray-200);
  border-radius: 50%;
  background: var(--gray-0);
  font-size: 11px;

  .active & {
    border-color: var(--main-700);
    background: var(--main-700);
    color: var(--gray-0);
  }

  .completed & {
    border-color: var(--color-success-500);
    color: var(--color-success-700);
  }

  .attention & {
    border-color: var(--color-error-500);
    background: var(--color-error-50);
    color: var(--color-error-700);
  }
}

.stage-list li.active .stage-marker {
  border-color: var(--main-700);
  background: var(--main-700);
  color: var(--gray-0);
}

.project-summary-band {
  width: 100%;
  max-width: 100%;
  min-width: 0;
  padding: 14px var(--page-padding);
  border-bottom: 1px solid var(--gray-150);
}

.summary-progress {
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 6px;
  font-size: 12px;

  div {
    display: flex;
    min-width: 0;
    align-items: baseline;
    gap: 10px;
  }

  strong {
    color: var(--gray-900);
  }

  span {
    color: var(--gray-600);
  }
}

.actionable-error {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin-top: 10px;
  padding: 9px 11px;
  border: 1px solid var(--color-error-100);
  border-radius: 6px;
  background: var(--color-error-10);
  color: var(--color-error-700);
  font-size: 13px;
  line-height: 1.5;
}

.project-content {
  width: 100%;
  max-width: 100%;
  min-width: 0;
  flex: 1;
  overflow-x: hidden;
  overflow-y: auto;
}

.paused-alert {
  margin: 16px var(--page-padding) 0;
}

.history-toolbar {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 20px;

  h2,
  p,
  .section-kicker {
    margin: 0;
  }

  h2 {
    margin-top: 4px;
    color: var(--gray-900);
    font-size: 21px;
    font-weight: 650;
    line-height: 1.35;
  }

  p {
    margin-top: 7px;
    color: var(--gray-600);
    font-size: 13px;
    line-height: 1.65;
  }

  :deep(.ant-btn) {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
}

.stage-history-list {
  margin-top: 20px;
}

.history-count-strip {
  margin-top: 0;
}

.project-section {
  width: min(100%, 1080px);
  max-width: 100%;
  margin: 0 auto;
  padding: 28px var(--page-padding) 56px;
}

.section-heading,
.subsection-heading {
  justify-content: space-between;
  gap: 18px;
}

.section-heading {
  align-items: flex-start;
  margin-bottom: 20px;

  h2,
  p,
  .section-kicker {
    margin: 0;
  }

  h2 {
    margin-top: 4px;
    color: var(--gray-900);
    font-size: 21px;
    font-weight: 650;
    line-height: 1.35;
    letter-spacing: 0;
  }

  p {
    max-width: 760px;
    margin-top: 7px;
    color: var(--gray-600);
    font-size: 13px;
    line-height: 1.65;
  }
}

.section-kicker {
  color: var(--main-700);
  font-size: 12px;
  font-weight: 600;
}

.subsection-heading {
  margin: 28px 0 12px;

  h3,
  p {
    margin: 0;
  }

  h3 {
    color: var(--gray-900);
    font-size: 15px;
  }

  p {
    margin-top: 4px;
    color: var(--gray-600);
    font-size: 12px;
  }
}

.status-badge,
.selection-count,
.sample-label {
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

.document-block,
.disclosure-block,
.decision-panel,
.excluded-band {
  border: 1px solid var(--gray-150);
  border-radius: 7px;
  background: var(--gray-0);
}

.document-block {
  padding: 18px;

  > h3 {
    margin: 0 0 14px;
    color: var(--gray-900);
    font-size: 14px;
  }
}

.disclosure-block {
  margin-top: 14px;
  padding: 0 16px;

  > summary {
    padding: 14px 0;
    color: var(--gray-800);
    cursor: pointer;
    font-size: 13px;
    font-weight: 600;
  }

  &[open] > summary {
    border-bottom: 1px solid var(--gray-150);
  }

  > :not(summary) {
    margin-top: 14px;
    margin-bottom: 16px;
  }
}

.prompt-list {
  display: grid;
  gap: 8px;

  details {
    border-bottom: 1px solid var(--gray-150);
  }

  summary {
    padding: 9px 0;
    color: var(--gray-700);
    cursor: pointer;
    font-size: 12px;
  }

  pre {
    max-height: 360px;
    overflow: auto;
    margin: 0 0 12px;
    padding: 12px;
    background: var(--gray-25);
    color: var(--gray-800);
    font-size: 12px;
    line-height: 1.65;
    white-space: pre-wrap;
    word-break: break-word;
  }
}

.artifact-list,
.material-list,
.topic-list,
.source-list {
  border-top: 1px solid var(--gray-150);
}

.artifact-row,
.material-row,
.topic-row,
.source-row {
  border-bottom: 1px solid var(--gray-150);
}

.artifact-row {
  padding: 18px 4px;

  > header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 14px;

    div {
      display: flex;
      min-width: 0;
      flex-direction: column;
      gap: 3px;
    }

    strong {
      color: var(--gray-900);
      font-size: 14px;
    }

    span:not(.status-badge) {
      overflow: hidden;
      color: var(--gray-500);
      font-size: 12px;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
  }
}

.review-disclosure,
.row-details,
.topic-copy details {
  margin-top: 12px;

  summary {
    width: fit-content;
    color: var(--main-700);
    cursor: pointer;
    font-size: 12px;
  }
}

.review-summary {
  margin: 10px 0 0;
  color: var(--gray-700);
  font-size: 13px;
  line-height: 1.6;
}

.issue-list {
  display: grid;
  gap: 8px;
  margin: 10px 0 0;
  padding: 0;
  list-style: none;

  li {
    display: flex;
    flex-direction: column;
    gap: 3px;
    padding-left: 10px;
    border-left: 2px solid var(--color-warning-500);
    color: var(--gray-700);
    font-size: 12px;
    line-height: 1.55;
  }
}

.decision-panel {
  display: flex;
  align-items: flex-end;
  gap: 14px;
  margin-top: 24px;
  padding: 16px;
  background: var(--gray-25);

  :deep(.ant-input) {
    flex: 1;
  }
}

.decision-actions,
.release-actions {
  flex: 0 0 auto;
  justify-content: flex-end;
  gap: 8px;

  :deep(.ant-btn) {
    display: inline-flex;
    align-items: center;
    gap: 6px;
  }
}

.material-row {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 12px;
  padding: 13px 4px;

  &.selectable {
    padding: 14px 10px;
    cursor: pointer;

    &:hover,
    &.selected {
      background: var(--main-30);
    }
  }
}

.material-copy,
.topic-copy {
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
  gap: 3px;

  strong {
    overflow-wrap: anywhere;
    color: var(--gray-900);
    font-size: 13px;
  }

  > span {
    overflow: hidden;
    color: var(--gray-500);
    font-size: 12px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
}

.row-error {
  color: var(--color-error-700) !important;
  white-space: normal !important;
}

.row-muted {
  white-space: normal !important;
}

.row-actions {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 6px;

  :deep(.ant-btn) {
    display: inline-flex;
    align-items: center;
    gap: 5px;
  }
}

.learning-row,
.compilation-row {
  flex-wrap: wrap;
}

.row-details {
  width: 100%;
  padding-left: 0;

  > :not(summary) {
    margin-top: 14px;
  }
}

.review-box {
  padding: 12px;
  border: 1px solid var(--gray-150);
  border-radius: 6px;
  background: var(--gray-25);
  color: var(--gray-700);
  font-size: 12px;
}

.section-list {
  margin-top: 20px;
}

.count-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 18px;
  margin-top: 16px;
  font-size: 12px;
}

.material-filter {
  padding: 2px 0;
  border: 0;
  background: transparent;
  color: var(--gray-600);
  cursor: pointer;
  font: inherit;

  &:hover,
  &.active {
    color: var(--main-700);
    font-weight: 600;
  }

  &:focus-visible {
    outline: 2px solid var(--main-500);
    outline-offset: 3px;
  }

  &.attention {
    color: var(--color-error-700);
    font-weight: 600;
  }
}

.filtered-material-empty {
  margin-top: 36px;
}

.stage-next-step {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-top: 16px;
  padding: 10px 12px;
  border: 1px solid var(--main-100);
  border-radius: 6px;
  background: var(--main-10);
  color: var(--gray-700);
  font-size: 13px;
  line-height: 1.6;

  span {
    flex: 0 0 auto;
    color: var(--main-700);
    font-size: 12px;
    font-weight: 600;
  }

  strong {
    color: var(--gray-800);
    font-weight: 500;
  }
}

.topic-row {
  display: flex;
  min-width: 0;
  align-items: flex-start;
  gap: 14px;
  padding: 16px 4px;
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

.topic-copy {
  > p {
    margin: 2px 0 0;
    color: var(--gray-700);
    font-size: 13px;
    line-height: 1.55;
  }

  details p {
    margin: 8px 0 0;
    color: var(--gray-600);
    font-size: 12px;
    line-height: 1.6;
  }
}

.sample-label {
  border-color: var(--main-200);
  background: var(--main-30);
  color: var(--main-700);
}

.excluded-band {
  margin-top: 24px;
  padding: 16px;
  background: var(--color-warning-10);

  h3 {
    margin: 0 0 10px;
    color: var(--gray-900);
    font-size: 14px;
  }

  ul {
    display: grid;
    gap: 8px;
    margin: 0;
    padding: 0;
    list-style: none;
  }

  li {
    display: flex;
    flex-direction: column;
    gap: 2px;
    color: var(--gray-700);
    font-size: 12px;
  }
}

.processing-section,
.paused-section {
  display: flex;
  min-height: 300px;
  align-items: center;
  justify-content: center;
  gap: 14px;

  h2,
  p {
    margin: 0;
  }

  h2 {
    color: var(--gray-900);
    font-size: 17px;
  }

  p {
    margin-top: 5px;
    color: var(--gray-600);
    font-size: 13px;
  }
}

.paused-section > svg {
  color: var(--color-warning-700);
}

.spin {
  animation: spin 1s linear infinite;
}

.compact-decision {
  align-items: center;
  justify-content: space-between;
  color: var(--gray-700);
  font-size: 13px;
}

.release-heading {
  align-items: center;
}

.preview-loading,
.project-loading {
  display: flex;
  min-height: 260px;
  align-items: center;
  justify-content: center;
}

.preview-document {
  min-height: 320px;
}

.primary-preview-document {
  max-width: 820px;

  :deep(.frontmatter-card) {
    display: none;
  }
}

.history-final-review {
  margin-top: 20px;
}

.preview-collapse {
  border: 1px solid var(--gray-150);
  background: var(--gray-0);

  :deep(.ant-collapse-item) {
    border-color: var(--gray-150);
  }

  :deep(.ant-collapse-header) {
    color: var(--gray-800);
    font-size: 13px;
  }

  :deep(.ant-collapse-content-box) {
    padding: 18px;
  }
}

.source-row {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 14px 4px;

  strong {
    overflow-wrap: anywhere;
    color: var(--gray-900);
    font-size: 13px;
  }

  span,
  p {
    margin: 0;
    overflow-wrap: anywhere;
    color: var(--gray-600);
    font-size: 12px;
    line-height: 1.55;
  }
}

.modal-copy {
  margin: 0 0 14px;
  color: var(--gray-700);
  font-size: 13px;
  line-height: 1.6;
}

@keyframes loading-slide {
  from {
    transform: translateX(-110%);
  }
  to {
    transform: translateX(390%);
  }
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 768px) {
  .project-topbar {
    min-height: auto;
    grid-template-columns: 1fr auto;
    gap: 8px 12px;
    padding-left: 64px;
    padding-top: 10px;
    padding-bottom: 10px;
  }

  .back-button {
    grid-column: 1 / -1;
    min-height: 40px;
  }

  .icon-button {
    width: 40px;
    min-width: 40px;
    height: 40px;
  }

  .project-section :deep(.ant-btn) {
    min-height: 40px;
  }

  .project-title-copy p {
    white-space: normal;
  }

  .stage-list {
    min-width: 650px;
    padding: 0 10px;
  }

  .project-summary-band,
  .project-section {
    padding-right: 14px;
    padding-left: 14px;
  }

  .summary-progress div {
    align-items: flex-start;
    flex-direction: column;
    gap: 2px;
  }

  .section-heading,
  .release-heading,
  .decision-panel,
  .compact-decision,
  .history-toolbar,
  .section-heading h2 {
    font-size: 18px;
  }

  .decision-actions,
  .release-actions {
    width: 100%;
    flex-wrap: wrap;

    :deep(.ant-btn) {
      flex: 1 1 auto;
      justify-content: center;
    }
  }

  .material-row,
  .topic-row {
    align-items: flex-start;
    flex-wrap: wrap;
  }

  .material-copy,
  .topic-copy {
    min-width: calc(100% - 48px);
  }

  .row-actions {
    width: 100%;
    flex-wrap: wrap;
    padding-left: 40px;
  }

  .learning-row .row-actions,
  .compilation-row .row-actions {
    padding-left: 0;
  }

  .status-badge {
    max-width: 100%;
  }

  .artifact-row > header {
    flex-wrap: wrap;
  }

  .artifact-row > header span:not(.status-badge),
  .material-copy > span,
  .source-row strong,
  .source-row span,
  .source-row p,
  .actionable-error span {
    overflow: visible;
    overflow-wrap: anywhere;
    text-overflow: clip;
    white-space: normal;
  }

  :deep(.preview-tabs .ant-tabs-nav-wrap) {
    overflow-x: auto;
  }
}

@media (max-width: 420px) {
  .project-topbar-actions .status-badge {
    display: none;
  }

  .project-section {
    padding-top: 22px;
  }

  .document-block,
  .decision-panel,
  .excluded-band {
    padding: 13px;
  }
}
</style>
