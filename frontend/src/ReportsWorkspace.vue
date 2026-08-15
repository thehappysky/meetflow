<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from "vue";
import { api, post, downloadFile } from "./api";
type Row = Record<string, any>;
const rows = ref<Row[]>([]),
  candidates = ref<Row[]>([]),
  selected = ref<string[]>([]),
  report = ref<Row | null>(null);
const template = ref("weekly"),
  start = ref(""),
  end = ref(""),
  busy = ref(false),
  error = ref(""),
  notice = ref("");
const accepted = ref(false),
  unknownAccepted = ref(false),
  title = ref(""),
  plan = ref(""),
  entries = ref<Row[]>([]);
const sourcePicker = ref(true),
  loadedPeriod = ref("");
let dead = false;
function today() {
  return new Intl.DateTimeFormat("sv-SE", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}
end.value = today();
const day = new Date(end.value + "T12:00:00+08:00");
day.setDate(day.getDate() - 6);
start.value = new Intl.DateTimeFormat("sv-SE", {
  timeZone: "Asia/Shanghai",
}).format(day);
const unknown = computed(() => entries.value.filter((e) => !e.meeting_date));
const confirmed = computed(() => Boolean(report.value?.confirmed_at));
function time(value: string) {
  return new Date(value).toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour12: false,
  });
}
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
async function loadRows() {
  const result = await api("/workspace/reports");
  if (!dead) rows.value = result;
}
async function loadSources() {
  const period = start.value + ":" + end.value;
  const result = await api(
    `/workspace/reports/sources?start=${start.value}&end=${end.value}`,
  );
  if (dead || period !== start.value + ":" + end.value) return;
  candidates.value = result;
  loadedPeriod.value = period;
  selected.value = (
    template.value === "brief" ? result.slice(0, 1) : result
  ).map((r: Row) => r.key);
}
async function open(key: string) {
  const result = await api(`/workspace/reports/${key}`);
  if (dead) return;
  report.value = result;
  entries.value = structuredClone(result.body.entries);
  title.value = result.body.title;
  plan.value = result.body.plan || "";
  accepted.value = false;
  unknownAccepted.value = false;
  sourcePicker.value = false;
}
let attempt: { signature: string; key: string } | null = null;
async function generate(parent: string | null = null) {
  await perform(async () => {
    if (loadedPeriod.value !== start.value + ":" + end.value)
      throw new Error("期间已改变，请先更新会谈列表。");
    const sources = candidates.value
      .filter((c) => selected.value.includes(c.key))
      .map((c) => ({ id: c.id, kind: c.kind, revision: c.revision }));
    const body = {
      template: template.value,
      start: start.value,
      end: end.value,
      sources,
      parent_id: parent,
    };
    const signature = JSON.stringify(body);
    if (attempt?.signature !== signature)
      attempt = { signature, key: crypto.randomUUID() };
    const result = await post("/workspace/reports", {
      ...body,
      request_key: attempt!.key,
    });
    await open(result.id);
    await loadRows();
    notice.value = "已从现有会谈和跟进填好草稿，请核对后确认。";
    attempt = null;
  });
}
async function regenerate() {
  const previous = report.value!;
  await perform(async () => {
    start.value = previous.body.start;
    end.value = previous.body.end;
    template.value = previous.body.template;
    await loadSources();
    const keys = new Set(previous.body.entries.map((e: Row) => e.key));
    selected.value = candidates.value
      .filter((c) => keys.has(c.key))
      .map((c) => c.key);
    sourcePicker.value = true;
    notice.value = "已更新当前可用来源，请核对选择后生成。旧报告继续保留。";
  });
}
async function confirmReport() {
  await perform(async () => {
    await post(`/workspace/reports/${report.value!.id}/confirm`, {
      version: report.value!.version,
      title: title.value,
      plan: plan.value,
      accept_unknown_dates: unknownAccepted.value,
      entries: entries.value.map((e) => ({
        key: e.key,
        meeting_date: e.meeting_date,
        summary: e.summary,
        next_steps: e.next_steps,
        risks: e.risks,
        note: e.note || "",
      })),
    });
    await open(report.value!.id);
    await loadRows();
    notice.value = "已确认并固定此版本，可以导出 Word 或 PDF；不会自动上报。";
  });
}
async function saveDraft() {
  await perform(async () => {
    const result = await api(`/workspace/reports/${report.value!.id}/draft`, {
      method: "PATCH",
      body: JSON.stringify({
        version: report.value!.version,
        title: title.value,
        plan: plan.value,
        accept_unknown_dates: false,
        entries: entries.value.map((e) => ({
          key: e.key,
          meeting_date: e.meeting_date,
          summary: e.summary,
          next_steps: e.next_steps,
          risks: e.risks,
          note: e.note || "",
        })),
      }),
    });
    report.value!.version = result.version;
    await loadRows();
    notice.value = "核对进度已保存，稍后可以继续；尚未确认，也不能导出。";
  });
}
async function exportReport(format: string) {
  await perform(async () => {
    await downloadFile(
      `/workspace/reports/${report.value!.id}/export/${format}`,
      `${report.value!.body.template}-${report.value!.id}.${format}`,
    );
    notice.value = "文件已生成并交给浏览器下载。";
  });
}
onMounted(() =>
  perform(async () => {
    await Promise.all([loadRows(), loadSources()]);
  }),
);
onBeforeUnmount(() => {
  dead = true;
});
</script>

