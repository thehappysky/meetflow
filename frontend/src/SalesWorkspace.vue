<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { api, post } from "./api";

type Row = Record<string, any>;
const props = defineProps<{ projectId: string; health: Row }>();
const tab = ref("calls"),
  busy = ref(false),
  error = ref(""),
  notice = ref("");
const calls = ref<Row[]>([]),
  cards = ref<Row[]>([]),
  board = ref<Row | null>(null);
const detail = ref<Row | null>(null),
  conversation = ref<Row | null>(null);
const draft = ref<Row | null>(null),
  showImport = ref(false),
  cardId = ref("");
const editingSpeakers = ref(false);
const mode = ref("demo"),
  file = ref<File | null>(null),
  importKind = ref("text");
const evidence = ref<string[]>([]),
  player = ref<HTMLAudioElement | null>(null);
const edits = reactive<Record<string, Row>>({}),
  followups = ref<string[]>([]),
  reviewNote = ref("");
const filters = reactive({
  mode: "live",
  playbook_id: "",
  salesperson: "",
  start: "",
  end: "",
});
const form = reactive({
  title: "",
  salesperson: "",
  customer: "",
  occurred_on: "",
  complete: false,
  transcript: "",
});
const names: Row = {
  met: "达标",
  partial: "部分达标",
  unmet: "未达标",
  not_applicable: "不适用",
  unknown: "无法判断",
};
const statuses: Row = {
  queued: "等待质检",
  running: "专业分析中",
  failed: "质检失败",
  waiting_review: "待主管复核",
  reviewed: "已复核",
  cancelled: "已停止",
  ready: "可开始质检",
  transcribing: "转写中",
  needs_speakers: "待确认说话人",
  audio_failed: "转写失败",
};
const dimensions: Row = {
  discovery: "需求与交易信息",
  process: "流程与下一步",
  product: "产品与承诺",
};
const topics: Row = {
  price: "价格",
  feature: "产品需求",
  competition: "竞品",
  procurement: "采购流程",
  next_step: "下一步",
  other: "其他",
};
const analysis = computed(() => detail.value?.analysis);
const report = computed(
  () => detail.value?.review?.body || analysis.value?.report,
);
const activeCard = computed(
  () =>
    analysis.value?.snapshot.playbook ||
    cards.value.find((c) => c.id === cardId.value),
);
const rules = computed<Row[]>(() => activeCard.value?.body.rules || []);
const spans = computed<Row[]>(() => conversation.value?.body.spans || []);
const sourceSpans = computed<Row[]>(
  () => analysis.value?.snapshot.conversation.body.spans || spans.value,
);
const selectedEvidence = computed(() =>
  sourceSpans.value.filter((s) => evidence.value.includes(s.id)),
);
const canReview = computed(
  () => analysis.value?.status === "waiting_review" && !detail.value?.stale,
);
const isProcessing = computed(() =>
  ["queued", "running"].includes(analysis.value?.status),
);
const scoreText = (value: number | null | undefined) =>
  value == null ? "—" : value;
let timer: number | undefined,
  destroyed = false,
  selection = 0,
  polling = false;
let reviewKey = "";

