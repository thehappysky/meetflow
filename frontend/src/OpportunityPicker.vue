<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref, computed } from "vue";
import { api, post } from "./api";
type Row = Record<string, any>;
let dead = false;
defineProps<{ modelValue?: string | null; label?: string }>();
const emit = defineEmits<{
  "update:modelValue": [value: string | null];
  label: [value: string];
}>();
const rows = ref<Row[]>([]),
  creating = ref(false),
  busy = ref(false),
  error = ref("");
const customerName = ref(""),
  title = ref(""),
  customerId = ref("");
const customers = computed(() =>
  Array.from(
    new Map(
      rows.value.map((r) => [
        r.customer_id,
        { id: r.customer_id, name: r.customer },
      ]),
    ).values(),
  ),
);
async function load() {
  const result = await api("/workspace/business/opportunities");
  if (!dead) rows.value = result;
}
function select(event: Event) {
  const key = (event.target as HTMLSelectElement).value;
  emit("update:modelValue", key || null);
  const row = rows.value.find((r) => r.id === key);
  emit("label", row ? `${row.customer} / ${row.title}` : "");
}
async function create() {
  busy.value = true;
  error.value = "";
  try {
    const result = await post("/workspace/business/opportunities", {
      customer_id: customerId.value || null,
      customer_name: customerName.value,
      title: title.value,
    });
    await load();
    if (dead) return;
    const row = rows.value.find((r) => r.id === result.id)!;
    emit("update:modelValue", row.id);
    emit("label", `${row.customer} / ${row.title}`);
    creating.value = false;
    customerName.value = title.value = "";
  } catch (e) {
    error.value = e instanceof Error ? e.message : "创建失败";
  } finally {
    busy.value = false;
  }
}
onMounted(async () => {
  try {
    await load();
  } catch (e) {
    error.value = e instanceof Error ? e.message : "读取失败";
  }
});
onBeforeUnmount(() => {
  dead = true;
});
</script>
<template>
  <div class="op-picker">
    <label
      >{{ label || "客户／商机"
      }}<select :value="modelValue || ''" @change="select">
        <option value="">待归属，上传后也可补选</option>
        <option v-for="r in rows" :key="r.id" :value="r.id">
          {{ r.customer }} / {{ r.title }}
        </option>
      </select></label
    ><button type="button" @click="creating = !creating">新建客户／商机</button>
    <div v-if="error" role="alert">{{ error }}</div>
    <div v-if="creating" class="op-new">
      <label
        >已有客户（可选）<select v-model="customerId">
          <option value="">新建客户，不按同名自动合并</option>
          <option v-for="c in customers" :key="c.id" :value="c.id">
            {{ c.name }}
          </option>
        </select></label
      ><label v-if="!customerId"
        >客户名称<input v-model="customerName" maxlength="150" /></label
      ><label>商机／项目名称<input v-model="title" maxlength="150" /></label
      ><button
        type="button"
        @click="create"
        :disabled="
          busy || !title.trim() || (!customerId && !customerName.trim())
        "
      >
        保存并选中商机
      </button>
    </div>
  </div>
</template>
<style scoped>
.op-picker {
  margin: 12px 0;
}
.op-picker label {
  display: block;
}
.op-picker select,
.op-picker input {
  display: block;
  width: 100%;
  border: 1px solid #cad7cc;
  border-radius: 7px;
  padding: 9px;
  margin: 6px 0;
  box-sizing: border-box;
  background: white;
  font: inherit;
}
.op-picker button {
  margin: 5px 0;
  padding: 8px 12px;
  border: 1px solid #cbd8cc;
  border-radius: 7px;
  background: #fff;
  color: #244b3d;
  cursor: pointer;
}
.op-new {
  border: 1px dashed #b5c8b8;
  padding: 12px;
  margin-top: 10px;
  background: #f5f8f2;
}
</style>
