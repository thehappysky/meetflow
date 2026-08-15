<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, reactive, ref } from "vue";
import { api, post } from "./api";
type Row = Record<string, any>;
const props = defineProps<{
  user: Row;
  source?: { kind: string; id: string } | null;
}>();
const emit = defineEmits<{ meeting: [kind: string, id: string] }>();
const scope = ref(
  props.source?.kind || (props.user.role === "sales" ? "personal" : "center"),
);
const rows = ref<Row[]>([]),
  groups = ref<Row[]>([]),
  detail = ref<Row | null>(null);
const busy = ref(false),
  error = ref(""),
  notice = ref(""),
  search = ref("");
const contextOpen = ref(false),
  reviewOpen = ref(false),
  feedbackOpen = ref(false);
const stage = ref("通用复盘"),
  complete = ref(false),
  reason = ref(""),
  note = ref("");
const speakers = reactive<Row>({}),
  reviewEdits = reactive<Row>({});
const selected = ref<string[]>([]),
  highlighted = ref(""),
  audioUrl = ref("");
const audio = ref<HTMLAudioElement | null>(null),
  historical = ref("");
const feedback = reactive({ rule_id: "", reason: "" });
let timer: number | undefined,
  generation = 0,
  dead = false,
  polling = false;
const stages = ["通用复盘", "需求沟通", "产品演示", "方案报价", "决策签约"];
const labels: Row = {
  met: "达标",
  partial: "部分达标",
  unmet: "未达标",
  not_applicable: "不适用",
  unknown: "无法判断",
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
const decisions = computed<Row[]>(
  () =>
    detail.value?.review?.decisions || detail.value?.report?.decisions || [],
);
const canContext = computed(
  () =>
    detail.value &&
    !detail.value.stale &&
    !detail.value.historical &&
    (scope.value === "personal" || props.user.role === "manager"),
);
const canReview = computed(
  () =>
    props.user.role === "manager" &&
    detail.value?.source_kind === "center" &&
    detail.value?.report &&
    !detail.value.stale &&
    !detail.value.historical,
);
const canFeedback = computed(
  () =>
    props.user.role === "sales" &&
    detail.value?.source_kind === "center" &&
    detail.value?.report &&
    !detail.value.stale &&
    !detail.value.historical,
);
function clearAudio() {
  if (audioUrl.value) URL.revokeObjectURL(audioUrl.value);
  audioUrl.value = "";
}
async function perform(work: () => Promise<void>) {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  notice.value = "";
  try {
    await work();
  } catch (e) {
    error.value = e instanceof Error ? e.message : "操作失败";
  } finally {
    busy.value = false;
  }
}
async function readDetail(id: string, analysis = "") {
  const token = ++generation;
  const result = await api(
    `/workspace/quality/cases/${id}${analysis ? `?analysis_id=${encodeURIComponent(analysis)}` : ""}`,
  );
  if (dead || token !== generation) return;
  detail.value = result;
}
async function choose(row: Row, analysis = "") {
  clearAudio();
  contextOpen.value = reviewOpen.value = feedbackOpen.value = false;
  selected.value = [];
  highlighted.value = "";
  historical.value = analysis;
  await readDetail(row.id, analysis);
}
async function refresh(initial = false) {
  const requestedScope = scope.value;
  const data = await api(`/workspace/quality/cases?scope=${requestedScope}`);
  if (dead || requestedScope !== scope.value) return;
  rows.value = data.cases;
  groups.value = data.summary;
  if ((initial || !detail.value) && props.source) {
    const item = rows.value.find(
      (r) =>
        r.source_id === props.source!.id &&
        r.source_kind === props.source!.kind,
    );
    if (item) await choose(item);
  } else if (detail.value) await readDetail(detail.value.id, historical.value);
}
async function switchScope(value: string) {
  generation++;
  detail.value = null;
  clearAudio();
  scope.value = value;
  rows.value = [];
  groups.value = [];
  await perform(() => refresh());
}
function editContext() {
  stage.value = detail.value!.stage;
  complete.value = detail.value!.complete;
  reason.value = "";
  selected.value = [];
  for (const s of detail.value!.spans) speakers[s.id] = s.speaker;
  contextOpen.value = true;
  reviewOpen.value = feedbackOpen.value = false;
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
    historical.value = "";
    await refresh();
    notice.value = "背景已保存，系统继续质检；旧分析保留。";
  });
}
async function loadAudio() {
  if (audioUrl.value) return;
  const id = detail.value!.id,
    token = generation;
  const path =
    detail.value!.source_kind === "personal" ? "recordings" : "center";
  const response = await fetch(
    `/api/workspace/${path}/${detail.value!.source_id}/audio`,
    {
      headers: {
        Authorization: `Bearer ${sessionStorage.getItem("meetflow-token")}`,
      },
    },
  );
  if (response.status === 401) {
    sessionStorage.removeItem("meetflow-token");
    window.dispatchEvent(new Event("meetflow-session-expired"));
  }
  if (!response.ok) throw new Error("录音不可用或无权访问");
  const blob = await response.blob();
  if (dead || generation !== token || detail.value?.id !== id) return;
  audioUrl.value = URL.createObjectURL(blob);
}
async function seek(id: string) {
  highlighted.value = id;
  document
    .getElementById(`quality-span-${id}`)
    ?.scrollIntoView({ behavior: "smooth", block: "center" });
  const span = detail.value?.spans.find((s: Row) => s.id === id);
  if (!detail.value?.has_audio || span?.start == null) {
    notice.value = "已定位到文字依据；该片段没有可回听时间。";
    return;
  }
  await perform(async () => {
    await loadAudio();
    const setTime = () => {
      if (audio.value) audio.value.currentTime = span.start;
    };
    if (audio.value?.readyState) setTime();
    else
      audio.value?.addEventListener("loadedmetadata", setTime, { once: true });
    notice.value = `已定位到 ${Math.floor(span.start)} 秒，点播放即可回听。`;
  });
}
function startReview() {
  reviewOpen.value = true;
  contextOpen.value = feedbackOpen.value = false;
  selected.value = [];
  note.value = "";
  for (const d of decisions.value)
    reviewEdits[d.rule_id] = {
      status: d.status,
      reason: "",
      evidence: [...d.evidence],
    };
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
    notice.value = "复核已保存，机器结果和历次复核均保留。";
  });
}
function startFeedback(d: Row) {
  feedbackOpen.value = true;
  contextOpen.value = reviewOpen.value = false;
  feedback.rule_id = d.rule_id;
  feedback.reason = "";
  selected.value = [...d.evidence];
}
async function saveFeedback() {
  await perform(async () => {
    await post(`/workspace/quality/cases/${detail.value!.id}/feedback`, {
      version: detail.value!.version,
      report_hash: detail.value!.report_hash,
      ...feedback,
      evidence: selected.value,
    });
    feedbackOpen.value = false;
    await refresh();
    notice.value = "异议已提交，由负责团队主管处理。";
  });
}
onMounted(async () => {
  await perform(() => refresh(true));
  timer = window.setInterval(async () => {
    if (
      busy.value ||
      polling ||
      dead ||
      contextOpen.value ||
      reviewOpen.value ||
      feedbackOpen.value
    )
      return;
    polling = true;
    try {
      await refresh();
    } catch (e) {
      error.value = e instanceof Error ? e.message : "读取进度失败";
    } finally {
      polling = false;
    }
  }, 3500);
});
onBeforeUnmount(() => {
  dead = true;
  generation++;
  window.clearInterval(timer);
  clearAudio();
});
</script>