async function action(work: () => Promise<void>) {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  notice.value = "";
  try {
    await work();
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : "操作失败，请重试";
  } finally {
    busy.value = false;
  }
}
async function refresh() {
  const [nextCards, nextCalls] = await Promise.all([
    api(`/sales/playbooks?project_id=${props.projectId}`),
    api(`/sales/conversations?project_id=${props.projectId}`),
  ]);
  if (destroyed) return;
  cards.value = nextCards;
  calls.value = nextCalls;
  if (!cards.value.some((c) => c.id === cardId.value))
    cardId.value = cards.value[0]?.id || "";
}
async function loadBoard() {
  const query = new URLSearchParams({ project_id: props.projectId });
  Object.entries(filters).forEach(([key, value]) => {
    if (value) query.set(key, value);
  });
  const next = await api(`/sales/dashboard?${query}`);
  if (!destroyed) board.value = next;
}
async function setDetail(key: string) {
  const version = selection;
  const next = await api(`/sales/analyses/${key}`);
  if (destroyed || version !== selection) return;
  const changed =
    analysis.value?.id !== key ||
    analysis.value?.report_hash !== next.analysis.report_hash;
  detail.value = next;
  if (changed) {
    Object.keys(edits).forEach((key) => delete edits[key]);
    (
      next.review?.body.decisions ||
      next.analysis.report?.decisions ||
      []
    ).forEach((item: Row) => {
      edits[item.rule_id] = { status: item.status, reason: "" };
    });
    followups.value = [];
    reviewNote.value = "";
    reviewKey = crypto.randomUUID();
  }
}
async function openCall(row: Row) {
  selection++;
  evidence.value = [];
  editingSpeakers.value = false;
  detail.value = null;
  conversation.value = row;
  if (row.analysis) await setDetail(row.analysis.id);
}
function closeCall() {
  selection++;
  conversation.value = null;
  detail.value = null;
  evidence.value = [];
  editingSpeakers.value = false;
}
async function demo() {
  await action(async () => {
    const result = await post(`/sales/demo?project_id=${props.projectId}`);
    await refresh();
    selection++;
    conversation.value = result.conversation;
    cardId.value = result.playbook.id;
    await setDetail(result.analysis.id);
  });
}
async function startAnalysis() {
  await action(async () => {
    const result = await post(
      `/sales/conversations/${conversation.value!.id}/analyze`,
      { playbook_id: cardId.value, mode: mode.value },
    );
    await setDetail(result.id);
    await refresh();
  });
}
async function importCall() {
  await action(async () => {
    const card = cards.value.find((c) => c.id === cardId.value);
    if (!card) throw new Error("先发布并选择一张评分卡");
    const metadata = {
      ...form,
      project_id: props.projectId,
      stage: card.body.stage,
      occurred_on: form.occurred_on || null,
    };
    let result: Row;
    if (importKind.value === "audio") {
      if (!file.value) throw new Error("请选择录音文件");
      const body = new FormData();
      body.append("metadata", JSON.stringify(metadata));
      body.append("file", file.value);
      result = await api("/sales/audio", { method: "POST", body });
    } else result = await post("/sales/conversations", metadata);
    await refresh();
    selection++;
    conversation.value = result;
    detail.value = null;
    showImport.value = false;
    if (importKind.value === "text") {
      const next = await post(`/sales/conversations/${result.id}/analyze`, {
        playbook_id: card.id,
        mode: mode.value,
      });
      await setDetail(next.id);
      await refresh();
    }
  });
}
async function saveSpeakers() {
  await action(async () => {
    conversation.value = await api(
      `/sales/conversations/${conversation.value!.id}/spans`,
      {
        method: "PATCH",
        body: JSON.stringify({
          version: conversation.value!.version,
          spans: spans.value,
          complete: conversation.value!.body.complete,
        }),
      },
    );
    detail.value = null;
    editingSpeakers.value = false;
    notice.value = "身份与完整性已保存，请选择相同阶段的评分卡开始质检。";
    await refresh();
  });
}
async function saveReview() {
  await action(async () => {
    const decisions = Object.entries(edits).map(([rule_id, value]) => ({
      rule_id,
      status: value.status,
      reason: value.reason,
    }));
    await post(`/sales/analyses/${analysis.value!.id}/review`, {
      report_hash: analysis.value!.report_hash,
      conversation_version: analysis.value!.snapshot.conversation.version,
      idempotency_key: reviewKey,
      decisions,
      followup_rule_ids: followups.value,
      note: reviewNote.value,
    });
    await setDetail(analysis.value!.id);
    await refresh();
    notice.value = "复核已保存；所选辅导事项已写入任务看板。";
  });
}
async function control(kind: string) {
  await action(async () => {
    await post(`/sales/analyses/${analysis.value!.id}/${kind}`);
    await setDetail(analysis.value!.id);
  });
}
async function editCard(card?: Row) {
  await action(async () => {
    draft.value = card
      ? { ...JSON.parse(JSON.stringify(card.body)), base_id: card.id }
      : await api("/sales/template");
    tab.value = "cards";
    closeCall();
  });
}
async function publish() {
  await action(async () => {
    const result = await post("/sales/playbooks", {
      ...draft.value,
      project_id: props.projectId,
    });
    await refresh();
    cardId.value = result.id;
    draft.value = null;
    notice.value = `评分卡 v${result.version} 已发布，旧分析继续使用原版本。`;
  });
}
function addRule() {
  draft.value!.rules.push({
    id: `rule_${Date.now()}`,
    title: "",
    dimension: "process",
    weight: 10,
    criterion: "",
    applicability: "",
    pass_anchor: "",
    partial_anchor: "",
    demo_terms: [],
  });
}
function seek(span: Row) {
  if (player.value && span.start != null) {
    player.value.currentTime = span.start;
    player.value.play().catch(() => {});
  }
}
async function readText(event: Event) {
  const next = (event.target as HTMLInputElement).files?.[0];
  if (!next) return;
  if (next.size > 500000) {
    error.value = "文字文件过大，请按实际会话拆分";
    return;
  }
  form.transcript = await next.text();
}
onMounted(() => {
  action(refresh);
  timer = window.setInterval(async () => {
    if (busy.value || polling || destroyed) return;
    polling = true;
    try {
      if (isProcessing.value) await setDetail(analysis.value!.id);
      if (conversation.value?.status === "transcribing") {
        const key = conversation.value.id,
          version = selection;
        const next = await api(`/sales/conversations/${key}`);
        if (!destroyed && selection === version) conversation.value = next;
      }
      if (
        calls.value.some(
          (c) =>
            ["queued", "running"].includes(c.analysis?.status) ||
            c.status === "transcribing",
        )
      )
        await refresh();
    } catch (exc) {
      if (!destroyed)
        error.value = exc instanceof Error ? exc.message : "进度读取失败";
    } finally {
      polling = false;
    }
  }, 1500);
});
onBeforeUnmount(() => {
  destroyed = true;
  selection++;
  window.clearInterval(timer);
});
</script>

