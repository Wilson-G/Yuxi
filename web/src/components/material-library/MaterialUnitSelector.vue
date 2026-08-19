<template>
  <div class="material-unit-selector">
    <div class="selector-toolbar">
      <a-select
        v-model:value="activeLibraryId"
        :options="libraryOptions"
        :loading="loadingLibraries"
        placeholder="选择资料库"
        class="library-select"
      />
      <a-input v-model:value="searchQuery" allow-clear placeholder="搜索标题或来源路径" />
      <span class="selection-summary">已选 {{ selectedIds.length }} 份</span>
    </div>

    <a-alert v-if="loadError" type="error" show-icon :message="loadError" />

    <a-table
      :columns="columns"
      :data-source="filteredUnits"
      :row-key="(unit) => unit.unit_id"
      :loading="loadingUnits"
      :pagination="{ pageSize: 8, hideOnSinglePage: true, size: 'small' }"
      :row-selection="rowSelection"
      :scroll="{ x: 620 }"
      size="small"
      class="unit-table"
    >
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'title'">
          <div class="unit-title">
            <strong :title="record.title">{{ record.title }}</strong>
            <span :title="record.source_path">{{ record.source_path }}</span>
          </div>
        </template>
        <template v-else-if="column.key === 'range'">
          L{{ record.start_line }}–L{{ record.end_line }}
        </template>
      </template>
      <template #emptyText>
        <div class="selector-empty">
          <span>{{ activeLibraryId ? '当前资料库没有可选材料单元' : '还没有资料库' }}</span>
          <a-button v-if="!loadingLibraries" type="link" @click="$emit('open-library')">
            前往资料库
          </a-button>
        </div>
      </template>
    </a-table>
  </div>
</template>

<script setup>
import { computed, ref, watch } from 'vue'

import { materialLibraryApi } from '@/apis/material_library_api'
import { getReadyMaterialUnits } from '@/utils/materialLibrary'

defineEmits(['open-library'])
const selectedIds = defineModel({ type: Array, default: () => [] })
const libraries = ref([])
const activeLibraryId = ref('')
const units = ref([])
const loadingLibraries = ref(false)
const loadingUnits = ref(false)
const loadError = ref('')
const searchQuery = ref('')

const columns = [
  { title: '材料单元', key: 'title', dataIndex: 'title' },
  { title: '来源文件', key: 'source_filename', dataIndex: 'source_filename', width: 180 },
  { title: '来源行', key: 'range', width: 110 }
]

const libraryOptions = computed(() =>
  libraries.value.map((library) => ({ label: library.name, value: library.library_id }))
)
const filteredUnits = computed(() => {
  const keyword = searchQuery.value.trim().toLowerCase()
  if (!keyword) return units.value
  return units.value.filter((unit) =>
    [unit.title, unit.source_filename, unit.source_path]
      .filter(Boolean)
      .join(' ')
      .toLowerCase()
      .includes(keyword)
  )
})
const rowSelection = computed(() => ({
  selectedRowKeys: selectedIds.value,
  preserveSelectedRowKeys: true,
  onChange: (keys) => {
    selectedIds.value = keys
  }
}))

/** 加载资料库清单，并默认打开第一项。 */
const load = async () => {
  loadingLibraries.value = true
  loadError.value = ''
  try {
    const response = await materialLibraryApi.list()
    libraries.value = response?.items || []
    if (!libraries.value.some((item) => item.library_id === activeLibraryId.value)) {
      activeLibraryId.value = libraries.value[0]?.library_id || ''
    }
  } catch (error) {
    loadError.value = error.message || '加载资料库失败'
  } finally {
    loadingLibraries.value = false
  }
}

watch(activeLibraryId, async (libraryId) => {
  units.value = []
  if (!libraryId) return
  loadingUnits.value = true
  loadError.value = ''
  try {
    const response = await materialLibraryApi.listUnits(libraryId)
    if (activeLibraryId.value === libraryId) {
      units.value = getReadyMaterialUnits(response?.items || [])
    }
  } catch (error) {
    if (activeLibraryId.value === libraryId) {
      loadError.value = error.message || '加载材料单元失败'
    }
  } finally {
    if (activeLibraryId.value === libraryId) loadingUnits.value = false
  }
})

defineExpose({ load })
</script>

<style scoped lang="less">
.material-unit-selector {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.selector-toolbar {
  display: grid;
  grid-template-columns: minmax(180px, 0.7fr) minmax(220px, 1fr) auto;
  align-items: center;
  gap: 8px;
}

.library-select {
  width: 100%;
}

.selection-summary {
  color: var(--gray-600);
  font-size: 13px;
  white-space: nowrap;
}

.unit-title {
  min-width: 0;

  strong,
  span {
    display: block;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  strong {
    color: var(--gray-900);
    font-weight: 500;
  }

  span {
    margin-top: 2px;
    color: var(--gray-500);
    font-size: 12px;
  }
}

.selector-empty {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  min-height: 120px;
  color: var(--gray-500);
}

@media (max-width: 700px) {
  .selector-toolbar {
    grid-template-columns: 1fr;
  }
}
</style>
