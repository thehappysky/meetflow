<script setup lang="ts">
import {
  computed,
  onBeforeUnmount,
  onMounted,
  reactive,
  ref,
  watch,
} from "vue";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronRight,
  ClipboardList,
  Download,
  FileText,
  FolderOpen,
  Layers3,
  LoaderCircle,
  MessageSquareText,
  Plus,
  Search,
  ShieldCheck,
  Sparkles,
  UploadCloud,
  X,
} from "@lucide/vue";
import { api, post } from "./api";

type Row = Record<string, any>;
const page = ref("meetings");
const projects = ref<Row[]>([]),
  meetings = ref<Row[]>([]),
  tasks = ref<Row[]>([]),
  sources = ref<Row[]>([]);
const projectId = ref(""),
  health = ref<Row>({}),
  detail = ref<Row | null>(null),
  events = ref<Row[]>([]);
const error = ref(""),
  notice = ref(""),
  busy = ref(false),
  modal = ref(false),
  search = ref("");
const activeTab = ref("minutes"),
  selected = ref<string[]>([]),
  evidenceRefs = ref<string[]>([]),
  showTrace = ref(false);
const edits = reactive<Record<string, Row>>({}),
  answers = reactive<Record<string, string>>({});
const form = reactive({
  title: "",
  transcript: "",
  meeting_time: "",
  mode: "demo",
});
const sourceForm = reactive({ title: "", text: "", kind: "requirement" });
const projectName = ref("");
const audioFile = ref<File | null>(null);
const audioPlayer = ref<HTMLAudioElement | null>(null);
let subscription: EventSource | null = null;
let activeRunId = "";
let detailRefreshTimer: number | undefined;
let approvalKey = { signature: "", key: "" };
const statusNames: Record<string, string> = {
  queued: "等待处理",
  running: "处理中",
  waiting_review: "待确认",
  approved: "记录中",
  completed: "已完成",
  failed: "处理失败",
  cancelled: "已停止",
};
const taskStatus: Record<string, string> = {
  todo: "待开始",
  in_progress: "进行中",
  blocked: "有阻塞",
  paused: "已暂停",
  done: "已完成",
  cancelled: "已取消",
};
const roleNames: Record<string, string> = {
  coordinator: "协作协调",
  meeting: "会议理解",
  context: "项目上下文",
  planner: "执行规划",
  risk: "风险与依赖",
  auditor: "证据审查",
};
const filteredMeetings = computed(() =>
  meetings.value.filter(
    (item) =>
      (!projectId.value || item.project_id === projectId.value) &&
      item.title.toLowerCase().includes(search.value.toLowerCase()),
  ),
);
const filteredTasks = computed(() =>
  tasks.value.filter(
    (item) => !projectId.value || item.project_id === projectId.value,
  ),
);
const report = computed(() => detail.value?.report?.body);
const currentProject = computed(
  () =>
    projects.value.find((item) => item.id === projectId.value)?.name ||
    "个人项目",
);
const pending = computed(
  () =>
    filteredMeetings.value.filter(
      (item) => item.run?.status === "waiting_review",
    ).length,
);
const isProcessing = computed(
  () =>
    detail.value &&
    ["queued", "running", "approved"].includes(detail.value.run.status),
);