<template>
  <section class="sales-workspace">
    <div class="page-heading">
      <div>
        <div class="eyebrow">SALES QUALITY</div>
        <h1>销售会话质检</h1>
        <p>把会话变成可核对的评价，帮助主管找到值得辅导的地方。</p>
      </div>
      <button class="primary" :disabled="busy" @click="demo">
        体验销售示例
      </button>
    </div>
    <div class="sales-notice">
      本地单用户试用版。规则演示与真实模型分开统计；评分用于复核和辅导，不能代表成交能力。
    </div>
    <div v-if="error" class="alert error" role="alert">
      {{ error }}<button @click="error = ''">关闭</button>
    </div>
    <div v-if="notice" class="alert success" role="status">{{ notice }}</div>
    <div class="sales-tabs" role="tablist" aria-label="销售工作台">
      <button
        v-for="[key, label] in [
          ['calls', '会话与复核'],
          ['dashboard', '团队概览'],
          ['cards', '评分标准'],
        ]"
        :key="key"
        :class="{ active: tab === key }"
        @click="
          tab = key;
          closeCall();
          if (key === 'dashboard') action(loadBoard);
        "
      >
        {{ label }}
      </button>
    </div>

    <template v-if="tab === 'calls'">
      <template v-if="conversation">
        <button class="text-button" @click="closeCall">← 返回会话列表</button>
        <div class="sales-panel">
          <h2>{{ conversation.body.title }}</h2>
          <p>
            {{ conversation.body.salesperson }} ·
            {{ conversation.body.customer }} · {{ conversation.body.stage }} ·
            {{ conversation.body.occurred_on || "实际日期未提供" }}
          </p>
          <p v-if="analysis" role="status">
            {{ statuses[analysis.status] }} · {{ analysis.phase }} ·
            {{
              analysis.mode === "demo"
                ? "规则演示（非模型语义判断）"
                : "真实模型"
            }}
          </p>
          <p v-else role="status">{{ statuses[conversation.status] }}</p>
          <audio
            v-if="conversation.has_audio"
            ref="player"
            controls
            :src="`/api/sales/conversations/${conversation.id}/audio`"
          />
          <div v-if="conversation.error || analysis?.error" class="alert error">
            {{ conversation.error || analysis?.error }}
          </div>
          <div v-if="detail?.stale" class="alert error">
            原会话已有新版本；此报告仅保留历史依据，不能继续复核。
          </div>
          <div class="sales-controls">
            <label
              >评分卡<select v-model="cardId">
                <option value="" disabled>选择评分卡</option>
                <option
                  v-for="card in cards.filter(
                    (c) => c.body.stage === conversation!.body.stage,
                  )"
                  :key="card.id"
                  :value="card.id"
                >
                  {{ card.body.name }} · v{{ card.version }}
                </option>
              </select></label
            >
            <label
              >分析方式<select v-model="mode">
                <option value="demo">规则演示</option>
                <option value="live" :disabled="!health.live_available">
                  真实模型{{ health.live_available ? "" : "（服务端未配置）" }}
                </option>
              </select></label
            >
            <button
              v-if="conversation.status === 'ready' && !isProcessing"
              class="primary"
              :disabled="busy || !cardId"
              @click="startAnalysis"
            >
              {{ analysis ? "新建分析版本" : "开始质检" }}
            </button>
            <button
              v-if="conversation.status === 'ready' && !isProcessing"
              :disabled="busy"
              @click="editingSpeakers = !editingSpeakers"
            >
              核对发言身份
            </button>
            <button
              v-if="isProcessing"
              :disabled="busy"
              @click="control('cancel')"
            >
              停止分析
            </button>
            <button
              v-if="analysis?.status === 'failed'"
              :disabled="busy"
              @click="control('retry')"
            >
              恢复失败分析
            </button>
            <a
              v-if="analysis?.report"
              class="sales-link"
              :href="`/api/sales/analyses/${analysis.id}/export`"
              download
              >导出质检报告</a
            >
          </div>
        </div>

        <div
          v-if="
            conversation.status === 'needs_speakers' ||
            editingSpeakers ||
            (!analysis && conversation.status === 'ready')
          "
          class="sales-panel"
        >
          <h3>确认发言身份</h3>
          <p>
            当前没有自动说话人分离。请按原录音逐段确认；保留未知时，相关未执行判断会转为无法判断。
          </p>
          <div v-for="span in spans" :key="span.id" class="sales-span-edit">
            <button
              class="text-button"
              :disabled="span.start == null"
              @click="seek(span)"
            >
              {{ span.id
              }}{{ span.start != null ? ` · ${Math.floor(span.start)}s` : "" }}
            </button>
            <select v-model="span.speaker" :aria-label="`${span.id} 发言身份`">
              <option value="unknown">未知</option>
              <option value="sales">销售</option>
              <option value="customer">客户</option></select
            ><span>{{ span.text }}</span>
          </div>
          <label class="sales-check"
            ><input
              v-model="conversation.body.complete"
              type="checkbox"
            />已核对：这是完整的本次会话，而非节选</label
          >
          <button class="primary" :disabled="busy" @click="saveSpeakers">
            保存身份确认
          </button>
        </div>

        <template v-if="report">
          <div class="sales-stats">
            <div>
              <span>可评项目得分</span
              ><strong
                >{{ scoreText(report.metrics.score)
                }}<small>/100</small></strong
              >
            </div>
            <div>
              <span>可评权重覆盖率</span
              ><strong
                >{{ scoreText(report.metrics.coverage)
                }}<small>%</small></strong
              >
            </div>
            <div>
              <span>无法判断</span
              ><strong
                >{{ report.metrics.unknown_count }}<small>项</small></strong
              >
            </div>
          </div>
          <p class="muted">
            {{ activeCard?.body.name }} v{{ activeCard?.version }} ·
            仅可评项计入得分；不适用移出分母，无法判断单独影响覆盖率。高分配合低覆盖率不能代表整体表现好。
          </p>
          <div
            v-for="item in report.decisions"
            :key="item.rule_id"
            class="sales-panel sales-decision"
          >
            <div class="sales-row">
              <h3>{{ rules.find((r) => r.id === item.rule_id)?.title }}</h3>
              <span class="sales-badge">{{ names[item.status] }}</span>
            </div>
            <p class="muted">
              权重 {{ item.weight }} ·
              {{
                item.points == null
                  ? "本项不计分"
                  : `得分 ${item.points}/${item.weight}，扣分 ${item.deduction}`
              }}
            </p>
            <p>{{ item.reason }}</p>
            <p v-if="item.review_reason">
              <strong>主管修订：</strong>{{ item.review_reason }}
            </p>
            <p class="sales-guidance">{{ item.guidance }}</p>
            <details>
              <summary>查看评价标准</summary>
              <p>{{ rules.find((r) => r.id === item.rule_id)?.criterion }}</p>
              <p>
                适用条件：{{
                  rules.find((r) => r.id === item.rule_id)?.applicability
                }}
              </p>
              <p>
                达标：{{
                  rules.find((r) => r.id === item.rule_id)?.pass_anchor
                }}
              </p>
            </details>
            <button
              v-if="item.evidence.length"
              class="text-button"
              @click="evidence = item.evidence"
            >
              核对原文 · {{ item.evidence.join(", ") }}
            </button>
            <p v-else class="muted">
              本项无直接发言引用，请结合原文和材料完整性核对。
            </p>
            <div
              v-if="canReview && edits[item.rule_id]"
              class="sales-review-fields"
            >
              <label
                >主管判断<select
                  v-model="edits[item.rule_id].status"
                  :aria-label="`${item.rule_id} 主管判断`"
                >
                  <option v-for="(label, key) in names" :key="key" :value="key">
                    {{ label }}
                  </option>
                </select></label
              >
              <label
                >改判原因<input
                  v-model="edits[item.rule_id].reason"
                  :aria-label="`${item.rule_id} 改判原因`"
                  placeholder="修改判断时必须说明依据；原机器结果会保留"
              /></label>
              <label class="sales-check"
                ><input
                  v-model="followups"
                  :value="item.rule_id"
                  type="checkbox"
                  :aria-label="`${item.rule_id} 创建辅导任务`"
                />创建此项辅导任务</label
              >
            </div>
          </div>
          <div v-if="analysis.report.insights.length" class="sales-panel">
            <h3>客户信号</h3>
            <p class="muted">
              自动提取，尚未逐条人工核准；不等于真实失单原因。
            </p>
            <div
              v-for="(item, index) in analysis.report.insights"
              :key="index"
              class="sales-insight"
            >
              <span class="sales-badge">{{ topics[item.topic] }}</span>
              <p>{{ item.text }}</p>
              <button class="text-button" @click="evidence = item.evidence">
                核对原文
              </button>
            </div>
          </div>
          <div v-if="canReview" class="sales-panel">
            <h3>保存主管复核</h3>
            <label
              >复核备注<textarea
                v-model="reviewNote"
                placeholder="补充背景、辅导安排或本次判断限制"
              />
            </label>
            <p>
              仅勾选的
              {{ followups.length }}
              项会创建本地辅导任务；不会自动联系销售或写入外部 CRM。
            </p>
            <button class="primary" :disabled="busy" @click="saveReview">
              保存复核结果
            </button>
          </div>
          <div v-if="detail?.review" class="sales-notice" role="status">
            主管复核已保存 ·
            {{ detail.review.body.task_ids.length }}
            项辅导任务。可从左侧“任务看板”继续跟进。{{
              detail.review.body.note
            }}
          </div>
        </template>
        <details v-if="analysis" class="sales-panel">
          <summary>处理记录与协作产物</summary>
          <p>
            模型调用 {{ analysis.model_calls }} · 工具调用
            {{ analysis.tool_calls }} · 输入 {{ analysis.input_tokens }} / 输出
            {{ analysis.output_tokens }} tokens
          </p>
          <p v-for="(event, index) in analysis.events" :key="index">
            {{ event.message }}
          </p>
          <details v-for="(value, key) in analysis.artifacts" :key="key">
            <summary>{{ key }}</summary>
            <pre>{{ JSON.stringify(value, null, 2) }}</pre>
          </details>
        </details>
        <details class="sales-panel">
          <summary>
            完整会话原文{{ analysis ? "（本次分析冻结版本）" : "" }}
          </summary>
          <p v-for="span in sourceSpans" :key="span.id">
            [{{ span.id }}]
            {{
              { sales: "销售", customer: "客户", unknown: "未知" }[
                span.speaker as string
              ]
            }}：{{ span.text }}
          </p>
        </details>
      </template>

      <template v-else>
        <div class="sales-row">
          <h2>会话与复核</h2>
          <button
            class="primary"
            :disabled="busy"
            @click="showImport = !showImport"
          >
            导入销售会话
          </button>
        </div>
        <form
          v-if="showImport"
          class="sales-panel"
          @submit.prevent="importCall"
        >
          <h3>导入会话</h3>
          <p>
            按已发布评分卡的阶段处理。文字建议每行使用“销售：”或“客户：”；其他身份保留未知。
          </p>
          <div class="sales-form-grid">
            <label
              >会话标题<input
                v-model="form.title"
                required
                maxlength="200" /></label
            ><label
              >销售人员<input
                v-model="form.salesperson"
                required
                maxlength="100" /></label
            ><label
              >客户 / 商机标识<input
                v-model="form.customer"
                required
                maxlength="150" /></label
            ><label
              >实际日期（可空）<input v-model="form.occurred_on" type="date"
            /></label>
            <label
              >评分卡<select v-model="cardId" required>
                <option value="" disabled>请先发布评分卡</option>
                <option v-for="card in cards" :key="card.id" :value="card.id">
                  {{ card.body.name }} · {{ card.body.stage }} · v{{
                    card.version
                  }}
                </option>
              </select></label
            >
            <label
              >分析方式<select v-model="mode">
                <option value="demo">规则演示</option>
                <option value="live" :disabled="!health.live_available">
                  真实模型{{ health.live_available ? "" : "（未配置）" }}
                </option>
              </select></label
            >
          </div>
          <label
            >输入形式<select v-model="importKind">
              <option value="text">已有转写文字</option>
              <option value="audio" :disabled="!health.audio_available">
                录音{{ health.audio_available ? "" : "（需配置 ASR）" }}
              </option>
            </select></label
          >
          <template v-if="importKind === 'text'"
            ><label
              >读取文字文件<input
                type="file"
                accept=".txt,.md"
                @change="readText" /></label
            ><label
              >会话文字<textarea
                v-model="form.transcript"
                required
                maxlength="50000"
                rows="8"
                placeholder="销售：您目前怎么处理这个问题？&#10;客户：……"
              /></label
            ><label class="sales-check"
              ><input
                v-model="form.complete"
                type="checkbox"
              />这是完整会话，且已核对发言身份</label
            ></template
          >
          <label v-else
            >录音文件<input
              type="file"
              accept=".wav,.mp3,.m4a,.flac,.ogg,.webm"
              required
              @change="
                file = ($event.target as HTMLInputElement).files?.[0] || null
              "
            />转写后必须先确认身份，不直接评价销售。</label
          >
          <button class="primary" :disabled="busy || !cardId">
            {{ importKind === "audio" ? "上传并转写" : "导入并质检" }}
          </button>
        </form>
        <div v-if="!calls.length" class="sales-panel sales-empty">
          <h3>从一场真实业务沟通开始</h3>
          <p>
            先发布本企业的阶段评分卡，再导入已有转写。也可先体验明确标记的合成示例。
          </p>
          <button @click="editCard()">配置评分标准</button>
        </div>
        <button
          v-for="call in calls"
          :key="call.id"
          class="sales-call"
          @click="action(() => openCall(call))"
        >
          <span
            ><strong>{{ call.body.title }}</strong
            ><small
              >{{ call.body.salesperson }} · {{ call.body.customer }} ·
              {{ call.body.stage }}</small
            ></span
          ><span class="sales-badge">{{
            statuses[call.analysis?.status || call.status]
          }}</span>
        </button>
      </template>
    </template>

    <template v-if="tab === 'dashboard'">
      <h2>团队概览</h2>
      <p>同一销售按评分卡版本分别显示；样本量和覆盖率与得分同时查看。</p>
      <form
        class="sales-panel sales-controls"
        @submit.prevent="action(loadBoard)"
      >
        <label
          >数据模式<select v-model="filters.mode" aria-label="数据模式">
            <option value="live">真实模型</option>
            <option value="demo">规则演示</option>
          </select></label
        ><label
          >评分卡版本<select
            v-model="filters.playbook_id"
            aria-label="评分卡版本"
          >
            <option value="">全部（分组显示）</option>
            <option v-for="card in cards" :key="card.id" :value="card.id">
              {{ card.body.name }} v{{ card.version }}
            </option>
          </select></label
        ><label
          >销售人员<input
            v-model="filters.salesperson"
            placeholder="精确姓名，可空" /></label
        ><label>开始日期<input v-model="filters.start" type="date" /></label
        ><label>结束日期<input v-model="filters.end" type="date" /></label
        ><button class="primary" :disabled="busy">应用筛选</button>
      </form>
      <template v-if="board"
        ><div class="sales-notice">
          {{
            board.mode === "demo"
              ? "规则演示数据 · 不代表真实质量"
              : "真实模型数据"
          }}
          · 已复核 {{ board.reviewed_conversations }} 场。{{ board.notice }}
        </div>
        <p v-if="board.has_pending_reviews" class="muted">
          该项目还有待复核记录，尚未计入以上汇总。
        </p>
        <div v-if="!board.people.length" class="sales-panel sales-empty">
          当前筛选下没有已复核数据。请先完成会话复核，或切换规则演示。
        </div>
        <div class="sales-people">
          <div
            v-for="person in board.people"
            :key="person.salesperson + person.playbook_id"
            class="sales-panel"
          >
            <h3>{{ person.salesperson }}</h3>
            <p>
              {{ cards.find((c) => c.id === person.playbook_id)?.body.name }}
              v{{ cards.find((c) => c.id === person.playbook_id)?.version }}
            </p>
            <strong class="sales-number"
              >{{ scoreText(person.average_score) }}<small> 分</small></strong
            >
            <p>
              {{ person.samples }} 场样本 · 平均可评覆盖率
              {{ scoreText(person.average_coverage) }}%
            </p>
          </div>
        </div>
        <div class="sales-form-grid">
          <div class="sales-panel">
            <h3>值得辅导的规则项</h3>
            <p v-for="gap in board.gaps" :key="gap.playbook_id + gap.title">
              {{ gap.title }} · {{ gap.conversations }} 场部分达标/未达标（v{{
                cards.find((c) => c.id === gap.playbook_id)?.version
              }}）
            </p>
            <p v-if="!board.gaps.length">暂无可统计问题项。</p>
          </div>
          <div class="sales-panel">
            <h3>客户信号分布</h3>
            <p v-for="item in board.topics" :key="item.topic">
              {{ topics[item.topic] }} · {{ item.conversations }} 场会话提及
            </p>
            <p class="muted">
              按会话去重的自动提取信号，未逐条人工核准，不是客户人数、成交原因或效果归因。
            </p>
          </div>
        </div>
      </template>
    </template>

    <template v-if="tab === 'cards'">
      <div class="sales-row">
        <h2>评分标准</h2>
        <button :disabled="busy" @click="editCard()">新建评分卡</button>
      </div>
      <p>
        每张卡对应一个销售阶段。发布后不改写；调整时发布新版本，原分析保留原规则。
      </p>
      <form v-if="draft" class="sales-panel" @submit.prevent="publish">
        <div class="sales-form-grid">
          <label
            >评分卡名称<input
              v-model="draft.name"
              required
              maxlength="150" /></label
          ><label
            >适用销售阶段<input v-model="draft.stage" required maxlength="80"
          /></label>
        </div>
        <label
          >已确认产品资料与承诺边界<textarea
            v-model="draft.product_notes"
            maxlength="15000"
            rows="4"
          />
        </label>
        <fieldset
          v-for="(rule, index) in draft.rules"
          :key="rule.id"
          class="sales-rule"
        >
          <legend>规则 {{ Number(index) + 1 }}</legend>
          <div class="sales-form-grid">
            <label
              >规则编号<input
                v-model="rule.id"
                required
                pattern="[a-zA-Z0-9_-]{1,60}" /></label
            ><label
              >标题<input
                v-model="rule.title"
                required
                maxlength="150" /></label
            ><label
              >专业维度<select v-model="rule.dimension">
                <option
                  v-for="(label, key) in dimensions"
                  :key="key"
                  :value="key"
                >
                  {{ label }}
                </option>
              </select></label
            ><label
              >权重<input
                v-model.number="rule.weight"
                type="number"
                min="1"
                max="100"
                required
            /></label>
          </div>
          <label
            >评价标准<textarea
              v-model="rule.criterion"
              required
              maxlength="1500"
            /></label
          ><label
            >适用条件<input
              v-model="rule.applicability"
              required
              maxlength="1000" /></label
          ><label
            >达标描述<input
              v-model="rule.pass_anchor"
              required
              maxlength="1500" /></label
          ><label
            >部分达标描述<input
              v-model="rule.partial_anchor"
              required
              maxlength="1500" /></label
          ><label
            >字面演示命中词（用 | 分隔，仅用于规则演示）<input
              :value="rule.demo_terms.join('|')"
              @input="
                rule.demo_terms = ($event.target as HTMLInputElement).value
                  .split('|')
                  .map((s) => s.trim())
                  .filter(Boolean)
              " /></label
          ><button
            type="button"
            :disabled="draft.rules.length <= 1"
            @click="draft.rules.splice(index, 1)"
          >
            移除此规则
          </button>
        </fieldset>
        <div class="sales-row">
          <button
            type="button"
            :disabled="draft.rules.length >= 15"
            @click="addRule"
          >
            添加规则</button
          ><button class="primary" :disabled="busy">
            发布{{ draft.base_id ? "新版本" : "评分卡" }}
          </button>
        </div>
      </form>
      <div v-for="card in cards" :key="card.id" class="sales-panel">
        <div class="sales-row">
          <h3>{{ card.body.name }} · v{{ card.version }}</h3>
          <button :disabled="busy" @click="editCard(card)">
            以此为基础修订
          </button>
        </div>
        <p>{{ card.body.stage }} · {{ card.body.rules.length }} 项规则</p>
        <details>
          <summary>查看已发布内容</summary>
          <p>产品资料：{{ card.body.product_notes || "未提供" }}</p>
          <p v-for="rule in card.body.rules" :key="rule.id">
            {{ rule.title }} · 权重 {{ rule.weight }} · {{ rule.criterion }}
          </p>
        </details>
      </div>
    </template>

    <aside
      v-if="evidence.length"
      class="sales-evidence"
      aria-label="销售原文依据"
    >
      <div class="sales-row">
        <h2>核对原文</h2>
        <button @click="evidence = []">关闭原文</button>
      </div>
      <p class="muted">
        冻结会话 v{{ analysis?.snapshot.conversation.version }} ·
        身份由导入者确认
      </p>
      <div v-for="span in selectedEvidence" :key="span.id" class="sales-panel">
        <button
          class="text-button"
          :disabled="span.start == null"
          @click="seek(span)"
        >
          {{ span.id
          }}{{ span.start != null ? ` · ${Math.floor(span.start)}秒` : "" }}
        </button>
        <p>
          {{
            span.speaker === "sales"
              ? "销售"
              : span.speaker === "customer"
                ? "客户"
                : "未知"
          }}：{{ span.text }}
        </p>
      </div>
      <p v-if="!selectedEvidence.length">
        当前原文已修订，请在完整报告中查看冻结依据。
      </p>
    </aside>
  </section>
