<script setup lang="ts">
defineProps<{
  receivedLabel: string;
  owners?: { id: string; label: string }[];
  teams?: { id: string; label: string }[];
  states?: Record<string, string>;
}>();
const query = defineModel<string>("query"),
  mode = defineModel<string>("mode"),
  from = defineModel<string>("from"),
  to = defineModel<string>("to"),
  owner = defineModel<string>("owner"),
  team = defineModel<string>("team"),
  state = defineModel<string>("state");
const emit = defineEmits<{ clear: [] }>();
</script>
<template>
  <details class="rf">
    <summary>查找会谈与筛选日期</summary>
    <div class="rf-fields">
      <label
        >搜索标题或客户<input v-model="query" placeholder="输入关键词" /></label
      ><label v-if="owners?.length"
        >负责销售<select v-model="owner">
          <option value="">全部可见销售</option>
          <option v-for="o in owners" :value="o.id" :key="o.id">
            {{ o.label }}
          </option>
        </select></label
      ><label v-if="teams?.length"
        >团队<select v-model="team">
          <option value="">全部可见团队</option>
          <option v-for="t in teams" :value="t.id" :key="t.id">
            {{ t.label }}
          </option>
        </select></label
      ><label v-if="states"
        >整理状态<select v-model="state">
          <option value="">全部状态</option>
          <option v-for="(label, id) in states" :value="id" :key="id">
            {{ label }}
          </option>
        </select></label
      ><label
        >日期依据<select v-model="mode">
          <option value="meeting">实际会谈日期</option>
          <option value="received">{{ receivedLabel }}日期（北京时间）</option>
          <option value="unknown">仅会议日期待确认</option>
        </select></label
      ><template v-if="mode !== 'unknown'"
        ><label>从<input v-model="from" type="date" /></label
        ><label>至<input v-model="to" type="date" /></label></template
      ><button @click="emit('clear')">清除筛选</button>
    </div>
    <p v-if="mode === 'meeting' && (from || to)">
      此时不显示实际日期未知的资料，可切换“仅会议日期待确认”单独找回。
    </p>
  </details>
</template>
<style scoped>
.rf {
  background: #fff;
  padding: 14px 18px;
  border: 1px solid #dce3dc;
  border-radius: 8px;
  margin: 18px 0;
}
.rf summary {
  cursor: pointer;
}
.rf-fields {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: end;
  margin-top: 12px;
}
.rf label {
  display: block;
  min-width: 130px;
}
.rf input,
.rf select {
  display: block;
  max-width: 100%;
  padding: 8px;
  border: 1px solid #d6dfd6;
  border-radius: 6px;
  font: inherit;
  margin-top: 6px;
}
.rf p {
  color: #60746a;
}
</style>
