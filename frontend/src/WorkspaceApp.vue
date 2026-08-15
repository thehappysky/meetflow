<script setup lang="ts">
import {
  computed,
  onMounted,
  onBeforeUnmount,
  reactive,
  ref,
  watch,
} from "vue";
import LegacyApp from "./App.vue";
import QualityWorkspace from "./QualityWorkspace.vue";
import TrainingRules from "./TrainingRules.vue";
import BusinessWorkspace from "./BusinessWorkspace.vue";
import OpportunityPicker from "./OpportunityPicker.vue";
import ReportsWorkspace from "./ReportsWorkspace.vue";
import MaintenanceWorkspace from "./MaintenanceWorkspace.vue";
import RecordingFilters from "./RecordingFilters.vue";
import "./workspace-controls.css";
import { api, post, NETWORK_ERROR } from "./api";

type Row = Record<string, any>;
const loaded = ref(false),
  legacy = ref(false),
  user = ref<Row | null>(null),
  health = ref<Row>({});
const busy = ref(false),
  error = ref(""),
  notice = ref(""),
  page = ref("home");
const rows = ref<Row[]>([]),
  center = ref<Row[]>([]),
  teams = ref<Row[]>([]),
  accounts = ref<Row[]>([]);
const invites = ref<Row[]>([]),
  corrections = ref<Row[]>([]),
  detail = ref<Row | null>(null);
const centralDetail = ref(false),
  formOpen = ref(false),
  editOpen = ref(false),
  requestOpen = ref(false);
const audioUrl = ref(""),
  inviteLink = ref(""),
  inviteInfo = ref<Row | null>(null),
  joinMode = ref(false);
const selectedTeam = ref(""),
  teamName = ref(""),
  search = ref(""),
  submitCustomer = ref("");
const submitOpportunity = ref<string | null>(null);
const login = reactive({ username: "", password: "" });
const registration = reactive({
  invitation: "",
  username: "",
  name: "",
  password: "",
});
const form = reactive({
  title: "",
  customer: "",
  meeting_date: "",
  transcript: "",
  opportunity_id: null as string | null,
});
const edit = reactive({
  title: "",
  customer: "",
  meeting_date: "",
  reason: "",
  opportunity_id: null as string | null,
  use_personal_revision: false,
});
const transcriptOpen = ref(false),
  transcriptDraft = ref(""),
  transcriptReason = ref(""),
  historicalVersion = ref<Row | null>(null);
const batchSelected = ref<string[]>([]),
  batchConfirmed = ref(false),
  batchMeta = reactive<Row>({}),
  batchErrors = ref<Row[]>([]);
const batchRows = computed(() =>
  rows.value.filter(
    (r) => batchSelected.value.includes(r.id) && batchMeta[r.id],
  ),
);
const batchReady = computed(
  () =>
    batchRows.value.length > 0 &&
    batchRows.value.length <= 30 &&
    batchRows.value.every(
      (r) =>
        batchMeta[r.id]?.opportunity_id &&
        batchMeta[r.id]?.version === r.version,
    ),
);
function toggleBatch(row: Row) {
  if (!batchSelected.value.includes(row.id)) {
    batchMeta[row.id] = {
      opportunity_id: row.body.opportunity_id,
      version: row.version,
      key: crypto.randomUUID(),
    };
    batchSelected.value = [...batchSelected.value, row.id];
  } else {
    batchSelected.value = batchSelected.value.filter((id) => id !== row.id);
    delete batchMeta[row.id];
  }
  batchConfirmed.value = false;
  batchErrors.value = [];
}
async function submitBatch() {
  await perform(async () => {
    const result = await post("/workspace/submissions/batch", {
      items: batchRows.value.map((r) => ({
        recording_id: r.id,
        version: batchMeta[r.id].version,
        opportunity_id: batchMeta[r.id].opportunity_id,
        request_key: batchMeta[r.id].key,
      })),
    });
    const done = new Set(result.succeeded.map((r: Row) => r.recording_id));
    batchSelected.value = batchSelected.value.filter((id) => !done.has(id));
    batchErrors.value = result.failed.map((r: Row) => ({
      ...r,
      title: rows.value.find((row) => row.id === r.recording_id)?.body.title,
    }));
    batchConfirmed.value = false;
    await refresh();
    notice.value = `已上报 ${result.succeeded.length} 场，${result.failed.length} 场需补充或刷新后再试。`;
  });
}
function editTranscript() {
  transcriptDraft.value = detail.value!.transcript_text;
  transcriptReason.value = "";
  transcriptOpen.value = true;
}
async function saveTranscript() {
  await perform(async () => {
    const id = detail.value!.id;
    await post(`/workspace/recordings/${id}/transcript`, {
      version: detail.value!.version,
      text: transcriptDraft.value,
      reason: transcriptReason.value,
    });
    await open(id);
    await refresh();
    notice.value =
      "修订已保存为新转写版本，正在重新整理。原始录音、旧转写和已上报中心不变。";
  });
}
async function viewHistorical(revision: number) {
  await perform(async () => {
    historicalVersion.value = await api(
      `/workspace/center/${detail.value!.id}/versions/${revision}`,
    );
  });
}
const file = ref<File | null>(null),
  checkSubmit = ref(false);
const qualitySource = ref<{ kind: string; id: string } | null>(null);
const removalImpact = ref<Row | null>(null),
  removalReason = ref(""),
  activity = ref<Row[]>([]);
const dateMode = ref("meeting"),
  dateFrom = ref(""),
  dateTo = ref(""),
  ownerFilter = ref(""),
  teamFilter = ref(""),
  statusFilter = ref("");
const questionAnswers = reactive<Row>({});
const evidenceOpen = ref(false),
  evidenceHighlight = ref("");
const audioPlayer = ref<HTMLAudioElement | null>(null);
let timer: number | undefined,
  sequence = 0,
  lastRefresh = 0;