<template>
  <div class="rp">
    <div class="rp-header">
      <div>
        <p class="rp-eyebrow">不用再从会谈里重新抄写</p>
        <h1>我的简报与周报</h1>
        <p>
          自动填入已有内容，集中核对后导出。个人报告仅自己可见，生成和导出不会自动上报。
        </p>
      </div>
      <button
        @click="
          sourcePicker = true;
          report = null;
        "
        :disabled="busy"
      >
        新建报告
      </button>
    </div>
    <p v-if="error" role="alert" class="wk-error">{{ error }}</p>
    <p v-if="notice" role="status" class="wk-note">{{ notice }}</p>
    <div class="rp-grid">
      <aside class="rp-history">
        <h2>已保存报告</h2>
        <p v-if="!rows.length">尚无报告，从右侧选期间即可生成。</p>
        <button
          v-for="r in rows"
          :key="r.id"
          @click="perform(() => open(r.id))"
          :disabled="busy"
          :class="{ active: report?.id === r.id }"
        >
          <strong>{{ r.body.title }}</strong
          ><small>{{ r.body.start }} 至 {{ r.body.end }}</small
          ><small
            >{{ r.confirmed_at ? "已确认" : "待核对草稿" }} ·
            {{ time(r.created_at) }}</small
          >
        </button>
      </aside>
      <section class="rp-main">
        <section v-if="sourcePicker" class="rp-card">
          <h2>1 选择报告范围</h2>
          <div class="rp-fields">
            <label
              >报告模板<select
                v-model="template"
                @change="
                  template === 'brief' && (selected = selected.slice(0, 1))
                "
              >
                <option value="weekly">销售周报 · 多场会谈</option>
                <option value="brief">会谈简报 · 一场会谈</option>
              </select></label
            >
            <label>开始日期<input v-model="start" type="date" /></label
            ><label>结束日期<input v-model="end" type="date" /></label
            ><button
              @click="perform(loadSources)"
              :disabled="busy || !start || !end"
            >
              更新会谈列表
            </button>
          </div>
          <p>
            已知日期只纳入期间内的会谈；未知日期先保留，下一步统一补充或单列。上传日期不参与期间归类。
          </p>
          <p v-if="loadedPeriod !== start + ':' + end" class="wk-note">
            期间已改变，请更新列表后生成。
          </p>
          <div class="rp-source" v-for="c in candidates" :key="c.key">
            <label
              ><input type="checkbox" v-model="selected" :value="c.key" />{{
                c.metadata.title
              }}</label
            ><small
              >{{ c.metadata.customer || "客户待确认" }} · 实际日期
              {{ c.metadata.meeting_date || "时间待确认" }} ·
              {{ c.kind === "personal" ? "个人资料" : "本人已上报资料" }} v{{
                c.revision
              }}</small
            >
          </div>
          <p v-if="!candidates.length">
            此范围暂无已整理的会谈，可先上传录音，或调整日期。
          </p>
          <p v-if="selected.length > 50" class="wk-note">
            一份报告最多纳入 50 场，请取消部分会谈或缩短期间。
          </p>
          <button
            class="wk-primary"
            :disabled="
              busy ||
              !selected.length ||
              selected.length > 50 ||
              loadedPeriod !== start + ':' + end ||
              (template === 'brief' && selected.length !== 1)
            "
            @click="
              generate(
                report?.confirmed_at ? report.id : report?.parent_id || null,
              )
            "
          >
            填好{{ template === "weekly" ? "周报" : "简报" }}草稿（{{
              selected.length
            }}
            场）
          </button>
        </section>
        <section v-if="report && !sourcePicker" class="rp-card">
          <div class="rp-header">
            <h2>{{ confirmed ? "已确认的固定版本" : "2 集中核对报告" }}</h2>
            <button @click="regenerate" :disabled="busy">
              根据最新资料生成{{ confirmed ? "后续版本" : "新草稿" }}
            </button>
          </div>
          <p v-if="report.stale_sources.length" class="wk-note">
            {{ report.stale_sources.length }} 场来源或跟进已改变／移除。{{
              confirmed
                ? "此版本仍为历史快照，可另生成新版。"
                : "请重新生成草稿后再确认。"
            }}
          </p>
          <p v-if="report.parent_id">
            后续版本来源：{{ report.parent_id }}，旧报告仍保留。
          </p>
          <div v-if="confirmed" class="wk-note">
            已于 {{ time(report.confirmed_at) }} 确认 · 内容编号
            {{ report.body.content_id.slice(0, 16) }}
            <div class="rp-buttons">
              <button
                class="wk-primary"
                @click="exportReport('docx')"
                :disabled="busy"
              >
                导出 Word</button
              ><button @click="exportReport('pdf')" :disabled="busy">
                导出 PDF
              </button>
            </div>
          </div>
          <p v-if="entries.some((e) => e.mode === 'demo')" class="wk-note">
            包含规则演示提取内容，请核对原文。人工修改及日期补充单独留痕，不冒充客户确认。
          </p>
          <label
            >报告名称<input
              v-model="title"
              :readonly="confirmed"
              maxlength="200"
          /></label>
          <p>
            报告期间 {{ report.body.start }} 至 {{ report.body.end }} ·
            {{ entries.length }} 场资料
          </p>
          <div
            class="rp-date-check"
            v-if="!confirmed && entries.some((e) => !e.mapped.meeting_date)"
          >
            <h3>先集中补会议日期</h3>
            <p>
              这里只补本报告，不直接修改个人会谈或中心证据。补充日期应在报告期间内；跨期请重新选范围。
            </p>
            <label
              v-for="e in entries.filter((e) => !e.mapped.meeting_date)"
              :key="e.key"
              >{{ e.title
              }}<input
                v-model="e.meeting_date"
                type="date"
                :min="report.body.start"
                :max="report.body.end"
            /></label>
          </div>
          <article class="rp-entry" v-for="e in entries" :key="e.key">
            <h3>{{ e.customer || "客户待确认" }}</h3>
            <p>
              {{ e.title }} · {{ e.meeting_date || "时间待确认，单列展示" }} ·
              来源 v{{ e.revision }}
            </p>
            <details :open="!confirmed">
              <summary>
                {{ confirmed ? "展开本场报告内容" : "核对本场填入内容" }}
              </summary>
              <label
                >会谈摘要<textarea
                  v-model="e.summary"
                  rows="4"
                  :readonly="confirmed"
                  maxlength="12000"
                /></label
              ><label
                >后续安排与反馈<textarea
                  v-model="e.next_steps"
                  rows="4"
                  :readonly="confirmed"
                  maxlength="12000"
                /></label
              ><label
                >风险与待确认<textarea
                  v-model="e.risks"
                  rows="3"
                  :readonly="confirmed"
                  maxlength="12000"
                />
              </label>
              <details v-if="e.supplements?.length">
                <summary>
                  已带入
                  {{ e.supplements.length }} 条会谈本人补充（未经客户确认）
                </summary>
                <p>
                  将随本报告导出。如需更正，请在会谈中修改补充，再生成新草稿。
                </p>
                <p v-for="(item, index) in e.supplements" :key="index">
                  [{{ item.context }}] {{ item.question }}：{{ item.answer }}
                </p>
              </details>
              <label
                >报告中另外补充<textarea
                  v-model="e.note"
                  rows="2"
                  :readonly="confirmed"
                  maxlength="3000"
                  placeholder="可空，例如需要主管提供的支持；不会自动当作客户承诺。"
                />
              </label>
              <details>
                <summary>核对原文依据</summary>
                <p v-for="(span, key) in e.evidence" :key="key">
                  [{{ key }}] {{ span.text }}
                </p>
              </details>
            </details>
          </article>
          <label
            >下阶段计划与需协助事项<textarea
              v-model="plan"
              rows="3"
              :readonly="confirmed"
              maxlength="6000"
            />
          </label>
          <template v-if="!confirmed"
            ><p>
              确认会固定当前内容。修改后的字段会标记为销售核对修改，来源保留。中途离开前可保存核对进度，稍后继续。
            </p>
            <button
              @click="saveDraft"
              :disabled="busy || report.stale_sources.length > 0"
            >
              保存核对进度
            </button>
            <label v-if="unknown.length" class="rp-check"
              ><input type="checkbox" v-model="unknownAccepted" />{{
                unknown.length
              }}
              场日期仍未知，同意单列为“时间待确认”，不计入期间已发生数量。</label
            ><label class="rp-check"
              ><input
                type="checkbox"
                v-model="accepted"
              />我已核对内容，确认生成个人报告。</label
            ><button
              class="wk-primary"
              @click="confirmReport"
              :disabled="
                busy ||
                !accepted ||
                (unknown.length > 0 && !unknownAccepted) ||
                report.stale_sources.length > 0
              "
            >
              确认内容并准备导出
            </button></template
          >
        </section>
      </section>
    </div>
  </div>
