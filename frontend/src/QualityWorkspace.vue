<script setup lang="ts">
import {
  computed,
  onMounted,
  onBeforeUnmount,
  reactive,
  ref,
  nextTick,
} from "vue";
import { api, post, NETWORK_ERROR } from "./api";
type Row = Record<string, any>;
const props = defineProps<{
  user: Row;
  source?: { kind: string; id: string } | null;
}>();
const emit = defineEmits<{ meeting: [kind: string, id: string] }>();
const rows = ref<Row[]>([]),
  groups = ref<Row[]>([]),
  detail = ref<Row | null>(null);
const busy = ref(false),
  error = ref(""),
  notice = ref(""),
  search = ref("");
const expanded = ref<string[]>([]),
  evidenceOpen = ref(false),
  allTranscript = ref(false),
  activeRule = ref("");
const contextOpen = ref(false),
  reviewOpen = ref(false),
  stage = ref("通用复盘"),
  complete = ref(false),
  reason = ref(""),
  note = ref("");
const speakers = reactive<Row>({}),
  reviewEdits = reactive<Row>({}),
  selected = ref<string[]>([]),
  highlighted = ref(""),
  historical = ref("");
const audioUrl = ref(""),
  audio = ref<HTMLAudioElement | null>(null),
  evidencePanel = ref<HTMLElement | null>(null);
let timer: number | undefined,
  sequence = 0,
  dead = false,
  polling = false;