async function perform(action: () => Promise<void>) {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  notice.value = "";
  try {
    await action();
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : "操作未完成，请重试";
  } finally {
    busy.value = false;
  }
}
async function refresh() {
  const [p, m, t] = await Promise.all([
    api("/projects"),
    api("/meetings"),
    api("/tasks"),
  ]);
  projects.value = p;
  meetings.value = m;
  tasks.value = t;
  if (!projectId.value || !p.some((item: Row) => item.id === projectId.value))
    projectId.value = p[0]?.id || "";
}
async function loadDetail(id: string) {
  const next = await api(`/runs/${id}`);
  if (activeRunId !== id) return;
  if (
    detail.value?.run.id === id &&
    detail.value.run.updated_at > next.run.updated_at
  )
    return;
  const changed =
    detail.value?.report?.content_hash !== next.report?.content_hash ||
    detail.value?.run?.id !== id;
  detail.value = next;
  if (changed && next.report) {
    selected.value = next.report.body.actions
      .filter(
        (item: Row) =>
          !next.report.body.challenges.some(
            (issue: Row) =>
              issue.severity === "blocking" &&
              (!issue.action_id || issue.action_id === item.id),
          ),
      )
      .map((item: Row) => item.id);
    Object.keys(edits).forEach((key) => delete edits[key]);
    Object.keys(answers).forEach((key) => delete answers[key]);
    next.report.body.actions.forEach((item: Row) => {
      edits[item.id] = {
        title: item.title,
        owner: item.owner || "",
        due: item.due || "",
      };
    });
    approvalKey = { signature: "", key: "" };
  }
  if (next.approval) {
    selected.value = next.approval.body.applied.map(
      (item: Row) => item.action_id,
    );
    for (const change of next.approval.body.changes) {
      edits[change.after.id] = {
        title: change.after.title,
        owner: change.after.owner || "",
        due: change.after.due || "",
      };
    }
  }
}
function originalTask(action: Row) {
  return detail.value?.run.manifest.tasks.find(
    (task: Row) => task.id === action.target_task_id,
  );
}
async function openRun(id: string) {
  activeRunId = id;
  subscription?.close();
  evidenceRefs.value = [];
  activeTab.value = "minutes";
  showTrace.value = false;
  await loadDetail(id);
  events.value = await api(`/runs/${id}/event-log`);
  window.location.hash = `run/${id}`;
  if (isProcessing.value) connect(id);
}
function connect(id: string) {
  if (activeRunId !== id) return;
  subscription?.close();
  const cursor = events.value.at(-1)?.id || 0;
  subscription = new EventSource(`/api/runs/${id}/events?after=${cursor}`);
  subscription.onmessage = (event) => {
    const value = JSON.parse(event.data);
    if (value.id && !events.value.some((item) => item.id === value.id))
      events.value.push(value);
    if (!detailRefreshTimer) {
      detailRefreshTimer = window.setTimeout(async () => {
        detailRefreshTimer = undefined;
        if (activeRunId !== id) return;
        try {
          await loadDetail(id);
          if (!isProcessing.value) {
            subscription?.close();
            await refresh();
          }
        } catch {
          /* EventSource reconnect will re-read persisted state. */
        }
      }, 150);
    }
  };
  subscription.onerror = () => {
    subscription?.close();
    loadDetail(id)
      .then(() => {
        if (activeRunId === id && isProcessing.value)
          window.setTimeout(() => connect(id), 2000);
      })
      .catch(() => {});
  };
}
function back() {
  activeRunId = "";
  subscription?.close();
  detail.value = null;
  evidenceRefs.value = [];
  window.location.hash = "";
  refresh().catch(() => {});
}
function changePage(next: string) {
  back();
  page.value = next;
  error.value = "";
}
async function createMeeting() {
  await perform(async () => {
    const payload = {
      project_id: projectId.value,
      title: form.title || "未命名会议",
      transcript: form.transcript,
      meeting_time: form.meeting_time
        ? new Date(form.meeting_time).toISOString()
        : null,
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      mode: form.mode,
    };
    let result;
    if (audioFile.value) {
      const upload = new FormData();
      upload.append("file", audioFile.value);
      for (const [key, value] of Object.entries(payload)) {
        if (key !== "transcript" && value !== null)
          upload.append(key, String(value));
      }
      result = await api("/meetings/audio", { method: "POST", body: upload });
    } else {
      result = await post("/meetings", payload);
    }
    modal.value = false;
    form.transcript = "";
    form.title = "";
    audioFile.value = null;
    await refresh();
    await openRun(result.run.id);
  });
}
async function loadDemo() {
  await perform(async () => {
    const result = await post("/demo");
    projectId.value = result.run.project_id;
    modal.value = false;
    page.value = "meetings";
    await refresh();
    await openRun(result.run.id);
  });
}
async function readFile(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0];
  if (!file) return;
  if (file.size > 1024 * 1024) {
    error.value = "文字文件请小于 1 MB";
    return;
  }
  form.transcript = await file.text();
  audioFile.value = null;
  if (!form.title) form.title = file.name.replace(/\.[^.]+$/, "");
}
function readAudio(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0];
  if (!file) return;
  if (file.size > 100 * 1024 * 1024) {
    error.value = "当前录音请小于 100 MB";
    return;
  }
  audioFile.value = file;
  form.transcript = "";
  if (!form.title) form.title = file.name.replace(/\.[^.]+$/, "");
}
function playEvidence(time: number) {
  if (audioPlayer.value) {
    audioPlayer.value.currentTime = time;
    audioPlayer.value.play().catch(() => {});
  }
}
async function confirmReview() {
  await perform(async () => {
    const body: Row = {
      revision: detail.value!.report.revision,
      report_hash: detail.value!.report.content_hash,
      selected_actions: selected.value,
      edits: Object.fromEntries(
        selected.value.map((id) => [
          id,
          {
            title: edits[id].title,
            owner: edits[id].owner || null,
            due: edits[id].due || null,
          },
        ]),
      ),
      answers: { ...answers },
    };
    const signature = JSON.stringify(body);
    if (approvalKey.signature !== signature)
      approvalKey = { signature, key: crypto.randomUUID() };
    await post(`/runs/${detail.value!.run.id}/review`, {
      ...body,
      idempotency_key: approvalKey.key,
    });
    await loadDetail(detail.value!.run.id);
    await refresh();
    notice.value = "所选变更已保存，未选择的事项不会创建任务。";
    if (isProcessing.value) connect(detail.value!.run.id);
  });
}
async function retry() {
  await perform(async () => {
    await post(`/runs/${detail.value!.run.id}/retry`);
    await loadDetail(detail.value!.run.id);
    connect(detail.value!.run.id);
  });
}
async function stopRun() {
  const id = detail.value?.run.id;
  if (!id) return;
  await perform(async () => {
    await post(`/runs/${id}/cancel`);
    notice.value = "已请求停止，正在等待在途处理结束";
  });
}
async function reanalyze() {
  await perform(async () => {
    const run = await post(`/meetings/${detail.value!.meeting.id}/reanalyze`);
    await refresh();
    await openRun(run.id);
  });
}
async function setTaskStatus(task: Row, event: Event) {
  const next = (event.target as HTMLSelectElement).value;
  await perform(async () => {
    await api(`/tasks/${task.id}`, {
      method: "PATCH",
      body: JSON.stringify({ version: task.version, status: next }),
    });
    await refresh();
    notice.value = "任务状态已保存，变更已留痕。";
  });
}
async function loadSources() {
  if (projectId.value)
    sources.value = await api(`/projects/${projectId.value}/sources`);
}
async function saveSource() {
  await perform(async () => {
    await post(`/projects/${projectId.value}/sources`, sourceForm);
    sourceForm.text = "";
    sourceForm.title = "";
    await loadSources();
    notice.value = "资料已保存，后续分析将使用这个版本。现有分析快照保持不变。";
  });
}
async function createProject() {
  await perform(async () => {
    const project = await post("/projects", { name: projectName.value });
    projectName.value = "";
    await refresh();
    projectId.value = project.id;
  });
}
async function showAgenda() {
  await perform(async () => {
    const value = await api(`/projects/${projectId.value}/agenda`);
    notice.value = value.items.length
      ? `下次会议可核对 ${value.items.length} 项未完成任务：${value.items.map((item: Row) => item.text).join("；")}`
      : "当前没有未完成任务。";
  });
}
watch(projectId, () => {
  if (page.value === "projects") loadSources().catch(() => {});
});
watch(page, () => {
  if (page.value === "projects") loadSources().catch(() => {});
});
onMounted(async () => {
  await perform(async () => {
    health.value = await api("/health");
    await refresh();
    const id = window.location.hash.match(/^#run\/(r_[a-f0-9]+)$/)?.[1];
    if (id) await openRun(id);
  });
});
onBeforeUnmount(() => subscription?.close());
</script>

<template>
  <div class="shell">
    <aside class="sidebar">
      <a class="brand" href="#" @click.prevent="changePage('meetings')"
        ><span class="brand-symbol"><Layers3 :size="23" /></span
        ><span>MeetFlow<small>让讨论有后续</small></span></a
      >
      <div class="nav-caption">工作空间</div>
      <nav aria-label="主导航">
        <button
          :class="{ active: page === 'meetings' }"
          @click="changePage('meetings')"
        >
          <MessageSquareText :size="19" />会议<ChevronRight
            class="nav-arrow"
            :size="14"
          />
        </button>
        <button
          :class="{ active: page === 'tasks' }"
          @click="changePage('tasks')"
        >
          <ClipboardList :size="19" />任务看板
        </button>
        <button
          :class="{ active: page === 'projects' }"
          @click="changePage('projects')"
        >
          <FolderOpen :size="19" />项目资料
        </button>
      </nav>
      <div class="sidebar-note">
        <ShieldCheck :size="18" /><strong>每个结论，都有出处</strong>
        <p>保留会议依据，确认后再更新任务。</p>
      </div>
      <div class="profile">
        <span class="avatar">我</span>
        <div>本地工作空间<small>单用户开发版本 · v0.1</small></div>
      </div>
    </aside>
    <main>
      <header class="topbar">
        <span class="breadcrumb"
          >工作空间 <ChevronRight :size="13" />
          {{
            detail
              ? "会议详情"
              : page === "meetings"
                ? "会议"
                : page === "tasks"
                  ? "任务看板"
                  : "项目资料"
          }}</span
        ><select aria-label="当前项目" v-model="projectId">
          <option
            v-for="project in projects"
            :key="project.id"
            :value="project.id"
          >
            {{ project.name }}
          </option>
        </select>
      </header>
      <div class="content">
        <div v-if="error" class="alert error" role="alert">
          {{ error
          }}<button aria-label="关闭错误" @click="error = ''">
            <X :size="16" />
          </button>
        </div>
        <div v-if="notice" class="alert success" role="status">
          {{ notice
          }}<button aria-label="关闭提示" @click="notice = ''">
            <X :size="16" />
          </button>
        </div>

        <template v-if="detail">
          <button class="back-button" @click="back">
            <ArrowLeft :size="16" />返回会议
          </button>
          <div class="page-heading">
            <div>
              <div class="eyebrow">MEETING WORKSPACE</div>
              <h1>{{ detail.meeting.title }}</h1>
              <p>
                {{
                  detail.meeting.meeting_time
                    ? new Date(detail.meeting.meeting_time).toLocaleString(
                        "zh-CN",
                      )
                    : "会议实际时间待确认"
                }}
                · {{ currentProject }}
              </p>
            </div>
            <span class="status" :class="detail.run.status">{{
              statusNames[detail.run.status]
            }}</span>
          </div>
          <div v-if="detail.run.mode === 'demo'" class="demo-banner">
            <Sparkles :size="17" /><span
              ><strong>离线规则演示</strong> ·
              当前未调用大模型，用于体验流程；内容须人工核对。</span
            >
          </div>
          <div v-if="isProcessing" class="processing card">
            <LoaderCircle class="spin" :size="22" />
            <div>
              <strong>{{ detail.run.phase }}</strong>
              <p>后台正在处理，你可以离开页面，结果会保留。</p>
            </div>
            <button class="button subtle" @click="stopRun">停止处理</button>
          </div>
          <div v-if="detail.run.status === 'failed'" class="card failed">
            <h3>处理未完成</h3>
            <p>{{ detail.run.error }}</p>
            <div class="button-row">
              <button class="button primary" :disabled="busy" @click="retry">
                恢复处理</button
              ><button class="button" :disabled="busy" @click="reanalyze">
                新建分析
              </button>
            </div>
          </div>
          <div
            v-if="report"
            class="meeting-grid"
            :class="{ 'has-evidence': evidenceRefs.length }"
          >
            <div>
              <div class="tabs">
                <button
                  :class="{ active: activeTab === 'minutes' }"
                  @click="activeTab = 'minutes'"
                >
                  会议纪要</button
                ><button
                  :class="{ active: activeTab === 'actions' }"
                  @click="activeTab = 'actions'"
                >
                  任务与变化 <span>{{ report.actions.length }}</span></button
                ><button
                  :class="{ active: activeTab === 'questions' }"
                  @click="activeTab = 'questions'"
                >
                  待确认 <span>{{ report.questions.length }}</span></button
                ><a class="export" :href="`/api/runs/${detail.run.id}/export`"
                  ><Download :size="15" />导出</a
                >
              </div>
              <section v-if="activeTab === 'minutes'" class="card report-card">
                <div class="section-title">
                  <FileText :size="19" />
                  <h2>本次讨论</h2>
                  <span class="muted-tag">{{
                    detail.run.status === "completed"
                      ? "已记录任务变更"
                      : "待核对"
                  }}</span>
                </div>
                <p class="summary-text">
                  {{ report.summary || "本次未提取到摘要。" }}
                </p>
                <h3>结论与依据</h3>
                <div
                  v-for="(claim, index) in report.claims"
                  :key="index"
                  class="claim"
                >
                  <span class="claim-dot"></span>
                  <p>{{ claim.text }}</p>
                  <button
                    class="text-button"
                    @click="evidenceRefs = claim.evidence"
                  >
                    原文 <ArrowRight :size="13" />
                  </button>
                </div>
                <div v-if="report.risks.length" class="risk-box">
                  <h3>需要留意</h3>
                  <button
                    v-for="(risk, index) in report.risks"
                    :key="index"
                    @click="evidenceRefs = risk.evidence"
                  >
                    {{ risk.text }}<ChevronRight :size="14" />
                  </button>
                </div>
              </section>
              <section v-if="activeTab === 'actions'" class="action-section">
                <div class="section-intro">
                  <h2>让讨论落到下一步</h2>
                  <p>
                    核对负责人、日期和执行条件，确认所选变更后写入本地看板。
                  </p>
                </div>
                <div v-if="!report.actions.length" class="card empty-state">
                  本次未识别到行动项。纪要仍可查看和保存。
                </div>
                <article
                  v-for="action in report.actions"
                  :key="action.id"
                  class="card action-card"
                >
                  <label class="action-title"
                    ><input
                      type="checkbox"
                      :value="action.id"
                      v-model="selected"
                      :disabled="detail.run.status !== 'waiting_review'"
                    /><strong>{{ action.title }}</strong></label
                  ><span class="muted-tag">{{
                    action.target_task_id ? "修改已有任务" : "新增候选"
                  }}</span>
                  <div class="action-fields">
                    <label style="grid-column: 1/-1"
                      >任务标题<input
                        v-model="edits[action.id].title"
                        :disabled="detail.run.status !== 'waiting_review'"
                    /></label>
                    <label
                      >负责人<input
                        v-model="edits[action.id].owner"
                        placeholder="待分配"
                        :disabled="
                          detail.run.status !== 'waiting_review'
                        " /></label
                    ><label
                      >日期<input
                        type="date"
                        v-model="edits[action.id].due"
                        :disabled="detail.run.status !== 'waiting_review'"
                    /></label>
                  </div>
                  <p v-if="originalTask(action)" class="muted">
                    原日期：{{ originalTask(action).due || "未设置" }} →
                    建议日期：{{ edits[action.id].due || "待确认" }}；状态：{{
                      taskStatus[originalTask(action).status]
                    }}
                    → {{ taskStatus[action.status] }}
                  </p>
                  <p v-if="detail.approval" class="muted">
                    {{
                      selected.includes(action.id)
                        ? "本项变更已确认，字段显示实际保存内容。"
                        : "本项未执行，仍为候选。"
                    }}
                  </p>
                  <div v-if="action.conditions.length" class="conditions">
                    <strong>执行条件</strong>
                    <p v-for="condition in action.conditions" :key="condition">
                      {{ condition }}
                    </p>
                  </div>
                  <div class="action-footer">
                    <span
                      >{{ taskStatus[action.status] }} ·
                      {{ action.rationale }}</span
                    ><button
                      class="text-button"
                      @click="evidenceRefs = action.evidence"
                    >
                      核对依据 <ArrowRight :size="13" />
                    </button>
                  </div>
                </article>
                <div
                  v-for="(issue, index) in report.challenges"
                  :key="index"
                  class="alert"
                  :class="issue.severity === 'blocking' ? 'error' : 'warning'"
                >
                  {{ issue.text
                  }}<button
                    class="text-button"
                    @click="evidenceRefs = issue.evidence"
                  >
                    看依据
                  </button>
                </div>
              </section>
              <section
                v-if="activeTab === 'questions'"
                class="card report-card"
              >
                <h2>需要你判断的部分</h2>
                <p class="muted">
                  同一问题集中处理；暂时不能确认，可以保留为空。
                </p>
                <div v-if="!report.questions.length" class="empty-state">
                  <CheckCircle2 :size="28" />
                  <p>当前没有集中澄清项。请继续核对任务变化。</p>
                </div>
                <div
                  v-for="question in report.questions"
                  :key="question.key"
                  class="question"
                >
                  <h3>{{ question.text }}</h3>
                  <input
                    v-model="answers[question.key]"
                    placeholder="输入确认结果，或留空稍后处理"
                    :disabled="detail.run.status !== 'waiting_review'"
                  />
                  <div class="button-row">
                    <button
                      v-for="option in question.options"
                      :key="option"
                      class="button small"
                      @click="answers[question.key] = option"
                    >
                      {{ option }}</button
                    ><button
                      class="text-button"
                      @click="evidenceRefs = question.evidence"
                    >
                      查看原文
                    </button>
                  </div>
                </div>
              </section>
              <div
                v-if="detail.run.status === 'waiting_review'"
                class="review-bar"
              >
                <div>
                  <strong>{{
                    selected.length
                      ? `已选择 ${selected.length} 项任务变化`
                      : "暂不更新任务"
                  }}</strong
                  ><small>请核对所选内容，未选择项不会执行。</small>
                </div>
                <button
                  class="button primary"
                  :disabled="busy"
                  @click="confirmReview"
                >
                  <Check :size="16" />{{
                    selected.length ? "确认所选变更" : "仅保存纪要"
                  }}
                </button>
              </div>
              <div v-if="detail.run.status === 'completed'" class="completion">
                <CheckCircle2 :size="20" />
                <div>
                  <strong>本次确认已留痕</strong>
                  <p>
                    已保存
                    {{ detail.approval?.body.applied.length || 0 }}
                    项任务变化，可以到看板继续跟进。
                  </p>
                </div>
                <button class="button" @click="changePage('tasks')">
                  查看任务 <ArrowRight :size="15" />
                </button>
              </div>
              <button class="trace-toggle" @click="showTrace = !showTrace">
                <Layers3 :size="15" />{{ showTrace ? "收起" : "查看" }}协作记录
                · {{ detail.artifacts.length }} 份角色产物 ·
                {{ detail.run.model_calls }} 次{{
                  detail.run.mode === "demo" ? "规则决策" : "模型调用"
                }}
              </button>
              <div v-if="showTrace" class="card trace">
                <article
                  v-for="artifact in detail.artifacts"
                  :key="artifact.id"
                >
                  <strong>{{ roleNames[artifact.role] }}</strong
                  ><small>{{ artifact.task_key }}</small>
                  <p>{{ artifact.body.summary }}</p>
                </article>
                <details>
                  <summary>查看工具调用与事件</summary>
                  <pre>{{ JSON.stringify(events, null, 2) }}</pre>
                </details>
              </div>
            </div>
            <aside v-if="evidenceRefs.length" class="card evidence-panel">
              <div class="section-title">
                <h3>核对原文</h3>
                <button
                  class="icon-button"
                  aria-label="关闭原文"
                  @click="evidenceRefs = []"
                >
                  <X :size="17" />
                </button>
              </div>
              <audio
                v-if="detail.meeting.audio_path"
                ref="audioPlayer"
                controls
                :src="`/api/meetings/${detail.meeting.id}/audio`"
                style="width: 100%; margin-bottom: 12px"
              />
              <article v-for="reference in evidenceRefs" :key="reference">
                <template v-if="detail.evidence[reference]"
                  ><small
                    >{{ detail.evidence[reference].source_title }} · 版本
                    {{ detail.evidence[reference].source_version }}</small
                  >
                  <blockquote>{{ detail.evidence[reference].text }}</blockquote>
                  <button
                    v-if="
                      detail.meeting.audio_path &&
                      detail.evidence[reference].start !== null
                    "
                    class="text-button"
                    @click="playEvidence(detail.evidence[reference].start)"
                  >
                    播放此处录音 ·
                    {{ Math.floor(detail.evidence[reference].start) }} 秒
                  </button>
                  <code>{{ reference }}</code></template
                >
                <p v-else>引用暂不可用。</p>
              </article>
            </aside>
          </div>
          <div
            v-else-if="!isProcessing && detail.run.status !== 'failed'"
            class="card empty-state"
          >
            当前尚无报告。
          </div>
        </template>

        <template v-else-if="page === 'meetings'">
          <div class="page-heading">
            <div>
              <div class="eyebrow">FROM CONVERSATION TO ACTION</div>
              <h1>每一次讨论，都有下一步。</h1>
              <p>整理会议，核对承诺，让重要的事持续向前。</p>
            </div>
            <button class="button primary" @click="modal = true">
              <Plus :size="18" />新建会议
            </button>
          </div>
          <section class="welcome-card">
            <div>
              <span class="soft-label"
                ><Sparkles :size="14" />会议协作工作台</span
              >
              <h2>从一份会议记录开始</h2>
              <p>
                导入文字，得到有原文依据的纪要、任务变化与待确认事项。<br />关联项目资料后，继续核对历史安排和执行条件。
              </p>
              <div class="button-row">
                <button class="button primary" @click="modal = true">
                  <UploadCloud :size="17" />导入会议</button
                ><button
                  class="button white"
                  :disabled="busy"
                  @click="loadDemo"
                >
                  体验示例 <ArrowRight :size="16" />
                </button>
              </div>
            </div>
            <div class="welcome-visual" aria-hidden="true">
              <div class="visual-note">
                <span class="visual-line"></span
                ><span class="visual-line short"></span>
                <div><CheckCircle2 :size="19" /><span>结论有依据</span></div>
                <div><ClipboardList :size="19" /><span>承诺有跟进</span></div>
                <div><ShieldCheck :size="19" /><span>变更先确认</span></div>
              </div>
              <span class="visual-bubble"><Layers3 :size="24" /></span>
            </div>
          </section>
          <div class="stat-row">
            <div class="stat">
              <FileText :size="20" />
              <div>
                <strong>{{ filteredMeetings.length }}</strong
                ><span>场会议</span>
              </div>
            </div>
            <div class="stat">
              <MessageSquareText :size="20" />
              <div>
                <strong>{{ pending }}</strong
                ><span>场待确认</span>
              </div>
            </div>
            <div class="stat">
              <ClipboardList :size="20" />
              <div>
                <strong>{{
                  filteredTasks.filter(
                    (item) => !["done", "cancelled"].includes(item.status),
                  ).length
                }}</strong
                ><span>项待跟进</span>
              </div>
            </div>
          </div>
          <div class="list-heading">
            <h2>最近会议</h2>
            <label class="search"
              ><Search :size="16" /><input
                v-model="search"
                placeholder="搜索会议标题"
            /></label>
          </div>
          <div v-if="!filteredMeetings.length" class="card empty-state">
            <FileText :size="34" />
            <h3>这里将留住你的讨论与决定</h3>
            <p>导入第一场会议，或先体验一份明确标记的离线示例。</p>
            <button class="text-button" @click="loadDemo">
              打开示例 <ArrowRight :size="14" />
            </button>
          </div>
          <div class="meeting-list">
            <button
              v-for="meeting in filteredMeetings"
              :key="meeting.id"
              class="meeting-row"
              @click="perform(() => openRun(meeting.run.id))"
            >
              <span class="meeting-icon"><FileText :size="22" /></span>
              <div>
                <strong>{{ meeting.title }}</strong
                ><small
                  >{{
                    meeting.meeting_time
                      ? new Date(meeting.meeting_time).toLocaleString("zh-CN")
                      : "实际会议时间待确认"
                  }}
                  ·
                  {{
                    meeting.run?.mode === "demo" ? "离线规则演示" : "真实模型"
                  }}</small
                >
              </div>
              <span class="status" :class="meeting.run?.status">{{
                statusNames[meeting.run?.status]
              }}</span
              ><ChevronRight :size="17" />
            </button>
          </div>
          <p class="footnote">
            本地开发版 ·
            未配置大模型也可体验规则演示，规则结果不代表模型效果。录音需配置本地
            ASR；团队权限仍在开发。
          </p>
        </template>

        <template v-else-if="page === 'tasks'">
          <div class="page-heading">
            <div>
              <div class="eyebrow">KEEP THINGS MOVING</div>
              <h1>让承诺，持续向前。</h1>
              <p>这里展示已确认写入的本地任务，状态修改会保留记录。</p>
            </div>
            <button class="button" :disabled="busy" @click="showAgenda">
              准备下次议程 <ArrowRight :size="16" />
            </button>
          </div>
          <div v-if="!filteredTasks.length" class="card empty-state">
            <ClipboardList :size="34" />
            <h3>还没有任务</h3>
            <p>在会议中确认任务变化后，它们会出现在这里。</p>
          </div>
          <div class="task-grid">
            <article
              v-for="task in filteredTasks"
              :key="task.id"
              class="card task-card"
            >
              <span class="muted-tag">版本 {{ task.version }}</span>
              <h3>{{ task.title }}</h3>
              <p>
                {{ task.owner || "待分配" }}<span class="separator">·</span
                >{{ task.due || "日期待确认" }}
              </p>
              <div v-if="task.conditions.length" class="conditions">
                <p v-for="condition in task.conditions" :key="condition">
                  {{ condition }}
                </p>
              </div>
              <div class="task-bottom">
                <select
                  :value="task.status"
                  :disabled="busy"
                  aria-label="任务状态"
                  @change="setTaskStatus(task, $event)"
                >
                  <option
                    v-for="(label, key) in taskStatus"
                    :key="key"
                    :value="key"
                  >
                    {{ label }}
                  </option></select
                ><span class="muted">本地看板</span>
              </div>
            </article>
          </div>
        </template>

        <template v-else>
          <div class="page-heading">
            <div>
              <div class="eyebrow">CONTEXT THAT STAYS</div>
              <h1>资料关联一次，会议持续复用。</h1>
              <p>项目资料仅供该项目的新分析读取；已经开始的分析保留原快照。</p>
            </div>
          </div>
          <div class="project-grid">
            <section class="card form-card">
              <h2>添加项目资料</h2>
              <label
                >资料名称<input
                  v-model="sourceForm.title"
                  placeholder="例如：发布验收要求 v3" /></label
              ><label
                >资料类型<select v-model="sourceForm.kind">
                  <option value="requirement">需求与规则</option>
                  <option value="history">历史会议</option>
                  <option value="members">成员与称呼</option>
                  <option value="task_snapshot">任务状态说明</option>
                </select></label
              ><label
                >正文<textarea
                  v-model="sourceForm.text"
                  rows="9"
                  placeholder="粘贴已授权使用的项目资料…"
                ></textarea></label
              ><button
                class="button primary"
                :disabled="busy || !sourceForm.title || !sourceForm.text"
                @click="saveSource"
              >
                保存到当前项目
              </button>
              <p class="footnote">
                当前支持文字资料；外部平台连接、PDF 解析和向量检索尚未实现。
              </p>
            </section>
            <section>
              <div class="card form-card">
                <h2>新建项目</h2>
                <div class="inline-form">
                  <input v-model="projectName" placeholder="项目名称" /><button
                    class="button"
                    :disabled="busy || !projectName"
                    @click="createProject"
                  >
                    <Plus :size="17" />创建
                  </button>
                </div>
              </div>
              <h2 class="space-top">已关联资料 · {{ sources.length }}</h2>
              <article
                v-for="source in sources"
                :key="source.id"
                class="card source-card"
              >
                <FileText :size="18" />
                <div>
                  <strong>{{ source.title }}</strong
                  ><small
                    >{{ source.kind }} · {{ source.body.spans.length }} 个片段 ·
                    版本 {{ source.version }}</small
                  >
                </div>
              </article>
            </section>
          </div>
        </template>
      </div>
    </main>
    <div v-if="modal" class="modal-overlay" @click.self="modal = false">
      <section
        class="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-title"
      >
        <div class="section-title">
          <div>
            <span class="eyebrow">NEW MEETING</span>
            <h2 id="create-title">带来讨论，带走下一步。</h2>
          </div>
          <button
            class="icon-button"
            aria-label="关闭新建会议"
            @click="modal = false"
          >
            <X :size="20" />
          </button>
        </div>
        <p class="muted">先导入会议文字即可开始，项目资料不必一次备齐。</p>
        <form @submit.prevent="createMeeting">
          <label
            >会议标题<input
              v-model="form.title"
              placeholder="例如：本周项目例会"
              maxlength="200"
          /></label>
          <div class="two-columns">
            <label
              >实际会议时间（本地时区，可稍后确认）<input
                type="datetime-local"
                v-model="form.meeting_time" /></label
            ><label
              >分析方式<select v-model="form.mode">
                <option value="demo">离线规则演示 · 不调用模型</option>
                <option value="live" :disabled="!health.live_available">
                  真实模型{{ health.live_available ? "" : " · 需先配置服务端" }}
                </option>
              </select></label
            >
          </div>
          <label class="file-picker"
            ><UploadCloud :size="18" /><span>读取 .txt / .md 文件</span
            ><input type="file" accept=".txt,.md" @change="readFile"
          /></label>
          <label class="file-picker"
            ><UploadCloud :size="18" /><span>{{
              audioFile
                ? audioFile.name
                : health.audio_available
                  ? "上传会议录音"
                  : "录音上传 · 需先配置本地 ASR"
            }}</span
            ><input
              type="file"
              accept=".wav,.mp3,.m4a,.flac,.ogg,.webm"
              :disabled="!health.audio_available"
              @change="readAudio"
          /></label>
          <button
            v-if="audioFile"
            type="button"
            class="text-button"
            @click="audioFile = null"
          >
            移除录音，改用文字
          </button>
          <label
            >会议文字<textarea
              v-model="form.transcript"
              rows="9"
              placeholder="按发言换行，例如：&#10;李明：接口改造我来跟进，下周三交付。&#10;王芳：发布前必须等压测通过。"
              :required="!audioFile"
              :disabled="!!audioFile"
              minlength="2"
              maxlength="200000"
            ></textarea>
          </label>
          <p class="footnote">
            当前版本先走通文字到审核任务的闭环。未填写实际时间时，相对日期会保留为待确认。
          </p>
          <div class="modal-actions">
            <button type="button" class="button subtle" @click="modal = false">
              稍后再说</button
            ><button
              class="button primary"
              :disabled="
                busy || (!audioFile && form.transcript.trim().length < 2)
              "
            >
              <LoaderCircle v-if="busy" class="spin" :size="16" />开始整理
              <ArrowRight :size="16" />
            </button>
          </div>
        </form>
      </section>
    </div>
  </div>
</template>