<template>
  <section class="quality">
    <header class="quality-heading">
      <div>
        <p class="eyebrow">
          {{
            user.role === "sales"
              ? "每次会谈，都能复盘"
              : "基于已上报会谈的培训复盘"
          }}
        </p>
        <h1>
          {{
            user.role === "sales"
              ? "我的复盘质检"
              : user.role === "boss"
                ? "公司质检概览"
                : "团队销售质检"
          }}
        </h1>
      </div>
      <button @click="perform(() => refresh())" :disabled="busy">
        刷新质检
      </button>
    </header>
    <div v-if="error" class="q-error" role="alert">{{ error }}</div>
    <div v-if="notice" class="q-notice" role="status">{{ notice }}</div>
    <div v-if="user.role === 'sales'" class="q-tabs" aria-label="质检资料范围">
      <button
        :class="{ active: scope === 'personal' }"
        @click="switchScope('personal')"
      >
        个人复盘</button
      ><button
        :class="{ active: scope === 'center' }"
        @click="switchScope('center')"
      >
        我的上报质检
      </button>
    </div>
    <div class="q-stats">
      <span
        >当前范围 <b>{{ rows.length }}</b> 场</span
      ><span
        >待核对身份
        <b>{{
          rows.filter((r) => r.status === "needs_speakers").length
        }}</b></span
      ><span
        >主管已复核
        <b>{{ rows.filter((r) => r.status === "reviewed").length }}</b></span
      ><span
        >待处理异议
        <b>{{ rows.reduce((n, r) => n + r.pending_feedback, 0) }}</b></span
      >
    </div>
    <p class="q-muted">
      {{
        scope === "personal"
          ? "未上报资料仅自己可见。上传一次，纪要与质检共用原文。"
          : "仅包含有权限的已上报来源，个人区后续修改不会覆盖中心依据。"
      }}
    </p>
    <div v-if="rows.some((r) => r.mode === 'demo')" class="q-notice">
      当前含规则演示数据，未调用大模型；演示分数仅用于验证流程，不作为实际销售评价。
    </div>
    <details v-if="groups.length" class="q-groups">
      <summary>查看分组统计 · 不同规则版本与分析模式分开</summary>
      <p v-for="g in groups" :key="`${g.policy.id}-${g.mode}-${g.reviewed}`">
        {{ g.policy.name }} v{{ g.policy.version }} ·
        {{ g.mode === "demo" ? "规则演示" : "模型分析" }} ·
        {{ g.reviewed ? "已复核" : "待复核／有异议" }} · {{ g.samples }} 场 ·
        可评项均分 {{ g.average_score ?? "无法计算" }}
      </p>
    </details>
    <div class="q-workbench">
      <aside class="q-rail">
        <label
          >查找会谈<input v-model="search" placeholder="客户、会谈、销售"
        /></label>
        <p v-if="!visible.length" class="q-muted">
          暂无质检。会谈整理完成后会自动准备，身份不明时先保留待确认。
        </p>
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
          ><span
            >{{ statuses[r.status]
            }}{{ r.pending_feedback ? " · 有异议" : "" }}</span
          ><span v-if="r.mode" class="q-mode">{{
            r.mode === "demo" ? "规则演示 · 非真实评价" : "模型分析"
          }}</span
          ><b
            >{{
              r.metrics?.score == null
                ? "尚无可评分数"
                : `${r.metrics.score} 分`
            }}
            <small v-if="r.metrics"
              >覆盖 {{ r.metrics.coverage ?? "—" }}%</small
            ></b
          >
        </button>
      </aside>
      <template v-if="detail">
        <main class="q-evidence">
          <div class="q-section-head">
            <div>
              <h2>{{ detail.metadata.title }}</h2>
              <p>
                {{ detail.owner }} ·
                {{ detail.metadata.customer || "客户待归属" }} · 来源 v{{
                  detail.source_revision
                }}
              </p>
            </div>
            <button
              @click="emit('meeting', detail.source_kind, detail.source_id)"
            >
              查看会谈纪要
            </button>
          </div>
          <div v-if="detail.stale || detail.historical" class="q-notice">
            正在查看旧版，不能覆盖当前结果。
          </div>
          <div v-if="detail.has_audio" class="q-player">
            <button
              v-if="!audioUrl"
              @click="perform(loadAudio)"
              :disabled="busy"
            >
              加载本场录音</button
            ><audio v-else ref="audio" :src="audioUrl" controls />
          </div>
          <div class="q-section-head">
            <h3>原文与说话人</h3>
            <button
              v-if="canContext && !contextOpen"
              @click="editContext"
              :disabled="busy || ['queued', 'running'].includes(detail.status)"
            >
              核对说话人与背景
            </button>
          </div>
          <form
            v-if="contextOpen"
            class="q-context"
            @submit.prevent="saveContext"
          >
            <p>
              按转写中的明确角色前缀识别身份；真实音频尚无自动说话人分离。只需核对不明确的片段。
            </p>
            <label
              >会谈阶段<select v-model="stage">
                <option v-for="s in stages" :key="s">{{ s }}</option>
              </select></label
            >
            <p class="q-muted">
              不确定阶段时保留通用复盘，不强行推测销售进度。
            </p>
            <label class="q-check"
              ><input
                type="checkbox"
                v-model="complete"
              />我确认录音完整覆盖本次会谈</label
            ><label v-if="scope === 'center'"
              >背景更正原因<textarea v-model="reason" required />
            </label>
            <div class="q-actions">
              <button
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
                设为客户</button
              ><button class="q-primary" :disabled="busy">
                保存背景并继续质检
              </button>
            </div>
          </form>
          <div class="q-transcript">
            <article
              v-for="s in detail.spans"
              :key="s.id"
              :id="`quality-span-${s.id}`"
              :class="{ highlight: highlighted === s.id }"
            >
              <header>
                <label
                  v-if="contextOpen || feedbackOpen || reviewOpen"
                  class="q-check"
                  ><input
                    type="checkbox"
                    :value="s.id"
                    v-model="selected"
                  />选择此段</label
                ><select
                  v-if="contextOpen"
                  v-model="speakers[s.id]"
                  :aria-label="`${s.id}说话人`"
                >
                  <option value="unknown">身份待确认</option>
                  <option value="sales">销售</option>
                  <option value="customer">客户</option></select
                ><span v-else class="q-speaker">{{
                  speakerNames[s.speaker]
                }}</span
                ><button class="q-time" @click="seek(s.id)">
                  {{ s.start == null ? s.id : `${Math.floor(s.start)}秒` }}
                </button>
              </header>
              <p>{{ s.text }}</p>
            </article>
          </div>
        </main>
        <aside class="q-findings">
          <div class="q-score">
            <span>{{ statuses[detail.status] }}</span
            ><strong
              >{{ detail.metrics?.score ?? "—" }}<small> / 100</small></strong
            >
            <p>
              可评覆盖率 {{ detail.metrics?.coverage ?? "—" }}% ·
              {{ detail.stage }}
            </p>
            <small>无法判断不当零分，不适用不进入分母。</small>
          </div>
          <div v-if="detail.mode === 'demo'" class="q-notice">
            规则演示，未调用大模型。字面命中只演示流程，分数不代表真实销售评价。
          </div>
          <p v-if="detail.policy" class="q-muted">
            {{ detail.policy.name }} · v{{ detail.policy.version }}
          </p>
          <p v-if="detail.status === 'needs_speakers'" class="q-notice">
            尚无法识别销售发言。请核对说话人后继续；纪要已可使用。
          </p>
          <p v-if="detail.error" class="q-error">{{ detail.error }}</p>
          <button
            v-if="detail.status === 'failed' && canContext"
            @click="
              perform(async () => {
                await post(`/workspace/quality/cases/${detail!.id}/retry`);
                await refresh();
              })
            "
          >
            恢复失败的质检
          </button>
          <p v-if="detail.report && !detail.complete" class="q-muted">
            录音完整性未确认，不据缺少发言认定销售未执行。
          </p>
          <button
            v-if="canReview && !reviewOpen"
            class="q-primary"
            @click="startReview"
          >
            {{ detail.status === "reviewed" ? "补充主管复核" : "复核本场质检" }}
          </button>
          <article v-for="d in decisions" :key="d.rule_id" class="q-rule">
            <header>
              <strong>{{
                detail.rules.find((r: Row) => r.id === d.rule_id)?.title
              }}</strong
              ><span :class="`judgment-${d.status}`">{{
                labels[d.status]
              }}</span>
            </header>
            <p>{{ d.reason }}</p>
            <p v-if="d.review_reason" class="q-review-note">
              主管说明：{{ d.review_reason }}
            </p>
            <small
              >权重 {{ d.weight }} · 得分 {{ d.points ?? "不计分" }} · 扣分
              {{ d.deduction ?? "不计分" }}</small
            >
            <div class="q-refs">
              <button v-for="id in d.evidence" :key="id" @click="seek(id)">
                依据 {{ id }}
              </button>
            </div>
            <details>
              <summary>培训要求与评分依据</summary>
              <p>
                {{
                  detail.rules.find((r: Row) => r.id === d.rule_id)?.criterion
                }}
              </p>
              <p>
                适用：{{
                  detail.rules.find((r: Row) => r.id === d.rule_id)
                    ?.applicability
                }}
              </p>
              <p>
                达标：{{
                  detail.rules.find((r: Row) => r.id === d.rule_id)?.pass_anchor
                }}
              </p>
              <p>
                部分达标：{{
                  detail.rules.find((r: Row) => r.id === d.rule_id)
                    ?.partial_anchor
                }}
              </p>
            </details>
            <p v-if="d.guidance" class="q-guidance">
              复盘建议：{{ d.guidance }}
            </p>
            <div v-if="reviewOpen" class="q-review-edit">
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
                使用选中的原文依据</button
              ><small
                >拟采用：{{
                  reviewEdits[d.rule_id].evidence.join("、") || "无"
                }}</small
              >
            </div>
            <button
              v-if="canFeedback && !feedbackOpen"
              class="q-link"
              @click="startFeedback(d)"
            >
              反馈这条判断
            </button>
          </article>
          <form v-if="reviewOpen" class="q-panel" @submit.prevent="saveReview">
            <label
              >复核与异议处理说明<textarea
                v-model="note"
                :required="detail.pending_feedback > 0"
              /></label
            ><button class="q-primary" :disabled="busy">确认保存主管复核</button
            ><button type="button" @click="reviewOpen = false">取消复核</button>
          </form>
          <form
            v-if="feedbackOpen"
            class="q-panel"
            @submit.prevent="saveFeedback"
          >
            <h3>向主管反馈判断</h3>
            <p>可在原文区勾选补充依据。</p>
            <label
              >异议原因<textarea
                v-model="feedback.reason"
                minlength="2"
                required
              /></label
            ><button class="q-primary" :disabled="busy">提交质检异议</button
            ><button type="button" @click="feedbackOpen = false">取消</button>
          </form>
          <section v-if="detail.feedback.length" class="q-panel">
            <h3>销售反馈与处理</h3>
            <article v-for="f in detail.feedback" :key="f.id">
              <strong>{{
                f.status === "pending"
                  ? "待主管处理"
                  : f.status === "resolved"
                    ? "已处理"
                    : "已更新背景重新分析"
              }}</strong>
              <p>{{ f.body.reason }}</p>
              <p v-if="f.resolution">主管处理：{{ f.resolution }}</p>
            </article>
          </section>
          <details v-if="detail.review_history.length" class="q-panel">
            <summary>
              历次主管复核（{{ detail.review_history.length }}）
            </summary>
            <p v-for="r in detail.review_history" :key="r.id">
              {{ r.actor }} · {{ r.body.metrics.score ?? "无法评分" }} 分 ·
              {{ r.body.note || "保留原判断" }}
            </p>
          </details>
          <details v-if="detail.source_history.length > 1" class="q-panel">
            <summary>
              来源修订历史（{{ detail.source_history.length }}）
            </summary>
            <button
              v-for="h in detail.source_history"
              :key="h.id"
              @click="perform(() => choose(h))"
            >
              来源 v{{ h.source_revision }} · {{ h.title
              }}{{ h.feedback_count ? ` · ${h.feedback_count}条反馈` : "" }}
            </button>
          </details>
          <details v-if="detail.history.length > 1" class="q-panel">
            <summary>历次分析（{{ detail.history.length }}）</summary>
            <button @click="perform(() => choose(detail!))">查看当前分析</button
            ><button
              v-for="(h, i) in detail.history"
              :key="h.id"
              @click="perform(() => choose(detail!, h.id))"
            >
              第 {{ Number(i) + 1 }} 次分析
            </button>
          </details>
        </aside>
      </template>
      <div v-else class="q-placeholder">
        从左侧选择一场会谈，结合原文查看做对的地方、待改进点和评分依据。
      </div>
    </div>
  </section>
