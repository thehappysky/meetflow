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
import { api, post } from "./api";

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
});
const edit = reactive({
  title: "",
  customer: "",
  meeting_date: "",
  reason: "",
});
const file = ref<File | null>(null),
  checkSubmit = ref(false);
const qualitySource = ref<{ kind: string; id: string } | null>(null);
const questionAnswers = reactive<Row>({});
const evidenceOpen = ref(false),
  evidenceHighlight = ref("");
const audioPlayer = ref<HTMLAudioElement | null>(null);
let timer: number | undefined,
  sequence = 0,
  lastRefresh = 0;
let submitAttempt: { signature: string; key: string } | null = null;
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
  center.value.filter((r) =>
    [r.metadata.title, r.metadata.customer, r.owner]
      .join(" ")
      .includes(search.value),
  ),
);
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
  if (refreshData && !busy.value) void perform(refresh);
}
function enterQuality(kind?: string, id?: string) {
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
}
async function saveRecording() {
  await perform(async () => {
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
        }),
      );
      result = await api("/workspace/audio", { method: "POST", body });
    } else result = await post("/workspace/recordings", form);
    formOpen.value = false;
    file.value = null;
    form.title = "";
    form.customer = "";
    form.meeting_date = "";
    form.transcript = "";
    await refresh();
    await open(result.id);
    notice.value =
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
    ]);
    if (submitAttempt?.signature !== signature)
      submitAttempt = { signature, key: crypto.randomUUID() };
    await post(`/workspace/recordings/${detail.value!.id}/submit`, {
      version: detail.value!.version,
      customer: submitCustomer.value,
      request_key: submitAttempt!.key,
    });
    navigate("center");
    await refresh();
    notice.value = "上报成功，中心已保存本次固定版本。";
  });
}
function editing(correction = false) {
  Object.assign(edit, metadata.value, { reason: "" });
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
            <button v-if="user.role === 'sales'" @click="navigate('home')">
              会谈录音
            </button>
            <button @click="navigate('center')">
              {{ user.role === "sales" ? "我的上报" : "中心会谈" }}
            </button>
            <button @click="navigate('corrections')">更正申请</button>
            <button @click="enterQuality()">
              {{
                user.role === "sales"
                  ? "复盘质检"
                  : user.role === "boss"
                    ? "质检概览"
                    : "销售质检"
              }}
            </button>
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
          {{ scope }} · 权限由服务器校验 ·
          {{
            user.role === "sales"
              ? "未上报资料仅自己可见"
              : "管理范围仅含已上报资料"
          }}
        </div>
        <QualityWorkspace
          v-if="page === 'quality' && user.role !== 'admin'"
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
              v-if="report"
              @click="
                enterQuality(centralDetail ? 'center' : 'personal', detail.id)
              "
            >
              查看本场复盘质检
            </button>
          </div>
          <form
            v-if="editOpen || requestOpen"
            class="wk-panel"
            @submit.prevent="saveEdit"
          >
            <h2>{{ requestOpen ? "中心更正申请" : "个人信息修改" }}</h2>
            <label>录音名称<input v-model="edit.title" required /></label
            ><label
              >客户／商机<input
                v-model="edit.customer"
                :required="requestOpen" /></label
            ><label
              >实际会议日期<input
                v-model="edit.meeting_date"
                type="date" /></label
            ><label v-if="requestOpen"
              >更正原因<textarea v-model="edit.reason" required /></label
            ><button class="wk-primary" :disabled="busy">
              {{ requestOpen ? "提交更正申请" : "保存个人修改" }}
            </button>
          </form>
          <div v-if="detail.error" class="wk-error">{{ detail.error }}</div>
          <div v-if="report?.mode === 'demo'" class="wk-note">
            当前为规则纪要，未调用大模型；录音配置启用时转写为真实
            ASR。内容需核对。
          </div>
          <div v-if="report" class="wk-columns">
            <section class="wk-panel">
              <h2>会谈纪要</h2>
              <p class="wk-summary">{{ report.summary || "暂无摘要" }}</p>
              <h3 v-if="report.claims?.length">讨论结论与决策</h3>
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
              <h3 v-if="report.risks?.length">风险与未决条件</h3>
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
                  <label
                    >上报客户／商机<input
                      v-model="submitCustomer"
                      placeholder="上报前补选或填写" /></label
                  ><label class="wk-check"
                    ><input
                      type="checkbox"
                      v-model="checkSubmit"
                    />确认上报本场录音与纪要，主管和老板可在范围内查看。</label
                  ><button
                    class="wk-primary"
                    @click="submit"
                    :disabled="busy || !report || !submitCustomer.trim()"
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
              ><label>客户／商机<input v-model="form.customer" /></label>
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
          <div v-if="!rows.length" class="wk-empty">
            个人区暂无会谈。其他销售、主管和老板无法读取你的未上报资料。
          </div>
          <article v-for="r in rows" :key="r.id" class="wk-row">
            <div>
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
          <label
            >搜索中心会谈<input
              v-model="search"
              placeholder="客户、销售或会谈标题"
          /></label>
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