const labels: Row = {
  met: "达标",
  partial: "部分达标",
  unmet: "未达标",
  unknown: "无法判断",
  not_applicable: "不适用",
};
const statuses: Row = {
  needs_speakers: "待核对说话人",
  queued: "等待质检",
  running: "正在质检",
  waiting_review: "自动结果 · 待复核",
  reviewed: "主管已复核",
  failed: "处理失败",
  cancelled: "已停止",
};
const stages = ["通用复盘", "需求沟通", "产品演示", "方案报价", "决策签约"];
const speakerNames: Row = {
  sales: "销售",
  customer: "客户",
  unknown: "身份待确认",
};
const visible = computed(() =>
  rows.value.filter((r) =>
    [r.metadata.title, r.metadata.customer, r.owner]
      .join(" ")
      .includes(search.value),
  ),
);
const decisions = computed<Row[]>(() => detail.value?.decisions || []);
const canManage = computed(
  () =>
    props.user.role === "manager" &&
    detail.value &&
    !detail.value.stale &&
    !detail.value.historical,
);
const evidenceSpans = computed<Row[]>(() => {
  const spans = detail.value?.spans || [];
  if (allTranscript.value || contextOpen.value || reviewOpen.value)
    return spans;
  const refs =
    decisions.value.find((d) => d.rule_id === activeRule.value)?.evidence || [];
  return spans.filter((s: Row) => refs.includes(s.id));
});
function title(d: Row) {
  return (
    d.title ||
    detail.value?.rules?.find((r: Row) => r.id === d.rule_id)?.title ||
    d.rule_id
  );
}
function shortReason(d: Row) {
  if (detail.value?.mode === "demo" && !d.review_reason)
    return d.status === "partial"
      ? "演示仅字面命中，原因待核对"
      : "依据不足，暂不评价";
  return (
    d.reason_title ||
    `${title(d)}：${{ met: "满足要求", partial: "部分满足要求", unmet: "未满足要求", unknown: "依据不足，待确认", not_applicable: "本场不适用" }[d.status as string] || "待核对"}`
  );
}
function displayStage(value: string) {
  return value === "通用复盘" ? "通用质检（阶段待确认）" : value;
}
function deductionLabel(d: Row) {
  const possible = detail.value?.metrics?.evaluated_weight;
  if (d.deduction == null || !possible) return "不计分";
  if (!d.deduction) return "未扣分";
  return `折算扣 ${Math.round((1000 * d.deduction) / possible) / 10} 分`;
}
function clearAudio() {
  if (audioUrl.value) URL.revokeObjectURL(audioUrl.value);
  audioUrl.value = "";
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
async function readDetail(id: string, analysis = "", view = "summary") {
  const token = ++sequence;
  const result = await api(
    `/workspace/quality/cases/${id}?view=${view}${analysis ? "&analysis_id=" + encodeURIComponent(analysis) : ""}`,
  );
  if (dead || sequence !== token) return;
  detail.value = result;
}
async function ensureView(view: string) {
  const rank: Row = { summary: 0, details: 1, full: 2 };
  if (rank[detail.value?.view || "summary"] >= rank[view]) return;
  await readDetail(detail.value!.id, historical.value, view);
}
async function choose(row: Row, analysis = "") {
  clearAudio();
  expanded.value = [];
  evidenceOpen.value =
    allTranscript.value =
    contextOpen.value =
    reviewOpen.value =
      false;
  selected.value = [];
  highlighted.value = "";
  historical.value = analysis;
  await readDetail(row.id, analysis);
}
async function refresh(initial = false) {
  const result = await api("/workspace/quality/cases?scope=center");
  if (dead) return;
  rows.value = result.cases;
  groups.value = result.summary;
  if (error.value === NETWORK_ERROR) error.value = "";
  if ((initial || !detail.value) && props.source) {
    const row = rows.value.find(
      (r) => r.source_id === props.source!.id && r.source_kind === "center",
    );
    if (row) await choose(row);
  } else if (detail.value)
    await readDetail(detail.value.id, historical.value, detail.value.view);
}
async function toggleReason(d: Row) {
  if (expanded.value.includes(d.rule_id)) {
    expanded.value = expanded.value.filter((id) => id !== d.rule_id);
    if (
      activeRule.value === d.rule_id &&
      !reviewOpen.value &&
      !contextOpen.value
    ) {
      evidenceOpen.value = false;
      clearAudio();
    }
    return;
  }
  await perform(async () => {
    await ensureView("details");
    expanded.value = [...expanded.value, d.rule_id];
  });
}
async function showEvidence(d: Row) {
  await perform(async () => {
    await ensureView("full");
    activeRule.value = d.rule_id;
    evidenceOpen.value = true;
    allTranscript.value = false;
    highlighted.value = "";
    await nextTick();
    evidencePanel.value?.focus({ preventScroll: true });
    evidencePanel.value?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
}
async function loadAudio() {
  if (audioUrl.value) return;
  const id = detail.value!.id,
    token = sequence;
  const response = await fetch(
    `/api/workspace/center/${detail.value!.source_id}/audio`,
    {
      headers: {
        Authorization: `Bearer ${sessionStorage.getItem("meetflow-token")}`,
      },
    },
  );
  if (!response.ok) throw new Error("录音不可用或无权访问");
  const blob = await response.blob();
  if (dead || sequence !== token || detail.value?.id !== id) return;
  audioUrl.value = URL.createObjectURL(blob);
}
async function seek(id: string) {
  highlighted.value = id;
  await nextTick();
  document
    .getElementById(`quality-span-${id}`)
    ?.scrollIntoView({ behavior: "smooth", block: "center" });
  const span = detail.value?.spans?.find((s: Row) => s.id === id);
  if (!detail.value?.has_audio || span?.start == null) {
    notice.value = "已定位文字；本段没有已对齐的音频时间点。";
    return;
  }
  await perform(async () => {
    await loadAudio();
    const set = () => {
      if (audio.value) audio.value.currentTime = span.start;
    };
    if (audio.value?.readyState) set();
    else audio.value?.addEventListener("loadedmetadata", set, { once: true });
    notice.value = `已定位到 ${Math.floor(span.start)} 秒，点播放回听。`;
  });
}
async function editContext() {
  await perform(async () => {
    await ensureView("full");
    stage.value = detail.value!.stage;
    complete.value = detail.value!.complete;
    reason.value = "";
    selected.value = [];
    for (const s of detail.value!.spans) speakers[s.id] = s.speaker;
    contextOpen.value = true;
    reviewOpen.value = false;
    evidenceOpen.value = allTranscript.value = true;
    await nextTick();
    evidencePanel.value?.focus({ preventScroll: true });
    evidencePanel.value?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
}
function assign(role: string) {
  for (const id of selected.value) speakers[id] = role;
  selected.value = [];
}
async function saveContext() {
  await perform(async () => {
    await post(`/workspace/quality/cases/${detail.value!.id}/context`, {
      version: detail.value!.version,
      stage: stage.value,
      complete: complete.value,
      speakers: Object.fromEntries(
        detail.value!.spans.map((s: Row) => [s.id, speakers[s.id]]),
      ),
      reason: reason.value,
    });
    contextOpen.value = false;
    evidenceOpen.value = false;
    historical.value = "";
    await readDetail(detail.value!.id);
    await refresh();
    notice.value = "背景已更新，旧分析保留。";
  });
}
async function startReview() {
  await perform(async () => {
    await ensureView("full");
    reviewOpen.value = true;
    contextOpen.value = false;
    selected.value = [];
    note.value = "";
    for (const d of decisions.value)
      reviewEdits[d.rule_id] = {
        status: d.status,
        reason: "",
        evidence: [...d.evidence],
      };
    expanded.value = decisions.value.map((d) => d.rule_id);
  });
}
async function saveReview() {
  await perform(async () => {
    const edits = decisions.value
      .filter(
        (d) =>
          reviewEdits[d.rule_id].status !== d.status ||
          reviewEdits[d.rule_id].reason ||
          JSON.stringify(reviewEdits[d.rule_id].evidence) !==
            JSON.stringify(d.evidence),
      )
      .map((d) => ({ rule_id: d.rule_id, ...reviewEdits[d.rule_id] }));
    await post(`/workspace/quality/cases/${detail.value!.id}/review`, {
      version: detail.value!.version,
      report_hash: detail.value!.report_hash,
      decisions: edits,
      note: note.value,
    });
    reviewOpen.value = false;
    await refresh();
    notice.value = "主管复核已保存，原判断与版本保留。";
  });
}
async function retry() {
  await perform(async () => {
    await post(`/workspace/quality/cases/${detail.value!.id}/retry`);
    await refresh();
  });
}
async function loadHistory() {
  await perform(async () => ensureView("full"));
}
onMounted(async () => {
  if (!["manager", "boss"].includes(props.user.role)) {
    error.value = "此工作区仅供主管和老板使用";
    return;
  }
  await perform(() => refresh(true));
  timer = window.setInterval(async () => {
    if (busy.value || polling || dead || contextOpen.value || reviewOpen.value)
      return;
    polling = true;
    try {
      await refresh();
    } catch (e) {
      error.value = e instanceof Error ? e.message : "读取失败";
    } finally {
      polling = false;
    }
  }, 5000);
});
onBeforeUnmount(() => {
  dead = true;
  sequence++;
  window.clearInterval(timer);
  clearAudio();
});
</script>

<template>
  <section class="quality">
    <header class="q-heading">
      <div>
        <p>已上报资料的管理质检</p>
        <h1>{{ user.role === "boss" ? "公司质检概览" : "团队销售质检" }}</h1>
      </div>
      <button @click="perform(() => refresh())" :disabled="busy">
        刷新质检
      </button>
    </header>
    <p v-if="error" role="alert" class="q-error">{{ error }}</p>
    <p v-if="notice" role="status" class="q-notice">{{ notice }}</p>
    <div class="q-stats">
      <span
        >当前范围 <b>{{ rows.length }}</b> 场</span
      ><span
        >主管已复核
        <b>{{ rows.filter((r) => r.status === "reviewed").length }}</b></span
      ><span
        >待核对身份
        <b>{{
          rows.filter((r) => r.status === "needs_speakers").length
        }}</b></span
      >
    </div>
    <p class="q-muted">
      只评价已上报的授权资料。先看分数与扣分概览，展开条目看具体原因，再查看原文或回听依据。
    </p>
    <p v-if="rows.some((r) => r.mode === 'demo')" class="q-notice">
      当前含规则演示，未调用大模型；演示分数不用于真实人员评价。
    </p>
    <details v-if="groups.length">
      <summary>展开分组统计</summary>
      <p v-for="g in groups" :key="`${g.policy.id}-${g.mode}-${g.reviewed}`">
        {{ g.policy.name }} v{{ g.policy.version }} ·
        {{ g.mode === "demo" ? "规则演示" : "模型分析" }} ·
        {{ g.reviewed ? "已复核" : "待复核" }} · {{ g.samples }} 场 · 可评项均分
        {{ g.average_score ?? "无法计算" }}
      </p>
    </details>
    <div class="q-workbench" :class="{ 'with-evidence': evidenceOpen }">
      <aside class="q-rail">
        <label
          >查找会谈<input v-model="search" placeholder="客户、会谈、销售"
        /></label>
        <p v-if="!visible.length">暂无已上报质检。</p>
        <button
          v-for="r in visible"
          :key="r.id"
          class="q-call"
          :class="{ selected: detail?.id === r.id }"
          @click="perform(() => choose(r))"
          :disabled="busy"
        >
          <strong>{{ r.metadata.title }}</strong
          ><span>{{ r.owner }} · {{ r.metadata.customer || "客户待归属" }}</span
          ><span>{{ statuses[r.status] }}</span
          ><b>{{
            r.metrics?.score == null ? "尚无可评分数" : `${r.metrics.score} 分`
          }}</b>
        </button>
      </aside>
      <section v-if="detail" class="q-findings">
        <div class="q-heading">
          <div>
            <h2>{{ detail.metadata.title }}</h2>
            <p>
              {{ detail.owner }} · {{ detail.metadata.customer }} · 来源 v{{
                detail.source_revision
              }}
            </p>
          </div>
          <button @click="emit('meeting', 'center', detail.source_id)">
            查看会谈纪要
          </button>
        </div>
        <p v-if="detail.stale || detail.historical" class="q-notice">
          正在查看旧版，只能追溯，不能覆盖当前结果。
        </p>
        <div class="q-score">
          <span>{{ statuses[detail.status] }}</span
          ><strong
            >{{ detail.metrics?.score ?? "—" }}<small> / 100</small></strong
          ><span
            >可评覆盖 {{ detail.metrics?.coverage ?? "—" }}% ·
            {{ displayStage(detail.stage) }}</span
          >
        </div>
        <p v-if="detail.error" class="q-error">{{ detail.error }}</p>
        <p v-if="detail.status === 'needs_speakers'">
          先由主管核对说话人；身份不明时不评价销售表现。
        </p>
        <div class="q-actions">
          <button
            v-if="canManage && !reviewOpen"
            @click="editContext"
            :disabled="busy || ['queued', 'running'].includes(detail.status)"
          >
            核对说话人与背景</button
          ><button
            v-if="canManage && detail.has_report && !reviewOpen"
            @click="startReview"
            :disabled="busy"
          >
            复核本场质检</button
          ><button
            v-if="canManage && detail.status === 'failed'"
            @click="retry"
            :disabled="busy"
          >
            恢复失败质检
          </button>
        </div>
        <article v-for="d in decisions" :key="d.rule_id" class="q-rule">
          <button
            class="q-summary"
            :aria-expanded="expanded.includes(d.rule_id)"
            :aria-controls="`quality-reason-${d.rule_id}`"
            @click="toggleReason(d)"
            :disabled="busy"
          >
            <span
              ><strong>{{ title(d) }}</strong
              ><small>{{ shortReason(d) }}</small></span
            ><span
              >{{ deductionLabel(d)
              }}<small
                >{{ labels[d.status] }} ·
                {{
                  expanded.includes(d.rule_id) ? "收起原因" : "展开原因"
                }}</small
              ></span
            >
          </button>
          <div
            v-if="expanded.includes(d.rule_id)"
            :id="`quality-reason-${d.rule_id}`"
            class="q-reason"
            :aria-label="`${title(d)}具体原因`"
          >
            <h3>具体原因</h3>
            <p v-if="d.points != null">
              本项原始得分 {{ d.points }} / {{ d.weight }}，原始扣分
              {{ d.deduction }}；按可评权重折算为上方百分制扣分。
            </p>
            <p>
              {{
                d.review_reason && d.review_reason !== "保留机器判断"
                  ? d.review_reason
                  : d.reason
              }}
            </p>
            <details
              v-if="d.review_reason && d.review_reason !== '保留机器判断'"
            >
              <summary>查看原自动判断说明</summary>
              <p>{{ d.reason }}</p>
              <p v-if="d.guidance">原管理辅导建议：{{ d.guidance }}</p>
            </details>
            <p v-else-if="d.guidance">管理辅导建议：{{ d.guidance }}</p>
            <details>
              <summary>查看本项培训要求与评分口径</summary>
              <p>
                {{
                  detail.rules?.find((r: Row) => r.id === d.rule_id)?.criterion
                }}
              </p>
              <p>
                适用条件：{{
                  detail.rules?.find((r: Row) => r.id === d.rule_id)
                    ?.applicability
                }}
              </p>
              <p>
                达标：{{
                  detail.rules?.find((r: Row) => r.id === d.rule_id)
                    ?.pass_anchor
                }}
              </p>
              <p>
                部分达标：{{
                  detail.rules?.find((r: Row) => r.id === d.rule_id)
                    ?.partial_anchor
                }}
              </p>
            </details>
            <button @click="showEvidence(d)" :disabled="busy">
              查看证据与回听：{{ title(d) }}
            </button>
            <section v-if="reviewOpen" class="q-edit">
              <label
                >复核判断<select
                  v-model="reviewEdits[d.rule_id].status"
                  :aria-label="`${d.rule_id}复核判断`"
                >
                  <option
                    v-for="(label, key) in labels"
                    :key="key"
                    :value="key"
                  >
                    {{ label }}
                  </option>
                </select></label
              ><label
                >改判原因<textarea
                  v-model="reviewEdits[d.rule_id].reason"
                  :aria-label="`${d.rule_id}改判原因`"
                /></label
              ><button
                @click="reviewEdits[d.rule_id].evidence = [...selected]"
                :disabled="!selected.length"
              >
                使用选中的原文依据
              </button>
              <p>
                拟采用 {{ reviewEdits[d.rule_id].evidence.join("、") || "无" }}
              </p>
            </section>
          </div>
        </article>
        <form v-if="reviewOpen" class="q-panel" @submit.prevent="saveReview">
          <label
            >主管复核说明<textarea
              v-model="note"
              :required="detail.pending_feedback > 0"
            /></label
          ><button class="q-primary" :disabled="busy">确认保存主管复核</button
          ><button type="button" @click="reviewOpen = false">取消复核</button>
        </form>
        <details class="q-panel">
          <summary>评分说明与版本记录</summary>
          <p>
            无法判断不扣分，不适用不进入分母。总分按可评权重折算至 100
            分，各条扣分使用同一折算分母，与总分对应；四舍五入可能有 0.1
            分尾差。展开原因可看原始权重分。
          </p>
          <p v-if="detail.policy">
            {{ detail.policy.name }} v{{ detail.policy.version }}
          </p>
          <button @click="loadHistory" :disabled="busy">
            加载历次分析和主管复核</button
          ><template v-if="detail.view === 'full'"
            ><details v-if="detail.review_history?.length">
              <summary>历次主管复核</summary>
              <p v-for="r in detail.review_history" :key="r.id">
                {{ r.actor }} · {{ r.body.metrics.score ?? "无法评分" }} 分 ·
                {{ r.body.note || "保留原判断" }}
              </p>
            </details>
            <details v-if="detail.feedback?.length">
              <summary>历史遗留反馈</summary>
              <p v-for="f in detail.feedback" :key="f.id">
                {{ f.body.reason }} · {{ f.resolution || "待处理" }}
              </p>
            </details>
            <details v-if="detail.source_history?.length > 1">
              <summary>来源修订历史</summary>
              <button
                v-for="h in detail.source_history"
                :key="h.id"
                @click="perform(() => choose(h))"
              >
                来源 v{{ h.source_revision }} · {{ h.title }}
              </button>
            </details>
            <details v-if="detail.history?.length > 1">
              <summary>历次分析</summary>
              <button @click="perform(() => choose(detail!))">
                查看当前分析</button
              ><button
                v-for="(h, index) in detail.history"
                :key="h.id"
                @click="perform(() => choose(detail!, h.id))"
              >
                第 {{ Number(index) + 1 }} 次分析
              </button>
            </details></template
          >
        </details>
      </section>
      <p v-else class="q-panel">选择会谈后先查看评分和扣分概览。</p>
      <section
        v-if="evidenceOpen && detail"
        ref="evidencePanel"
        tabindex="-1"
        class="q-evidence"
        aria-label="质检证据依据"
      >
        <div class="q-heading">
          <h2>{{ contextOpen ? "核对质检背景" : "原文与音频依据" }}</h2>
          <button
            @click="
              evidenceOpen = contextOpen = false;
              clearAudio();
            "
          >
            收起证据
          </button>
        </div>
        <p v-if="!contextOpen">
          {{ allTranscript ? "完整会谈原文" : "当前条目引用的原文" }} ·
          未引用的片段不会自动当作支持依据。
        </p>
        <div v-if="detail.has_audio">
          <button v-if="!audioUrl" @click="perform(loadAudio)" :disabled="busy">
            加载本场录音</button
          ><audio v-else ref="audio" :src="audioUrl" controls />
        </div>
        <form v-if="contextOpen" @submit.prevent="saveContext">
          <p>
            实际音频尚无自动说话人分离，按已确认身份核对；信息不足保留未知。
          </p>
          <label
            >会谈阶段<select v-model="stage">
              <option v-for="s in stages" :key="s" :value="s">
                {{ displayStage(s) }}
              </option>
            </select></label
          ><label
            ><input
              type="checkbox"
              v-model="complete"
            />我确认资料完整覆盖会谈</label
          ><label>背景更正原因<textarea v-model="reason" required /></label
          ><button
            type="button"
            @click="assign('sales')"
            :disabled="!selected.length"
          >
            所选发言设为销售</button
          ><button
            type="button"
            @click="assign('customer')"
            :disabled="!selected.length"
          >
            所选发言设为客户</button
          ><button class="q-primary" :disabled="busy">
            保存背景并继续质检
          </button>
        </form>
        <button
          v-if="!allTranscript && !contextOpen"
          @click="allTranscript = true"
        >
          展开完整会谈原文
        </button>
        <p v-if="!evidenceSpans.length">
          该条目没有可引用原文，不能据此补造证据。
        </p>
        <div class="q-transcript">
          <article
            v-for="s in evidenceSpans"
            :key="s.id"
            :id="`quality-span-${s.id}`"
            :class="{ highlight: highlighted === s.id }"
          >
            <header>
              <label v-if="contextOpen || reviewOpen"
                ><input
                  type="checkbox"
                  v-model="selected"
                  :value="s.id"
                />选择此段</label
              ><select
                v-if="contextOpen"
                v-model="speakers[s.id]"
                :aria-label="`${s.id}说话人`"
              >
                <option value="sales">销售</option>
                <option value="customer">客户</option>
                <option value="unknown">身份待确认</option></select
              ><span v-else>{{ speakerNames[s.speaker] }}</span
              ><button @click="seek(s.id)">
                {{
                  s.start == null
                    ? "定位文字"
                    : `回听 ${Math.floor(s.start)} 秒`
                }}
              </button>
            </header>
            <p>{{ s.text }}</p>
          </article>
        </div>
      </section>
    </div>
  </section>
</template>
<style scoped>
.quality {
  color: #203b33;
}
.quality h1 {
  margin: 4px 0 16px;
}
.q-heading,
.q-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.q-heading p,
.q-muted {
  color: #6a7c72;
  font-size: 12px;
}
.q-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 24px;
  padding: 14px 18px;
  background: #e8eee5;
  border-radius: 9px;
}
.q-stats b {
  font-size: 22px;
}
.q-notice {
  background: #eff3e7;
  padding: 12px;
  border-radius: 8px;
  font-size: 12px;
}
.q-error {
  color: #8a3525;
  background: #f9e9e6;
  padding: 12px;
}
.q-workbench {
  display: grid;
  grid-template-columns: 230px minmax(0, 1fr);
  gap: 18px;
  align-items: start;
  margin-top: 18px;
}
.q-workbench.with-evidence {
  grid-template-columns: 200px minmax(0, 1fr) minmax(300px, 0.9fr);
}
.q-rail {
  position: sticky;
  top: 12px;
  max-height: 80vh;
  overflow: auto;
}
.q-call {
  display: flex;
  flex-direction: column;
  gap: 8px;
  text-align: left;
  width: 100%;
  margin: 10px 0;
  padding: 14px !important;
}
.q-call span {
  font-size: 12px;
  color: #657b6e;
}
.q-call.selected {
  background: #eaf1e6 !important;
  border-color: #477158 !important;
}
.q-findings,
.q-evidence,
.q-panel {
  min-width: 0;
  background: #fff;
  padding: 20px;
  border: 1px solid #dce5da;
  border-radius: 10px;
  overflow-wrap: anywhere;
}
.q-evidence h2,
.q-findings h2 {
  font-size: 18px;
}
.q-evidence {
  position: sticky;
  top: 12px;
  scroll-margin-top: 12px;
}
.q-evidence h3,
.q-findings h3 {
  font-size: 15px;
}
.q-score {
  padding: 20px;
  border-radius: 10px;
  background: #244b3d;
  color: white;
  margin: 18px 0;
  display: flex;
  gap: 15px;
  align-items: center;
  flex-wrap: wrap;
}
.q-score strong {
  font-size: 38px;
}
.q-score small {
  font-size: 13px;
}
.q-summary {
  width: 100%;
  display: flex;
  justify-content: space-between;
  gap: 18px;
  text-align: left;
  padding: 16px !important;
  background: #fafcf8 !important;
}
.q-summary small {
  display: block;
  font-size: 12px;
  margin-top: 6px;
}
.q-rule {
  margin: 14px 0;
}
.q-reason {
  padding: 18px;
  background: #f7f9f4;
  border: 1px solid #e0e7dc;
  border-top: 0;
}
.q-reason p {
  white-space: pre-wrap;
}
.q-transcript {
  max-height: 65vh;
  overflow: auto;
}
.q-transcript article {
  padding: 14px 5px;
  border-bottom: 1px solid #e3e9df;
  scroll-margin-top: 25px;
}
.q-transcript header {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
}
.q-transcript p {
  white-space: pre-wrap;
}
.q-transcript .highlight {
  background: #fff1c5;
}
.q-transcript select {
  max-width: 140px;
}
.q-evidence audio {
  width: 100%;
  margin: 14px 0;
}
.q-primary {
  background: #315c43 !important;
  color: white !important;
}
.q-edit {
  border-top: 1px solid #dce5da;
  margin-top: 18px;
  padding-top: 8px;
}
.q-panel {
  margin-top: 18px;
}
@media (max-width: 1200px) {
  .q-workbench.with-evidence {
    grid-template-columns: 220px minmax(0, 1fr);
  }
  .q-evidence {
    grid-column: 2;
  }
}
@media (max-width: 780px) {
  .q-workbench,
  .q-workbench.with-evidence {
    grid-template-columns: minmax(0, 1fr);
  }
  .q-rail {
    position: static;
    max-height: 250px;
  }
  .q-evidence {
    grid-column: 1;
  }
  .q-findings,
  .q-evidence {
    padding: 16px;
  }
}
</style>