</template>

<style scoped>
.quality {
  color: #203b33;
}
.quality-heading,
.q-section-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
}
.quality-heading h1 {
  margin: 0 0 12px;
}
.eyebrow {
  color: #627b70;
  margin: 0;
  font-size: 12px;
  letter-spacing: 1px;
}
.quality button {
  cursor: pointer;
  border: 1px solid #d7e1db;
  background: #fff;
  color: #244c3e;
  padding: 8px 12px;
  border-radius: 8px;
  font: inherit;
}
.quality button:disabled {
  opacity: 0.5;
  cursor: wait;
}
.quality input,
.quality select,
.quality textarea {
  width: 100%;
  padding: 8px;
  border: 1px solid #d0dcd4;
  border-radius: 6px;
  font: inherit;
  background: #fff;
  box-sizing: border-box;
}
.quality label {
  display: block;
  font-size: 12px;
}
.quality textarea {
  min-height: 70px;
}
.q-tabs {
  display: flex;
  gap: 8px;
  margin: 12px 0;
}
.q-tabs .active,
.quality .q-primary {
  background: #254f40;
  color: #fff;
}
.q-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 24px;
  background: #e9efe8;
  padding: 14px 18px;
  border-radius: 10px;
}
.q-stats b {
  font-size: 20px;
  margin-left: 8px;
}
.q-muted {
  color: #738077;
  font-size: 12px;
}
.q-error {
  color: #8a2f2c;
  background: #fff0ed;
  padding: 12px;
}
.q-notice {
  background: #eff3e7;
  color: #5b6143;
  padding: 12px;
  border-radius: 8px;
  font-size: 12px;
  margin: 10px 0;
}
.q-workbench {
  display: grid;
  grid-template-columns: 210px minmax(250px, 1fr) 330px;
  gap: 16px;
  align-items: start;
  margin-top: 18px;
}
.q-rail {
  position: sticky;
  top: 12px;
  max-height: 80vh;
  overflow: auto;
}
.q-rail .q-call {
  display: flex;
  flex-direction: column;
  gap: 7px;
  text-align: left;
  width: 100%;
  margin-top: 10px;
  padding: 14px;
}
.q-call span,
.q-call small {
  font-size: 11px;
  color: #738077;
}
.q-call.selected {
  border-color: #46765d;
  background: #edf3ed;
}
.q-evidence,
.q-findings {
  min-width: 0;
}
.q-evidence {
  margin: 0;
  width: auto;
  background: #fff;
  border: 1px solid #e0e7df;
  border-radius: 12px;
  padding: 18px;
}
.q-section-head h2 {
  font-size: 17px;
  margin: 0;
}
.q-section-head p {
  font-size: 12px;
  color: #738077;
}
.q-section-head h3 {
  font-size: 14px;
}
.q-player {
  position: sticky;
  top: 0;
  background: #f3f6f1;
  padding: 10px;
  border-radius: 8px;
  z-index: 1;
}
.q-player audio {
  width: 100%;
  height: 40px;
}
.q-transcript {
  max-height: 70vh;
  overflow: auto;
  padding-right: 6px;
}
.q-transcript article {
  border-bottom: 1px solid #edf0ec;
  padding: 14px 6px;
  scroll-margin-top: 90px;
}
.q-transcript article.highlight {
  background: #fff4d6;
  border-left: 3px solid #bd9654;
}
.q-transcript article header {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
}
.q-transcript article p {
  margin: 8px 0 0;
  white-space: pre-wrap;
}
.q-transcript select {
  width: 120px;
}
.q-speaker {
  border-radius: 5px;
  background: #eef4ee;
  padding: 2px 6px;
}
.quality .q-time {
  margin-left: auto;
  border: 0;
  padding: 0;
  color: #698477;
}
.q-score {
  background: #234c3e;
  border-radius: 12px;
  padding: 20px;
  color: #fff;
}
.q-score > span {
  font-size: 12px;
}
.q-score > strong {
  display: block;
  font-size: 42px;
  line-height: 1.4;
}
.q-score small {
  font-size: 11px;
  opacity: 0.8;
}
.q-score p {
  margin: 4px 0;
  font-size: 12px;
}
.q-rule,
.q-panel {
  background: #fff;
  padding: 15px;
  border: 1px solid #e1e7df;
  border-radius: 10px;
  margin-top: 12px;
  font-size: 12px;
}
.q-rule header {
  display: flex;
  justify-content: space-between;
  gap: 8px;
}
.q-rule header strong {
  font-size: 14px;
}
.q-rule p {
  margin: 10px 0;
}
.q-rule > small {
  color: #798477;
}
.judgment-unmet {
  color: #a04c3c;
}
.judgment-met {
  color: #247347;
}
.judgment-unknown {
  color: #8d805d;
}
.q-refs {
  display: flex;
  gap: 5px;
  flex-wrap: wrap;
  margin: 9px 0;
}
.quality .q-refs button {
  padding: 3px 7px;
  font-size: 11px;
}
.q-guidance {
  background: #f3f6ee;
  padding: 9px;
}
.q-review-note {
  color: #546b86;
}
.q-link {
  margin-top: 10px;
}
.q-panel button {
  margin: 7px 5px 0 0;
}
.q-review-edit label {
  margin: 8px 0;
}
.q-review-edit small {
  display: block;
  margin-top: 5px;
}
.q-context {
  padding: 12px;
  border: 1px dashed #a2b6a7;
  border-radius: 8px;
  font-size: 12px;
}
.q-actions {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: 12px;
}
.quality .q-check {
  display: flex;
  align-items: center;
  gap: 6px;
}
.quality .q-check input {
  width: auto;
}
.q-placeholder {
  grid-column: span 2;
  background: #fff;
  border-radius: 12px;
  padding: 50px 25px;
  color: #7c887e;
}
.q-groups {
  font-size: 12px;
}
@media (max-width: 1150px) {
  .q-workbench {
    grid-template-columns: 170px minmax(220px, 1fr) 290px;
    gap: 10px;
  }
  .q-evidence {
    padding: 12px;
  }
}
@media (max-width: 850px) {
  .q-workbench {
    grid-template-columns: 1fr;
  }
  .q-rail {
    position: static;
    max-height: 230px;
  }
  .q-placeholder {
    grid-column: auto;
  }
  .q-transcript {
    max-height: 50vh;
  }
  .q-stats {
    gap: 14px;
  }
  .q-section-head,
  .quality-heading {
    flex-wrap: wrap;
  }
}
</style>
