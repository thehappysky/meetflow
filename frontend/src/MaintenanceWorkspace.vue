<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount } from "vue";
import { api, post } from "./api";
type Row = Record<string, any>;
const props = defineProps<{ user: Row }>();
const kind = ref(props.user.role === "sales" ? "personal" : "center"),
  rows = ref<Row[]>([]),
  busy = ref(false),
  error = ref(""),
  notice = ref(""),
  restoring = ref<Row | null>(null),
  reason = ref("");
let dead = false;
async function perform(fn: () => Promise<void>) {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  notice.value = "";
  try {
    await fn();
  } catch (e) {
    error.value = e instanceof Error ? e.message : "操作失败";
  } finally {
    busy.value = false;
  }
}
async function load() {
  const scope = kind.value;
  const result = await api(`/workspace/maintenance/trash?kind=${scope}`);
  if (!dead && scope === kind.value) rows.value = result;
}
async function restore() {
  await perform(async () => {
    await post(
      `/workspace/maintenance/${kind.value}/${restoring.value!.id}/restore`,
      { version: restoring.value!.version, reason: reason.value },
    );
    restoring.value = null;
    await load();
    notice.value = "资料已恢复，原有来源、历史与对应权限保留。";
  });
}
function time(value: string) {
  return value
    ? new Date(value).toLocaleString("zh-CN", {
        timeZone: "Asia/Shanghai",
        hour12: false,
      })
    : "历史记录未存时间";
}
onMounted(() => perform(load));
onBeforeUnmount(() => (dead = true));
</script>
<template>
  <div class="mt">
    <h1>回收站与恢复</h1>
    <p>
      这里的移除可恢复，不会清除原始文件。个人副本与中心证据分别管理；当前未设置自动永久清除。
    </p>
    <p v-if="error" role="alert" class="wk-error">{{ error }}</p>
    <p v-if="notice" role="status" class="wk-note">{{ notice }}</p>
    <div class="mt-controls">
      <label v-if="user.role === 'sales'"
        >资料范围<select
          v-model="kind"
          :disabled="busy"
          @change="
            restoring = null;
            perform(load);
          "
        >
          <option value="personal">我的个人回收站</option>
          <option value="center">本人被撤下的中心资料</option>
        </select></label
      ><button @click="perform(load)" :disabled="busy">刷新回收站</button>
    </div>
    <p v-if="kind === 'center'">
      中心资料仅负责团队主管可恢复，销售和老板可查看自己权限内的撤下信息。
    </p>
    <p v-if="!rows.length">当前范围没有已移除资料。</p>
    <article v-for="r in rows" :key="r.id">
      <h2>{{ r.metadata.title }}</h2>
      <p>
        {{ r.metadata.customer || "客户待确认" }} · 实际会议日期
        {{ r.metadata.meeting_date || "待确认" }} · 来源 v{{ r.version }}
      </p>
      <p>移除时间 {{ time(r.removed_at) }} · 说明 {{ r.reason }}</p>
      <button
        v-if="r.can_restore"
        @click="
          restoring = r;
          reason = '';
        "
        :disabled="busy"
      >
        恢复资料
      </button>
      <form v-if="restoring?.id === r.id" @submit.prevent="restore">
        <label
          >恢复原因<input
            v-model="reason"
            minlength="2"
            maxlength="1000"
            required /></label
        ><button :disabled="busy" class="wk-primary">确认恢复</button
        ><button type="button" @click="restoring = null">取消</button>
      </form>
    </article>
  </div>
</template>
<style scoped>
.mt article {
  background: white;
  border: 1px solid #dce3dc;
  padding: 22px;
  border-radius: 8px;
  margin: 16px 0;
  overflow-wrap: anywhere;
}
.mt-controls {
  display: flex;
  align-items: end;
  gap: 16px;
  flex-wrap: wrap;
}
.mt h2 {
  font-size: 18px;
}
.mt label {
  display: block;
}
.mt input,
.mt select {
  display: block;
  padding: 9px;
  border: 1px solid #d6dfd6;
  border-radius: 6px;
  font: inherit;
  max-width: 100%;
  margin: 8px 0;
}
.mt form {
  margin-top: 20px;
}
.mt button {
  margin-right: 10px;
}
</style>