let submitAttempt: { signature: string; key: string } | null = null;
let uploadAttempt: {
  signature: string;
  key: string;
  file: File | null;
} | null = null;
const roleNames: Row = {
  admin: "管理员",
  boss: "老板",
  manager: "主管",
  sales: "销售",
};
watch(
  user,
  (value) => {
    document.title = value
      ? `${value.name} · ${roleNames[value.role]} · MeetFlow`
      : "MeetFlow · 登录工作区";
  },
  { immediate: true },
);
const stateNames: Row = {
  queued: "等待整理",
  running: "正在整理",
  waiting_review: "整理完成",
  completed: "已完成",
  failed: "处理失败",
  cancelled: "已停止",
};
const scope = computed(() =>
  user.value?.role === "admin"
    ? "公司账号与团队设置"
    : user.value?.role === "sales"
      ? "个人工作区"
      : user.value?.role === "manager"
        ? user.value.team
        : "全公司已上报范围",
);
const metadata = computed(() =>
  centralDetail.value ? detail.value?.metadata : detail.value?.body,
);
const report = computed(() =>
  centralDetail.value ? detail.value?.report : detail.value?.report?.body,
);
const visibleCenter = computed(() =>
  center.value.filter(
    (r) =>
      [r.metadata.title, r.metadata.customer, r.owner]
        .join(" ")
        .includes(search.value) &&
      (!ownerFilter.value || r.owner_id === ownerFilter.value) &&
      (!teamFilter.value || r.team_id === teamFilter.value) &&
      matchDates(r.metadata.meeting_date, r.submitted_at),
  ),
);
const visibleRows = computed(() =>
  rows.value.filter(
    (r) =>
      [r.body.title, r.body.customer].join(" ").includes(search.value) &&
      (!statusFilter.value || r.status === statusFilter.value) &&
      matchDates(r.body.meeting_date, r.uploaded_at),
  ),
);
const centerOwners = computed(() =>
  Array.from(
    new Map(
      center.value.map((r) => [r.owner_id, { id: r.owner_id, label: r.owner }]),
    ).values(),
  ),
);
const centerTeams = computed(() =>
  Array.from(
    new Map(
      center.value.map((r) => [r.team_id, { id: r.team_id, label: r.team }]),
    ).values(),
  ),
);
function matchDates(meeting: string, received: string) {
  if (dateMode.value === "unknown") return !meeting;
  const day =
    dateMode.value === "meeting"
      ? meeting
      : new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Shanghai" }).format(
          new Date(received),
        );
  if (!dateFrom.value && !dateTo.value) return true;
  return (
    !!day &&
    (!dateFrom.value || day >= dateFrom.value) &&
    (!dateTo.value || day <= dateTo.value)
  );
}
function clearFilters() {
  search.value = "";
  dateFrom.value = "";
  dateTo.value = "";
  ownerFilter.value = "";
  teamFilter.value = "";
  statusFilter.value = "";
  dateMode.value = "meeting";
}
async function processing(action: string) {
  await perform(async () => {
    await post(
      `/workspace/maintenance/personal/${detail.value!.id}/${action}`,
      { version: detail.value!.version },
    );
    await open(detail.value!.id);
    await refresh();
    notice.value =
      action === "retry"
        ? "已请求从检查点恢复，累计预算保留。"
        : "已请求停止，在途处理结束后生效。";
  });
}
async function showActivity() {
  await perform(async () => {
    activity.value = await api(
      `/workspace/maintenance/${centralDetail.value ? "center" : "personal"}/${detail.value!.id}/activity`,
    );
  });
}
async function previewRemoval() {
  await perform(async () => {
    removalImpact.value = await api(
      `/workspace/maintenance/center/${detail.value!.id}/impact`,
    );
    removalReason.value = "";
  });
}
async function removeCenter() {
  await perform(async () => {
    await post(`/workspace/center/${detail.value!.id}/delete`, {
      revision: removalImpact.value!.version,
      reason: removalReason.value,
    });
    removalImpact.value = null;
    navigate("center");
    await refresh();
    notice.value = "中心资料已撤下，历史保留，可在回收站恢复。";
  });
}
function displayTime(value: string) {
  if (!value) return "时间待确认";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}
watch(
  () => registration.invitation,
  () => {
    inviteInfo.value = null;
  },
);