</template>

<style scoped>
.sales-workspace {
  max-width: 1160px;
  margin: auto;
}
.sales-notice {
  padding: 14px 18px;
  background: #eaf3ee;
  border-radius: 10px;
  line-height: 1.8;
  margin: 15px 0;
}
.sales-tabs {
  display: flex;
  gap: 8px;
  margin: 24px 0;
  flex-wrap: wrap;
}
.sales-tabs button {
  border: 1px solid #dce6df;
  padding: 11px 20px;
  background: white;
  border-radius: 9px;
}
.sales-tabs button.active {
  background: #176354;
  color: white;
}
.sales-panel {
  border: 1px solid #dfe8e2;
  border-radius: 12px;
  padding: 22px;
  margin: 16px 0;
  background: white;
  overflow-wrap: anywhere;
}
.sales-panel h2,
.sales-panel h3 {
  margin-top: 0;
}
.sales-panel p {
  line-height: 1.8;
}
.sales-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}
.sales-row h3 {
  margin: 0;
}
.sales-controls {
  display: flex;
  align-items: end;
  gap: 14px;
  flex-wrap: wrap;
  margin-top: 16px;
}
.sales-controls label {
  flex: 1;
  min-width: 140px;
}
.sales-form-grid,
.sales-people {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 16px;
}
.sales-workspace label {
  display: grid;
  gap: 7px;
  margin: 12px 0;
  color: #577064;
}
.sales-workspace input:not([type="checkbox"]),
.sales-workspace select,
.sales-workspace textarea {
  width: 100%;
  min-width: 0;
  padding: 10px 12px;
  border: 1px solid #d7e2db;
  border-radius: 7px;
  background: #fcfdfc;
  color: #233c37;
}
.sales-workspace textarea {
  resize: vertical;
  min-height: 70px;
}
.sales-workspace button.primary {
  padding: 11px 18px;
  border: 1px solid #176354;
  border-radius: 8px;
  background: #176354;
  color: white;
  font-weight: 600;
}
.sales-workspace button.primary:hover {
  background: #104f43;
}
.sales-workspace button:not(.primary):not(.text-button):not(.sales-call) {
  padding: 9px 14px;
  border: 1px solid #d7e2db;
  border-radius: 7px;
  background: white;
}
.sales-workspace .sales-tabs button.active:not(.primary) {
  background: #176354;
  color: white;
}
.sales-workspace .sales-check {
  display: flex;
  align-items: center;
  gap: 10px;
  line-height: 1.7;
}
.sales-check input {
  flex: 0 0 auto;
  width: 17px;
  height: 17px;
}
.sales-call {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 15px;
  width: 100%;
  border: 1px solid #dfe8e2;
  padding: 20px;
  margin: 12px 0;
  background: white;
  text-align: left;
  border-radius: 10px;
}
.sales-call:hover {
  border-color: #85b29d;
}
.sales-call strong,
.sales-call small {
  display: block;
}
.sales-call small {
  color: #7c9083;
  margin-top: 8px;
}
.sales-badge {
  display: inline-block;
  flex-shrink: 0;
  background: #eff4f0;
  border-radius: 6px;
  padding: 5px 9px;
  font-size: 12px;
}
.sales-stats {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
  margin-top: 20px;
}
.sales-stats > div {
  border: 1px solid #dfe8e2;
  border-radius: 12px;
  padding: 22px;
  background: white;
}
.sales-stats span {
  display: block;
  color: #768d7f;
}
.sales-stats strong,
.sales-number {
  display: block;
  font-size: 34px;
  margin-top: 12px;
  color: #176354;
}
.sales-stats small,
.sales-number small {
  font-size: 13px;
  font-weight: 400;
  color: #7b9183;
}
.sales-review-fields {
  background: #f6f9f6;
  border-radius: 8px;
  padding: 12px 16px;
  margin-top: 14px;
}
.sales-guidance {
  border-left: 3px solid #a5c8b3;
  padding-left: 12px;
  color: #527360;
}
.sales-rule {
  border: 1px solid #dae5dd;
  border-radius: 9px;
  margin: 20px 0;
  padding: 18px;
  min-width: 0;
}
.sales-span-edit {
  display: grid;
  grid-template-columns: 85px 100px minmax(0, 1fr);
  gap: 12px;
  align-items: center;
  border-bottom: 1px solid #edf1ee;
  padding: 12px 0;
  line-height: 1.7;
}
.sales-evidence {
  position: fixed;
  right: 0;
  top: 0;
  bottom: 0;
  width: min(460px, 100vw);
  background: #f6f9f6;
  box-shadow: -10px 0 50px #173b3320;
  z-index: 30;
  padding: 28px;
  overflow-y: auto;
}
.sales-empty {
  text-align: center;
  padding: 45px 22px;
  color: #789080;
}
.sales-link {
  color: #176354;
  text-decoration: underline;
  padding: 10px;
}
.sales-workspace pre {
  white-space: pre-wrap;
  font-size: 12px;
}
.sales-workspace summary {
  cursor: pointer;
  line-height: 1.8;
  color: #527360;
}
.sales-workspace audio {
  width: 100%;
  margin: 10px 0;
}
@media (max-width: 650px) {
  .sales-form-grid,
  .sales-people {
    grid-template-columns: 1fr;
  }
  .sales-stats {
    gap: 7px;
  }
  .sales-stats > div {
    padding: 14px 10px;
  }
  .sales-stats strong {
    font-size: 26px;
  }
  .sales-stats span {
    font-size: 11px;
  }
  .sales-panel {
    padding: 16px;
  }
  .sales-span-edit {
    grid-template-columns: 70px 90px;
  }
  .sales-span-edit > span {
    grid-column: 1 / -1;
  }
  .sales-call {
    align-items: start;
    flex-direction: column;
  }
}
</style>