</template>

<style scoped>
.rp h1 {
  margin: 8px 0 14px;
}
.rp-eyebrow,
.rp small {
  color: #60746a;
}
.rp-header {
  display: flex;
  justify-content: space-between;
  align-items: start;
  gap: 20px;
  flex-wrap: wrap;
}
.rp-grid {
  display: grid;
  grid-template-columns: 260px minmax(0, 1fr);
  gap: 24px;
  margin-top: 24px;
}
.rp-history button {
  display: block;
  text-align: left;
  width: 100%;
  margin: 10px 0;
  white-space: normal;
}
.rp-history small {
  display: block;
  margin-top: 8px;
  font-weight: normal;
}
.rp-history .active {
  border-color: #45674e;
  background: #e7eee4;
}
.rp-card {
  background: white;
  padding: 24px;
  border: 1px solid #dce3dc;
  border-radius: 10px;
  margin-bottom: 20px;
  min-width: 0;
  overflow-wrap: anywhere;
}
.rp-fields {
  display: flex;
  align-items: end;
  flex-wrap: wrap;
  gap: 16px;
}
.rp-fields label {
  flex: 1;
  min-width: 150px;
}
.rp-source {
  border-bottom: 1px solid #e4e9e1;
  padding: 12px 0;
}
.rp-source label,
.rp-check {
  display: flex;
  align-items: center;
  gap: 10px;
}
.rp-source small {
  display: block;
  margin: 4px 0 8px 25px;
}
.rp-source input,
.rp-check input {
  width: auto;
  flex: none;
}
.rp-buttons {
  display: flex;
  gap: 12px;
  margin-top: 15px;
}
.rp-entry {
  padding: 16px 0;
  border-top: 1px solid #e3e9df;
  min-width: 0;
}
.rp-entry textarea {
  white-space: pre-wrap;
}
.rp-date-check {
  background: #f4f6ed;
  padding: 18px;
  border-radius: 7px;
  margin: 20px 0;
}
.rp-date-check label {
  max-width: 500px;
}
.rp-check {
  margin: 16px 0;
}
.rp-main {
  min-width: 0;
}
@media (max-width: 900px) {
  .rp-grid {
    grid-template-columns: minmax(0, 1fr);
  }
  .rp-history {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
  }
  .rp-history h2 {
    width: 100%;
  }
  .rp-history button {
    width: auto;
    max-width: 100%;
  }
  .rp-card {
    padding: 18px;
  }
}
</style>