function clearAudio() {
  if (audioUrl.value) URL.revokeObjectURL(audioUrl.value);
  audioUrl.value = "";
}
function expire() {
  sequence++;
  user.value = null;
  rows.value = [];
  center.value = [];
  detail.value = null;
  clearAudio();
}
async function perform(work: () => Promise<void>) {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  notice.value = "";
  try {
    await work();
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : "操作失败";
  } finally {
    busy.value = false;
  }
}
async function refresh() {
  if (!user.value) return;
  const owner = user.value.id,
    role = user.value.role;
  const data = await Promise.all(
    role === "admin"
      ? [api("/admin/teams"), api("/admin/accounts"), api("/admin/invitations")]
      : [
          api("/workspace/recordings"),
          api("/workspace/center"),
          api("/workspace/corrections"),
        ],
  );
  if (user.value?.id !== owner) return;
  lastRefresh = Date.now();
  if (error.value === NETWORK_ERROR) error.value = "";
  if (role === "admin") {
    teams.value = data[0];
    accounts.value = data[1];
    invites.value = data[2];
    if (!selectedTeam.value) selectedTeam.value = teams.value[0]?.id || "";
  } else {
    rows.value = data[0];
    center.value = data[1];
    corrections.value = data[2];
  }
}
async function signedIn(result: Row) {
  sessionStorage.setItem("meetflow-token", result.token);
  user.value = result.user;
  joinMode.value = false;
  detail.value = null;
  formOpen.value = false;
  page.value = "home";
  window.history.replaceState(null, "", "/");
  login.password = "";
  registration.password = "";
  await refresh();
}
async function signIn() {
  await perform(async () => signedIn(await post("/auth/login", login)));
}
async function previewInvite() {
  inviteInfo.value = null;
  await perform(async () => {
    inviteInfo.value = await post("/auth/invitation", {
      code: registration.invitation.trim(),
    });
  });
}
async function register() {
  await perform(async () => {
    await signedIn(
      await post("/auth/register", {
        ...registration,
        invitation: registration.invitation.trim(),
      }),
    );
    notice.value = "注册成功，已加入邀请码指定团队。";
  });
}
async function logout() {
  await perform(async () => {
    await post("/auth/logout");
    sessionStorage.removeItem("meetflow-token");
    expire();
  });
}
function navigate(next: string, refreshData = true) {
  sequence++;
  clearAudio();
  detail.value = null;
  page.value = next;
  error.value = "";
  notice.value = "";
  editOpen.value = false;
  requestOpen.value = false;
  transcriptOpen.value = false;
  historicalVersion.value = null;
  removalImpact.value = null;
  activity.value = [];
  clearFilters();
  batchSelected.value = [];
  batchErrors.value = [];
  batchConfirmed.value = false;
  if (refreshData && !busy.value) void perform(refresh);
}
function enterQuality(kind?: string, id?: string) {
  if (!user.value || !["manager", "boss"].includes(user.value.role)) return;
  qualitySource.value = kind && id ? { kind, id } : null;
  navigate("quality");
}
async function meetingFromQuality(kind: string, id: string) {
  navigate(kind === "center" ? "center" : "home", false);
  await perform(async () => {
    await refresh();
    await open(id, kind === "center");
  });
}
async function seekEvidence(id: string) {
  evidenceOpen.value = true;
  evidenceHighlight.value = id;
  const span = detail.value?.evidence?.[id];
  if (detail.value?.has_audio && span?.start != null) {
    await audio();
    const seek = () => {
      if (audioPlayer.value) audioPlayer.value.currentTime = span.start;
    };
    if (audioPlayer.value?.readyState) seek();
    else
      audioPlayer.value?.addEventListener("loadedmetadata", seek, {
        once: true,
      });
    notice.value = `原文已展开，录音定位到 ${Math.floor(span.start)} 秒。`;
  } else notice.value = "已展开并标记文字依据。";
}
async function clarifyQuestions() {
  await perform(async () => {
    const answers = Object.fromEntries(
      (report.value?.questions || [])
        .filter((q: Row) => questionAnswers[q.key]?.trim())
        .map((q: Row) => [q.key, questionAnswers[q.key]]),
    );
    const key = detail.value!.id;
    await post(`/workspace/recordings/${key}/clarifications`, {
      version: detail.value!.version,
      answers,
    });
    await refresh();
    await open(key);
    notice.value = "补充已记录，正在重新整理；原始发言和已有中心版本保留。";
  });
}
async function loadInvitation() {
  if (user.value) return;
  const join = location.hash.match(/^#join\/(.+)$/);
  if (!join) return;
  registration.invitation = decodeURIComponent(join[1]);
  joinMode.value = true;
  await previewInvite();
}
async function open(key: string, central = false) {
  const current = ++sequence,
    owner = user.value?.id;
  clearAudio();
  editOpen.value = false;
  requestOpen.value = false;
  transcriptOpen.value = false;
  historicalVersion.value = null;
  activity.value = [];
  removalImpact.value = null;
  checkSubmit.value = false;
  const result = await api(
    `/workspace/${central ? "center" : "recordings"}/${key}`,
  );
  if (current !== sequence || user.value?.id !== owner) return;
  detail.value = result;
  evidenceOpen.value = false;
  evidenceHighlight.value = "";
  for (const key of Object.keys(questionAnswers)) delete questionAnswers[key];
  for (const note of result.clarifications || [])
    Object.assign(questionAnswers, note.body.answers);
  centralDetail.value = central;
  submitCustomer.value =
    result.body?.customer || result.metadata?.customer || "";
  submitOpportunity.value =
    result.body?.opportunity_id || result.metadata?.opportunity_id || null;
}
async function saveRecording() {
  await perform(async () => {
    const signature = JSON.stringify(form);
    if (
      uploadAttempt?.signature !== signature ||
      uploadAttempt?.file !== file.value
    )
      uploadAttempt = { signature, key: crypto.randomUUID(), file: file.value };
    let result: Row;
    if (file.value) {
      const body = new FormData();
      body.append("file", file.value);
      body.append(
        "metadata",
        JSON.stringify({
          title: form.title,
          customer: form.customer,
          meeting_date: form.meeting_date,
          opportunity_id: form.opportunity_id,
          request_key: uploadAttempt!.key,
        }),
      );
      result = await api("/workspace/audio", { method: "POST", body });
    } else
      result = await post("/workspace/recordings", {
        ...form,
        request_key: uploadAttempt!.key,
      });
    uploadAttempt = null;
    formOpen.value = false;
    file.value = null;
    form.title = "";
    form.customer = "";
    form.meeting_date = "";
    form.transcript = "";
    form.opportunity_id = null;
    await refresh();
    await open(result.id);
    notice.value =
      result.notice ||
      "已保存到个人区，整理在后台继续。确认上报前不会进入管理中心。";
  });
}
async function submit() {
  if (!checkSubmit.value) {
    error.value = "请确认本次上报范围";
    return;
  }
  await perform(async () => {
    const signature = JSON.stringify([
      detail.value!.id,
      detail.value!.version,
      submitCustomer.value,
      submitOpportunity.value,
    ]);
    if (submitAttempt?.signature !== signature)
      submitAttempt = { signature, key: crypto.randomUUID() };
    await post(`/workspace/recordings/${detail.value!.id}/submit`, {
      version: detail.value!.version,
      customer: submitCustomer.value,
      opportunity_id: submitOpportunity.value,
      request_key: submitAttempt!.key,
    });
    navigate("center");
    await refresh();
    notice.value = "上报成功，中心已保存本次固定版本。";
  });
}
function editing(correction = false) {
  Object.assign(edit, metadata.value, {
    reason: "",
    use_personal_revision: false,
    opportunity_id: metadata.value?.opportunity_id || null,
  });
  editOpen.value = !correction;
  requestOpen.value = correction;
}
async function saveEdit() {
  await perform(async () => {
    const id = detail.value!.id;
    if (requestOpen.value)
      await post(`/workspace/center/${id}/corrections`, {
        ...edit,
        base_revision: detail.value!.revision,
      });
    else
      await api(`/workspace/recordings/${id}`, {
        method: "PATCH",
        body: JSON.stringify({
          title: edit.title,
          customer: edit.customer,
          meeting_date: edit.meeting_date,
          opportunity_id: edit.opportunity_id,
          version: detail.value!.version,
        }),
      });
    const wasCentral = centralDetail.value;
    await refresh();
    await open(id, wasCentral);
    notice.value = wasCentral
      ? "更正申请已提交，等待负责团队的主管确认。"
      : "个人修改已保存；中心原版本不受影响。";
  });
}
async function removePersonal() {
  await perform(async () => {
    await api(`/workspace/recordings/${detail.value!.id}`, {
      method: "DELETE",
    });
    navigate("home");
    await refresh();
    notice.value = "个人记录已移除，已有中心证据保留。";
  });
}
async function decide(row: Row, approve: boolean) {
  await perform(async () => {
    await post(`/workspace/corrections/${row.id}/review`, { approve });
    await refresh();
    notice.value = approve
      ? "已确认，中心生成新版本，旧版保留。"
      : "申请已驳回，中心保持原样。";
  });
}
async function createTeam() {
  await perform(async () => {
    await post("/admin/teams", { name: teamName.value });
    teamName.value = "";
    await refresh();
  });
}
async function createInvite() {
  await perform(async () => {
    const result = await post("/admin/invitations", {
      team_id: selectedTeam.value,
    });
    inviteLink.value = `${location.origin}/#join/${result.code}`;
    await refresh();
    notice.value = "已生成一次性邀请链接，有效期 7 天，角色固定为销售。";
  });
}
async function revoke(row: Row) {
  await perform(async () => {
    await post(`/admin/invitations/${row.id}/revoke`);
    await refresh();
    notice.value = "邀请码已撤销。";
  });
}
async function audio() {
  await perform(async () => {
    const owner = user.value?.id,
      key = detail.value!.id;
    const response = await fetch(
      `/api/workspace/${centralDetail.value ? "center" : "recordings"}/${key}/audio`,
      {
        headers: {
          Authorization: `Bearer ${sessionStorage.getItem("meetflow-token")}`,
        },
      },
    );
    if (!response.ok) throw new Error("录音不可用或无权访问");
    const blob = await response.blob();
    if (user.value?.id !== owner || detail.value?.id !== key) return;
    clearAudio();
    audioUrl.value = URL.createObjectURL(blob);
  });
}
async function exportCenter() {
  await perform(async () => {
    const response = await fetch(
      `/api/workspace/center/${detail.value!.id}/export`,
      {
        headers: {
          Authorization: `Bearer ${sessionStorage.getItem("meetflow-token")}`,
        },
      },
    );
    if (!response.ok) throw new Error("无权导出该中心记录");
    const url = URL.createObjectURL(await response.blob()),
      a = document.createElement("a");
    a.href = url;
    a.download = `会谈中心-${detail.value!.id}.md`;
    a.click();
    URL.revokeObjectURL(url);
  });
}
const polling = ref(false);
onMounted(async () => {
  window.addEventListener("meetflow-session-expired", expire);
  window.addEventListener("hashchange", loadInvitation);
  try {
    health.value = await api("/health");
    legacy.value = health.value.auth_enabled === false;
    if (legacy.value) return;
    const join = location.hash.match(/^#join\/(.+)$/);
    if (join) {
      await loadInvitation();
    } else if (sessionStorage.getItem("meetflow-token")) {
      user.value = await api("/auth/me");
      await refresh();
    }
  } catch (exc) {
    error.value = exc instanceof Error ? exc.message : "加载失败";
  } finally {
    loaded.value = true;
  }
  timer = window.setInterval(async () => {
    if (
      busy.value ||
      polling.value ||
      !user.value ||
      (!rows.value.some((r) => ["queued", "running"].includes(r.status)) &&
        Date.now() - lastRefresh < 10000)
    )
      return;
    polling.value = true;
    try {
      await refresh();
      if (
        detail.value &&
        !centralDetail.value &&
        ["queued", "running"].includes(detail.value.status)
      ) {
        const key = detail.value.id,
          current = sequence,
          owner = user.value?.id;
        const next = await api(`/workspace/recordings/${key}`);
        if (current === sequence && user.value?.id === owner)
          detail.value = next;
      }
    } catch (exc) {
      error.value = exc instanceof Error ? exc.message : "读取进度失败";
    } finally {
      polling.value = false;
    }
  }, 1500);
});
onBeforeUnmount(() => {
  window.clearInterval(timer);
  window.removeEventListener("meetflow-session-expired", expire);
  window.removeEventListener("hashchange", loadInvitation);
  clearAudio();
});
</script>

<template>
  <LegacyApp v-if="legacy" />
  <section v-else class="wk-root">
    <div v-if="!loaded" class="wk-login">正在打开工作区…</div>
    <template v-else-if="!user">
      <div class="wk-login">
        <div class="wk-brand">MeetFlow</div>
        <h1>{{ joinMode ? "加入销售团队" : "登录你的工作区" }}</h1>
        <p>会谈先留在个人区，确认上报后才进入团队中心。</p>
        <div v-if="error" class="wk-error" role="alert">{{ error }}</div>
        <template v-if="joinMode">
          <form @submit.prevent="previewInvite">
            <label
              >邀请码<input
                v-model="registration.invitation"
                required
                autocomplete="off"
            /></label>
            <button :disabled="busy" type="submit">核对邀请团队</button>
          </form>
          <div v-if="inviteInfo" class="wk-note" role="status">
            即将加入：{{ inviteInfo.company }} / {{ inviteInfo.team }} ·
            销售账号
          </div>
          <form v-if="inviteInfo" @submit.prevent="register">
            <label
              >姓名<input
                v-model="registration.name"
                required
                maxlength="80"
                autocomplete="name"
            /></label>
            <label
              >注册账号<input
                v-model="registration.username"
                required
                minlength="3"
                maxlength="120"
                autocomplete="username"
            /></label>
            <label
              >设置密码<input
                v-model="registration.password"
                required
                type="password"
                minlength="12"
                maxlength="128"
                autocomplete="new-password"
            /></label>
            <small
              >至少 12 位。团队与销售角色由邀请码确定，无需自己选择。</small
            >
            <button class="wk-primary" type="submit" :disabled="busy">
              注册并加入团队
            </button>
          </form>
          <button
            class="wk-link"
            @click="
              joinMode = false;
              error = '';
            "
          >
            返回登录
          </button>
        </template>
        <template v-else>
          <form @submit.prevent="signIn">
            <label
              >账号<input
                v-model="login.username"
                required
                autocomplete="username"
            /></label>
            <label
              >密码<input
                v-model="login.password"
                type="password"
                required
                autocomplete="current-password"
            /></label>
            <button class="wk-primary" :disabled="busy" type="submit">
              登录
            </button>
          </form>
          <button
            class="wk-link"
            @click="
              joinMode = true;
              inviteInfo = null;
              error = '';
            "
          >
            使用邀请码加入团队
          </button>
          <small>每个标签页独立登录，适合在本机分别使用不同账号。</small>
        </template>
      </div>
    </template>
    <template v-else>
      <header class="wk-header">
        <strong class="wk-brand">MeetFlow</strong>
        <nav aria-label="工作区导航">
          <template v-if="user.role === 'admin'"
            ><button @click="navigate('home')">团队与邀请码</button
            ><button @click="navigate('rules')">销售培训规则</button></template
          >
          <template v-else>
            <button v-if="user.role !== 'sales'" @click="navigate('home')">
              {{ user.role === "boss" ? "公司简报" : "团队工作台" }}
            </button>
            <button v-if="user.role === 'sales'" @click="navigate('home')">
              会谈录音
            </button>
            <button @click="navigate('center')">
              {{ user.role === "sales" ? "我的上报" : "中心会谈" }}
            </button>
            <button @click="navigate('corrections')">更正申请</button>
            <button
              v-if="['manager', 'boss'].includes(user.role)"
              @click="enterQuality()"
            >
              {{ user.role === "boss" ? "质检概览" : "销售质检" }}
            </button>
            <button @click="navigate('business')">客户与跟进</button>
            <button v-if="user.role === 'sales'" @click="navigate('reports')">
              简报与周报
            </button>
            <button @click="navigate('maintenance')">回收站</button>
          </template>
        </nav>
        <div class="wk-identity" aria-label="当前登录身份">
          {{ user.name }} · {{ roleNames[user.role] }} ·
          {{ user.team || user.company }}
        </div>
        <button @click="logout" :disabled="busy">退出登录</button>
      </header>
      <section class="wk-body">
        <div v-if="error" class="wk-error" role="alert">{{ error }}</div>
        <div v-if="notice" class="wk-note" role="status">{{ notice }}</div>
        <div class="wk-scope">
          {{ scope }} ·
          {{
            user.role === "sales"
              ? "未上报资料仅自己可见"
              : "管理范围仅含已上报资料"
          }}
        </div>
        <BusinessWorkspace
          v-if="
            page === 'business' ||
            (page === 'home' && user.role !== 'sales' && user.role !== 'admin')
          "
          :key="page"
          :user="user"
          :initial="page === 'business' ? 'customers' : 'overview'"
          @meeting="meetingFromQuality"
        />
        <ReportsWorkspace
          v-else-if="page === 'reports' && user.role === 'sales'"
        />
        <MaintenanceWorkspace v-else-if="page === 'maintenance'" :user="user" />
        <QualityWorkspace
          v-else-if="
            page === 'quality' && ['manager', 'boss'].includes(user.role)
          "
          :user="user"
          :source="qualitySource"
          @meeting="meetingFromQuality"
        />
        <TrainingRules v-else-if="user.role === 'admin' && page === 'rules'" />
        <template v-else-if="user.role === 'admin'">
          <h1>团队与邀请码</h1>
          <div class="wk-columns">
            <section class="wk-panel">
              <h2>邀请销售加入</h2>
              <label
                >指定团队<select v-model="selectedTeam">
                  <option v-for="t in teams" :key="t.id" :value="t.id">
                    {{ t.name }}
                  </option>
                </select></label
              >
              <p>角色固定为销售，一人一码，有效期 7 天。</p>
              <button
                class="wk-primary"
                @click="createInvite"
                :disabled="busy || !selectedTeam"
              >
                生成销售邀请码</button
              ><label v-if="inviteLink"
                >邀请链接<input
                  :value="inviteLink"
                  readonly
                  aria-label="邀请链接"
              /></label>
            </section>
            <section class="wk-panel">
              <h2>团队设置</h2>
              <form @submit.prevent="createTeam">
                <label>新团队名称<input v-model="teamName" required /></label
                ><button :disabled="busy">创建团队</button>
              </form>
              <ul>
                <li v-for="t in teams" :key="t.id">{{ t.name }}</li>
              </ul>
            </section>
          </div>
          <h2>账号与归属</h2>
          <div v-for="a in accounts" :key="a.id" class="wk-row">
            <span>{{ a.name }} · {{ a.username }}</span
            ><span>{{ roleNames[a.role] }} · {{ a.team || "公司范围" }}</span>
          </div>
          <h2>邀请码记录</h2>
          <div v-for="i in invites" :key="i.id" class="wk-row">
            <span
              >{{ teams.find((t) => t.id === i.team_id)?.name }} ·
              {{ i.used ? "已使用" : i.revoked ? "已撤销" : "待使用" }}</span
            ><button
              v-if="!i.used && !i.revoked"
              @click="revoke(i)"
              :disabled="busy"
            >
              撤销邀请码
            </button>
          </div>
        </template>
        <template v-else-if="detail">
          <button
            class="wk-link"
            @click="navigate(centralDetail ? 'center' : 'home')"
          >
            ← 返回列表
          </button>
          <div class="wk-heading">
            <div>
              <h1>{{ metadata?.title }}</h1>
              <p>
                {{
                  centralDetail
                    ? `中心 v${detail.revision}`
                    : stateNames[detail.status]
                }}
                · 客户：{{ metadata?.customer || "待归属" }} · 会谈：{{
                  metadata?.meeting_date || "日期待确认"
                }}
              </p>
              <small
                >{{ centralDetail ? "上报时间" : "上传时间" }}：{{
                  displayTime(
                    centralDetail ? detail.submitted_at : detail.uploaded_at,
                  )
                }}（北京时间）</small
              >
            </div>
            <button v-if="!centralDetail" @click="editing()">
              修改个人信息</button
            ><button v-else-if="user.role === 'sales'" @click="editing(true)">
              申请更正中心记录
            </button>
            <button
              v-if="
                !centralDetail &&
                ['waiting_review', 'completed', 'failed', 'cancelled'].includes(
                  detail.status,
                )
              "
              @click="editTranscript"
              :disabled="busy"
            >
              修订转写文字
            </button>
            <button
              v-if="
                report &&
                centralDetail &&
                ['manager', 'boss'].includes(user.role)
              "
              @click="
                enterQuality(centralDetail ? 'center' : 'personal', detail.id)
              "
            >
              查看本场销售质检
            </button>
          </div>
          <form
            v-if="transcriptOpen"
            class="wk-panel"
            @submit.prevent="saveTranscript"
          >
            <h2>修订本场转写</h2>
            <p>
              生成新版本并重新整理；旧转写和原始录音保留。手工修订文字不会自动重新对齐音频时间点，已上报中心需另行申请更正。
            </p>
            <label
              >修订后的转写文字<textarea
                v-model="transcriptDraft"
                required
                minlength="2"
                maxlength="200000"
                rows="12"
              /></label
            ><label
              >修订原因<textarea
                v-model="transcriptReason"
                required
                minlength="2"
                maxlength="1000"
                rows="2"
              /></label
            ><button class="wk-primary" :disabled="busy">
              保存新转写并重新整理</button
            ><button
              type="button"
              @click="transcriptOpen = false"
              :disabled="busy"
            >
              取消修订
            </button>
          </form>
          <form
            v-if="editOpen || requestOpen"
            class="wk-panel"
            @submit.prevent="saveEdit"
          >
            <h2>{{ requestOpen ? "中心更正申请" : "个人信息修改" }}</h2>
            <label>录音名称<input v-model="edit.title" required /></label
            ><OpportunityPicker
              v-model="edit.opportunity_id"
              @label="edit.customer = $event"
            /><label
              >实际会议日期<input
                v-model="edit.meeting_date"
                type="date" /></label
            ><label v-if="requestOpen"
              >更正原因<textarea v-model="edit.reason" required /></label
            ><label v-if="requestOpen"
              ><input
                type="checkbox"
                v-model="edit.use_personal_revision"
              />一并申请采用个人区最新纪要与转写（将该版本提供给主管审核）</label
            ><button class="wk-primary" :disabled="busy">
              {{ requestOpen ? "提交更正申请" : "保存个人修改" }}
            </button>
          </form>
          <div v-if="detail.error" class="wk-error">{{ detail.error }}</div>
          <div v-if="!centralDetail" class="wk-actions">
            <button
              v-if="detail.status === 'failed'"
              @click="processing('retry')"
              :disabled="busy"
            >
              恢复失败处理
            </button>
            <button
              v-if="['queued', 'running'].includes(detail.status)"
              @click="processing('cancel')"
              :disabled="busy || detail.cancel_requested"
            >
              {{ detail.cancel_requested ? "已请求停止" : "停止本次整理" }}
            </button>
          </div>
          <details>
            <summary @click="showActivity">操作时间与记录</summary>
            <p v-for="(event, index) in activity" :key="index">
              {{ displayTime(event.at) }} · {{ event.actor }} ·
              {{ event.action }} {{ event.reason }}
            </p>
          </details>
          <section
            v-if="centralDetail && user.role === 'manager'"
            class="wk-panel"
          >
            <button @click="previewRemoval" :disabled="busy">
              查看撤下此中心资料的影响
            </button>
            <form v-if="removalImpact" @submit.prevent="removeCenter">
              <p>{{ removalImpact.notice }}</p>
              <p>
                关联质检版本 {{ removalImpact.quality_versions }} · 后续事项版本
                {{ removalImpact.followup_versions }}
              </p>
              <label
                >撤下原因<textarea
                  v-model="removalReason"
                  required
                  maxlength="1000"
                /></label
              ><button :disabled="busy || !removalReason.trim()">
                确认撤下中心资料
              </button>
            </form>
          </section>
          <div v-if="report?.mode === 'demo'" class="wk-note">
            当前为规则纪要，未调用大模型；录音配置启用时转写为真实
            ASR。内容需核对。
          </div>
          <details v-if="!centralDetail && detail.transcript_history?.length">
            <summary>查看修订前的转写</summary>
            <section
              v-for="(h, index) in detail.transcript_history"
              :key="index"
            >
              <p>{{ displayTime(h.at) }} · 修订原因 {{ h.reason }}</p>
              <p class="wk-summary">{{ h.text }}</p>
            </section>
          </details>
          <div v-if="report" class="wk-columns">
            <section class="wk-panel">
              <h2>会谈纪要</h2>
              <p class="wk-summary">{{ report.summary || "暂无摘要" }}</p>
              <details v-if="report.claims?.length">
                <summary>讨论结论与决策（{{ report.claims.length }}）</summary>
                <article
                  v-for="(c, i) in report.claims"
                  :key="`claim-${i}`"
                  class="wk-finding"
                >
                  <strong>{{
                    (
                      {
                        fact: "信息",
                        decision: "决策",
                        commitment: "承诺",
                        suggestion: "建议",
                        condition: "条件",
                        unresolved: "未决",
                      } as Row
                    )[c.kind] || "信息"
                  }}</strong>
                  <p>{{ c.text }}</p>
                  <button
                    v-for="id in c.evidence"
                    :key="id"
                    class="wk-link"
                    @click="seekEvidence(id)"
                  >
                    查看原文依据
                  </button>
                </article>
              </details>
              <h3>后续事项</h3>
              <p v-for="a in report.actions" :key="a.id">
                {{ a.title }} · {{ a.owner || "负责人待确认" }} ·
                {{ a.due || "日期待确认" }}
                <button
                  v-for="id in a.evidence"
                  :key="id"
                  class="wk-link"
                  @click="seekEvidence(id)"
                >
                  依据
                </button>
              </p>
              <p v-if="!report.actions?.length">本次未提取到明确后续事项。</p>
              <details v-if="report.risks?.length">
                <summary>风险与未决条件（{{ report.risks.length }}）</summary>
                <article
                  v-for="(risk, i) in report.risks"
                  :key="`risk-${i}`"
                  class="wk-finding"
                >
                  <p>{{ risk.text }}</p>
                  <button
                    v-for="id in risk.evidence"
                    :key="id"
                    class="wk-link"
                    @click="seekEvidence(id)"
                  >
                    核对风险依据
                  </button>
                </article>
              </details>
              <h3 v-if="report.questions?.length">需要补充的疑点</h3>
              <form
                v-if="report.questions?.length"
                @submit.prevent="clarifyQuestions"
              >
                <article
                  v-for="q in report.questions"
                  :key="q.key"
                  class="wk-finding"
                >
                  <p>{{ q.text }}</p>
                  <label v-if="!centralDetail"
                    >你的补充<textarea
                      v-model="questionAnswers[q.key]"
                      maxlength="2000"
                      placeholder="不确定可以暂时留空；补充不等于客户已确认。"
                    /></label
                  ><button
                    v-for="id in q.evidence"
                    :key="id"
                    class="wk-link"
                    @click="seekEvidence(id)"
                    type="button"
                  >
                    核对疑点依据
                  </button>
                </article>
                <button
                  v-if="!centralDetail"
                  :disabled="
                    busy ||
                    !report.questions.some((q: Row) =>
                      questionAnswers[q.key]?.trim(),
                    )
                  "
                >
                  保存补充并重新整理
                </button>
              </form>
              <details v-if="report.challenges?.length">
                <summary>
                  分析中的核查提醒（{{ report.challenges.length }}）
                </summary>
                <p v-for="(c, i) in report.challenges" :key="i">{{ c.text }}</p>
              </details>
              <details v-if="detail.clarifications?.length">
                <summary>销售补充记录（保留原话，不等于客户确认）</summary>
                <article v-for="n in detail.clarifications" :key="n.id">
                  <p v-for="(answer, key) in n.body.answers" :key="key">
                    {{ n.body.questions[key] }}：{{ answer }}
                  </p>
                </article>
              </details>
              <h3>原文依据</h3>
              <details :open="evidenceOpen">
                <summary>展开本次原文</summary>
                <p
                  v-for="s in Object.values(detail.evidence || {}) as Row[]"
                  :key="s.id"
                  :class="{
                    'wk-evidence-highlight': evidenceHighlight === s.id,
                  }"
                >
                  {{ s.start != null ? `${Math.floor(s.start)}秒 · ` : ""
                  }}{{ s.text }}
                </p>
              </details>
            </section>
            <aside class="wk-panel">
              <h2>{{ centralDetail ? "中心证据" : "个人工作区" }}</h2>
              <template v-if="detail.has_audio"
                ><button @click="audio" :disabled="busy">加载录音</button
                ><audio
                  v-if="audioUrl"
                  ref="audioPlayer"
                  :src="audioUrl"
                  controls /></template
              ><template v-if="!centralDetail"
                ><p>上传和个人修改不自动进入中心。上报时固定本次依据。</p>
                <div v-if="detail.submission">
                  <p>
                    {{
                      detail.submission.deleted
                        ? "该场曾上报，中心记录已由主管撤下。"
                        : "本场已上报；个人修改不会同步覆盖中心。"
                    }}
                  </p>
                  <button
                    v-if="!detail.submission.deleted"
                    @click="perform(() => open(detail!.submission.id, true))"
                    :disabled="busy"
                  >
                    查看已上报版本
                  </button>
                </div>
                <template v-else>
                  <OpportunityPicker
                    v-model="submitOpportunity"
                    label="上报客户／商机（必选）"
                    @label="submitCustomer = $event"
                  /><label class="wk-check"
                    ><input
                      type="checkbox"
                      v-model="checkSubmit"
                    />确认上报本场录音与纪要，主管和老板可在范围内查看。</label
                  ><button
                    class="wk-primary"
                    @click="submit"
                    :disabled="busy || !report || !submitOpportunity"
                  >
                    确认上报本场
                  </button></template
                ><button
                  class="wk-link"
                  @click="removePersonal"
                  :disabled="busy"
                >
                  移除个人记录（中心证据保留）
                </button></template
              ><template v-else
                ><p>个人区删改不会覆盖此版本。</p>
                <button @click="exportCenter" :disabled="busy">
                  导出中心纪要 Markdown
                </button>
                <h3>版本历史</h3>
                <p v-for="h in detail.history" :key="h.revision">
                  v{{ h.revision }} · {{ h.metadata.title }} ·
                  {{ h.metadata.meeting_date || "日期未知" }}
                </p>
                <button
                  v-for="h in detail.history"
                  :key="'version-' + h.revision"
                  @click="viewHistorical(h.revision)"
                  :disabled="busy"
                >
                  查看中心 v{{ h.revision }} 的纪要与原文
                </button>
                <section v-if="historicalVersion">
                  <h4>
                    历史中心 v{{ historicalVersion.revision }} ·
                    {{ historicalVersion.metadata.title }}
                  </h4>
                  <p class="wk-summary">
                    {{ historicalVersion.report.summary }}
                  </p>
                  <p v-for="a in historicalVersion.report.actions" :key="a.id">
                    {{ a.title }} · {{ a.due || "日期待确认" }}
                  </p>
                  <details>
                    <summary>该版原文依据</summary>
                    <p
                      v-for="(span, id) in historicalVersion.evidence"
                      :key="id"
                    >
                      {{ span.text }}
                    </p>
                  </details>
                </section>
                <p v-if="!detail.history.length">
                  当前为首个上报版本。
                </p></template
              >
            </aside>
          </div>
          <p v-else>正在后台整理，可离开页面稍后查看。</p>
        </template>
        <template v-else-if="user.role === 'sales' && page === 'home'">
          <div class="wk-heading">
            <div>
              <h1>{{ user.name }}的会谈</h1>
              <p>先留在个人区，选中确认后才上报。</p>
            </div>
            <button class="wk-primary" @click="formOpen = !formOpen">
              上传录音／导入文字
            </button>
          </div>
          <form
            v-if="formOpen"
            class="wk-panel"
            @submit.prevent="saveRecording"
          >
            <h2>带入本次会谈</h2>
            <label
              >录音文件<input
                type="file"
                accept="audio/*"
                :disabled="!health.audio_available"
                @change="
                  file = ($event.target as HTMLInputElement).files?.[0] || null
                " /></label
            ><small>{{
              health.audio_available
                ? "可直接选录音，名称与客户可稍后补。"
                : "本机 ASR 未启用，可先粘贴已有转写。"
            }}</small>
            <details>
              <summary>补充名称、日期和客户（可选）</summary>
              <label>录音名称<input v-model="form.title" /></label
              ><label
                >实际会议日期<input
                  v-model="form.meeting_date"
                  type="date" /></label
              ><OpportunityPicker
                v-model="form.opportunity_id"
                @label="form.customer = $event"
              />
            </details>
            <label v-if="!file"
              >会谈文字<textarea
                v-model="form.transcript"
                minlength="2"
                required
                rows="5"
                placeholder="按发言换行，填写实际内容。测试资料请标记为合成。"
              /></label
            ><button class="wk-primary" :disabled="busy">保存并自动整理</button>
          </form>
          <RecordingFilters
            v-if="rows.length"
            v-model:query="search"
            v-model:mode="dateMode"
            v-model:from="dateFrom"
            v-model:to="dateTo"
            v-model:state="statusFilter"
            :states="stateNames"
            received-label="上传"
            @clear="clearFilters"
          />
          <p v-if="rows.length">
            当前显示 {{ visibleRows.length }} / {{ rows.length }} 场个人会谈。
          </p>
          <div v-if="!rows.length" class="wk-empty">
            个人区暂无会谈。其他销售、主管和老板无法读取你的未上报资料。
          </div>
          <section v-if="batchRows.length" class="wk-panel">
            <h2>选中上报 {{ batchRows.length }} 场</h2>
            <p>
              仅本次选中的资料进入中心，最多 30
              场；缺客户商机时在这里补选。逐场保存，失败不影响其他成功记录。
            </p>
            <section v-for="r in batchRows" :key="r.id">
              <h3>{{ r.body.title }}</h3>
              <p v-if="batchMeta[r.id].version !== r.version" class="wk-error">
                本场版本已变化，请取消后重新选择。
              </p>
              <OpportunityPicker v-model="batchMeta[r.id].opportunity_id" />
            </section>
            <p v-for="(f, index) in batchErrors" :key="index" class="wk-error">
              {{ f.title }}：{{ f.message }}
            </p>
            <label
              ><input
                type="checkbox"
                v-model="batchConfirmed"
              />确认将以上录音和纪要上报，供主管和老板在权限内查看。</label
            ><button
              class="wk-primary"
              @click="submitBatch"
              :disabled="busy || !batchConfirmed || !batchReady"
            >
              确认上报选中会谈
            </button>
          </section>
          <article v-for="r in visibleRows" :key="r.id" class="wk-row">
            <div>
              <label
                v-if="
                  ['waiting_review', 'completed'].includes(r.status) &&
                  !center.some((s) => s.recording_id === r.id)
                "
                ><input
                  type="checkbox"
                  :checked="batchSelected.includes(r.id)"
                  :value="r.id"
                  @change="toggleBatch(r)"
                />选中上报本场</label
              >
              <strong>{{ r.body.title }}</strong>
              <p>
                {{ r.body.customer || "客户待归属" }} · 会谈
                {{ r.body.meeting_date || "日期待确认" }} ·
                {{ stateNames[r.status] }}
              </p>
              <small>上传 {{ displayTime(r.uploaded_at) }}（北京时间）</small>
            </div>
            <button @click="perform(() => open(r.id))" :disabled="busy">
              查看会谈
            </button>
          </article>
        </template>
        <template v-else-if="page === 'corrections'">
          <h1>更正申请</h1>
          <p>中心旧版在主管确认前保持不变。</p>
          <button @click="perform(refresh)" :disabled="busy">
            刷新更正申请
          </button>
          <div v-if="!corrections.length" class="wk-empty">
            当前范围内没有更正申请。
          </div>
          <section v-for="c in corrections" :key="c.id" class="wk-panel">
            <h2>{{ c.before.title }}</h2>
            <p>
              基于 v{{ c.base_revision }} ·
              {{
                c.status === "pending"
                  ? "待主管确认"
                  : c.status === "approved"
                    ? "已确认"
                    : "已驳回"
              }}
            </p>
            <p>
              拟修改为：{{ c.proposed.title }} · {{ c.proposed.customer }} ·
              {{ c.proposed.meeting_date || "日期未知" }}
            </p>
            <blockquote>{{ c.reason }}</blockquote>
            <details v-if="c.proposed.replacement">
              <summary>对比本次申请替换的纪要与转写</summary>
              <h3>中心原版纪要</h3>
              <p class="wk-summary">{{ c.before_summary }}</p>
              <h3>拟采用的个人新版纪要</h3>
              <p class="wk-summary">
                {{ c.proposed.replacement.report.summary }}
              </p>
              <h3>原版转写与依据</h3>
              <p v-for="(span, id) in c.before_evidence" :key="id">
                {{ span.text }}
              </p>
              <h3>拟采用的新版转写与依据</h3>
              <p
                v-for="(span, id) in c.proposed.replacement.evidence"
                :key="id"
              >
                {{ span.text }}
              </p>
            </details>
            <div
              v-if="user.role === 'manager' && c.status === 'pending'"
              class="wk-actions"
            >
              <button
                class="wk-primary"
                @click="decide(c, true)"
                :disabled="busy"
              >
                确认更正并生成新版</button
              ><button @click="decide(c, false)" :disabled="busy">
                驳回申请
              </button>
            </div>
          </section>
        </template>
        <template v-else>
          <div class="wk-heading">
            <div>
              <h1>
                {{
                  user.role === "sales"
                    ? "我的上报"
                    : user.role === "boss"
                      ? "公司中心会谈"
                      : "团队中心会谈"
                }}
              </h1>
              <p>
                {{
                  user.role === "sales"
                    ? "个人区移除后，这里的固定版本仍保留。"
                    : "只展示已上报资料；没有数据时不会读取销售个人草稿。"
                }}
              </p>
            </div>
            <button @click="perform(refresh)" :disabled="busy">刷新中心</button>
          </div>
          <RecordingFilters
            v-model:query="search"
            v-model:mode="dateMode"
            v-model:from="dateFrom"
            v-model:to="dateTo"
            v-model:owner="ownerFilter"
            v-model:team="teamFilter"
            :owners="centerOwners"
            :teams="centerTeams"
            received-label="上报"
            @clear="clearFilters"
          />
          <div class="wk-note">
            当前可见 {{ visibleCenter.length }} 场上报。范围：{{
              scope
            }}。该数量不代表全部销售活动。
          </div>
          <div v-if="!visibleCenter.length" class="wk-empty">
            当前范围内暂无已上报会谈。
          </div>
          <article v-for="s in visibleCenter" :key="s.id" class="wk-row">
            <div>
              <strong>{{ s.metadata.title }} · 中心 v{{ s.revision }}</strong>
              <p>
                {{ s.owner }} · {{ s.metadata.customer }} · 会谈
                {{ s.metadata.meeting_date || "日期待确认" }}
              </p>
              <small>上报 {{ displayTime(s.submitted_at) }}（北京时间）</small>
            </div>
            <button @click="perform(() => open(s.id, true))" :disabled="busy">
              查看中心证据
            </button>
          </article>
        </template>
      </section>
    </template>
  </section>
</template>

<style scoped>
.wk-root {
  color: #203b33;
  background: #f4f6f2;
  min-height: 100vh;
  font-size: 14px;
  line-height: 1.7;
}
.wk-root * {
  box-sizing: border-box;
}
.wk-root button {
  border: 1px solid #d6dfd6;
  border-radius: 7px;
  padding: 9px 14px;
  background: #fff;
  color: #244a38;
  font: inherit;
  cursor: pointer;
}
.wk-root button:disabled {
  opacity: 0.45;
  cursor: default;
}
.wk-root .wk-primary {
  background: #225e47;
  border-color: #225e47;
  color: #fff;
}
.wk-root .wk-link {
  border: 0;
  background: transparent;
  padding: 7px 0;
  text-decoration: underline;
  text-underline-offset: 3px;
}
.wk-root h1 {
  font-size: 27px;
  margin: 0 0 10px;
  line-height: 1.35;
  letter-spacing: -0.5px;
}
.wk-root h2 {
  font-size: 19px;
  margin: 16px 0 12px;
}
.wk-root h3 {
  font-size: 15px;
  margin: 20px 0 8px;
}
.wk-root p {
  margin: 8px 0 15px;
}
.wk-root small {
  font-size: 12px;
  color: #61746a;
}
.wk-root label {
  display: grid;
  gap: 6px;
  margin: 14px 0;
  font-size: 13px;
}
.wk-root input:not([type="checkbox"]),
.wk-root select,
.wk-root textarea {
  width: 100%;
  min-width: 0;
  padding: 10px;
  border: 1px solid #cdd8cf;
  border-radius: 6px;
  background: #fff;
  color: #203b33;
  font: inherit;
}
.wk-root textarea {
  resize: vertical;
}
.wk-root .wk-check {
  display: flex;
  align-items: flex-start;
  gap: 9px;
}
.wk-check input {
  margin-top: 6px;
}
.wk-login {
  max-width: 490px;
  margin: auto;
  padding: 65px 24px;
}
.wk-login form {
  margin: 20px 0;
}
.wk-login form > button {
  margin-top: 12px;
}
.wk-login small {
  display: block;
  margin: 12px 0;
}
.wk-brand {
  font-size: 23px;
  font-weight: 650;
  letter-spacing: -0.7px;
}
.wk-login .wk-brand {
  margin-bottom: 35px;
}
.wk-header {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 22px;
  padding: 18px 30px;
  background: #fff;
  border-bottom: 1px solid #dce3dc;
}
.wk-header nav {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.wk-header nav button {
  border: 0;
}
.wk-identity {
  margin-left: auto;
  font-size: 12px;
  color: #50685a;
}
.wk-body {
  max-width: 1180px;
  padding: 30px;
  margin: auto;
}
.wk-scope {
  font-size: 12px;
  color: #61746a;
  margin-bottom: 22px;
}
.wk-heading {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 18px;
  flex-wrap: wrap;
  margin: 20px 0;
}
.wk-panel {
  background: #fff;
  border: 1px solid #dce3dc;
  border-radius: 9px;
  padding: 23px;
  margin: 20px 0;
  min-width: 0;
}
.wk-panel h2:first-child {
  margin-top: 0;
}
.wk-columns {
  display: grid;
  grid-template-columns: minmax(0, 1.6fr) minmax(250px, 1fr);
  gap: 24px;
  align-items: start;
}
.wk-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 15px;
  padding: 20px 3px;
  border-bottom: 1px solid #dce3dc;
}
.wk-row > div {
  min-width: 0;
  overflow-wrap: anywhere;
}
.wk-row p {
  font-size: 13px;
  color: #61746a;
  margin: 4px 0;
}
.wk-note {
  background: #e6eee5;
  padding: 12px 16px;
  border-radius: 7px;
  margin: 16px 0;
}
.wk-error {
  background: #f9e9e6;
  color: #8a3525;
  padding: 12px 16px;
  border-radius: 7px;
  margin: 16px 0;
  overflow-wrap: anywhere;
}
.wk-empty {
  padding: 35px 20px;
  background: #fff;
  border-radius: 8px;
  color: #61746a;
  margin-top: 24px;
}
.wk-summary {
  white-space: pre-wrap;
}
.wk-finding {
  border-left: 3px solid #dce7d8;
  padding: 8px 12px;
  margin: 12px 0;
  background: #f8faf6;
}
.wk-finding p {
  margin: 6px 0;
}
.wk-evidence-highlight {
  background: #fff0bf;
  border-left: 3px solid #ba9556;
  padding: 12px;
}
.wk-actions {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}
.wk-root audio {
  max-width: 100%;
  display: block;
  margin: 14px 0;
}
.wk-root blockquote {
  border-left: 3px solid #d6dfd6;
  padding-left: 16px;
  margin: 16px 0;
}
.wk-root details {
  margin: 16px 0;
}
.wk-root summary {
  cursor: pointer;
}
.wk-panel input[readonly] {
  font-size: 12px;
}
@media (max-width: 720px) {
  .wk-columns {
    grid-template-columns: minmax(0, 1fr);
  }
  .wk-header {
    padding: 15px;
    gap: 10px;
  }
  .wk-identity {
    width: 100%;
    margin: 0;
  }
  .wk-body {
    padding: 20px 15px;
  }
  .wk-root h1 {
    font-size: 24px;
  }
  .wk-panel {
    padding: 18px;
  }
  .wk-root input:not([type="checkbox"]),
  .wk-root select,
  .wk-root textarea {
    font-size: 16px;
  }
}
</style>
