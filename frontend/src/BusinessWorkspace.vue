<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref, computed } from "vue";
import { api, post, NETWORK_ERROR } from "./api";
import OpportunityPicker from "./OpportunityPicker.vue";
type Row = Record<string, any>;
const props = defineProps<{ user: Row; initial?: string }>();
const emit = defineEmits<{ meeting: [kind: string, id: string] }>();
const tab = ref(props.initial || "overview"),
  dashboard = ref<Row | null>(null),
  opps = ref<Row[]>([]),
  items = ref<Row[]>([]),
  opportunity = ref<Row | null>(null);
const scope = ref(props.user.role === "sales" ? "mine" : "center"),
  days = ref(3),
  busy = ref(false),
  error = ref(""),
  notice = ref(""),
  search = ref("");
const editing = ref<Row | null>(null),
  feedback = ref(""),
  state = ref("todo"),
  due = ref(""),
  replaces = ref(""),
  responsible = ref("");
const stage = ref("需求沟通"),
  stageSource = ref(""),
  stageReason = ref("");
let timer: number | undefined,
  dead = false,
  polling = false;
const statusNames: Row = {
  todo: "待跟进",
  in_progress: "推进中",
  waiting: "等待条件",
  paused: "已暂停",
  done: "已完成反馈",
  cancelled: "已取消",
};
const visibleOpps = computed(() =>
  opps.value.filter((r) =>
    `${r.customer} ${r.title} ${r.owner}`.includes(search.value),
  ),
);
const visibleItems = computed(() =>
  items.value.filter((r) =>
    `${r.body.title} ${r.source.metadata.customer || ""}`.includes(
      search.value,
    ),
  ),
);
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
  const selectedScope = scope.value;
  const result = await Promise.all([
    api(`/workspace/business/dashboard?days=${days.value}`),
    api("/workspace/business/opportunities"),
    api(`/workspace/business/followups?scope=${selectedScope}`),
  ]);
  if (dead || scope.value !== selectedScope) return;
  [dashboard.value, opps.value, items.value] = result;
  if (error.value === NETWORK_ERROR) error.value = "";
}
async function openOpp(row: Row) {
  const result = await api(`/workspace/business/opportunities/${row.id}`);
  if (dead) return;
  opportunity.value = result;
  stage.value = result.stage === "待确认" ? "需求沟通" : result.stage;
  stageSource.value = result.stage_sources[0]?.id || "";
  stageReason.value = "";
}
function editItem(item: Row) {
  editing.value = item;
  state.value = item.body.status;
  feedback.value = "";
  due.value = item.body.due || "";
  responsible.value = item.body.responsible || "";
  replaces.value = "";
}
async function saveItem() {
  await perform(async () => {
    await api(`/workspace/business/followups/${editing.value!.id}`, {
      method: "PATCH",
      body: JSON.stringify({
        version: editing.value!.version,
        status: state.value,
        due: due.value || null,
        note: feedback.value,
        supersedes_id: replaces.value || null,
        responsible: responsible.value.trim() || null,
      }),
    });
    editing.value = null;
    await load();
    notice.value = "跟进反馈已保存，原约定和历史变化保留。";
  });
}
async function saveStage() {
  await perform(async () => {
    await post(
      `/workspace/business/opportunities/${opportunity.value!.id}/stage`,
      {
        version: opportunity.value!.version,
        stage: stage.value,
        source_id: stageSource.value,
        reason: stageReason.value,
      },
    );
    await openOpp(opportunity.value!);
    await load();
    notice.value = "阶段已根据所选已上报会谈确认。";
  });
}
onMounted(async () => {
  await perform(load);
  timer = window.setInterval(async () => {
    if (busy.value || editing.value || polling || dead) return;
    polling = true;
    try {
      await load();
    } catch (e) {
      error.value = e instanceof Error ? e.message : "读取失败";
    } finally {
      polling = false;
    }
  }, 15000);
});
onBeforeUnmount(() => {
  dead = true;
  window.clearInterval(timer);
});
function isPastDue(value: string) {
  return !!dashboard.value && value.localeCompare(dashboard.value.today) === -1;
}
</script>
<template>
  <section class="business">
    <div class="b-head">
      <div>
        <p class="b-muted">
          {{
            user.role === "sales"
              ? "自己的客户与后续安排"
              : user.role === "boss"
                ? "公司已上报资料的变化与资源关注"
                : "团队已上报资料的推进与跟进"
          }}
        </p>
        <h1>
          {{
            user.role === "sales"
              ? "我的客户与跟进"
              : user.role === "boss"
                ? "公司工作简报"
                : "团队工作台"
          }}
        </h1>
      </div>
      <button @click="perform(load)" :disabled="busy">刷新业务资料</button>
    </div>
    <div v-if="error" class="b-error" role="alert">{{ error }}</div>
    <div v-if="notice" class="b-notice" role="status">{{ notice }}</div>
    <nav aria-label="业务视图">
      <button
        @click="
          tab = 'overview';
          opportunity = null;
        "
      >
        概览</button
      ><button @click="tab = 'customers'">客户与商机</button
      ><button
        @click="
          tab = 'followups';
          opportunity = null;
        "
      >
        待办与承诺
      </button>
    </nav>
    <p class="b-muted">{{ dashboard?.notice }}</p>
    <p
      v-if="dashboard?.sources.some((s: Row) => s.report.mode === 'demo')"
      class="b-notice"
    >
      当前包含规则演示提取的资料，业务判断请核对原文；已确认阶段和人工跟进会分别留痕。
    </p>
    <template v-if="tab === 'overview' && dashboard"
      ><div class="b-stats">
        <article>
          <span>今日收到资料</span><strong>{{ dashboard.received }}</strong>
        </article>
        <article>
          <span>已知今日会谈</span
          ><strong>{{ dashboard.occurred_today }}</strong>
        </article>
        <article>
          <span>会谈日期待确认</span
          ><strong>{{ dashboard.unknown_dates }}</strong>
        </article>
        <article>
          <span>临近约定／尚无完成记录</span
          ><strong>{{ dashboard.due.length }}</strong>
        </article>
      </div>
      <label class="b-days"
        >站内关注范围<select v-model.number="days" @change="perform(load)">
          <option :value="0">今天及以前</option>
          <option :value="3">未来 3 天及以前</option>
          <option :value="7">未来 7 天及以前</option>
        </select></label
      >
      <div class="b-columns">
        <section class="b-card">
          <h2>
            {{
              user.role === "boss"
                ? "需要公司资源的关注线索"
                : "可能需要介入的事项"
            }}
          </h2>
          <p v-if="!dashboard.suggestions.length" class="b-muted">
            当前可见资料没有匹配的关注线索，不代表业务没有风险。
          </p>
          <article
            v-for="(s, i) in dashboard.suggestions"
            :key="i"
            class="b-row"
          >
            <strong>{{ s.customer || "客户待归属" }}</strong>
            <p>{{ s.text }}</p>
            <small>{{ s.label }}</small
            ><button @click="emit('meeting', s.kind, s.source_id)">
              查看会谈和依据
            </button>
          </article>
        </section>
        <section class="b-card">
          <h2>临近约定与缺少反馈</h2>
          <p v-if="!dashboard.due.length">
            暂无有明确日期且落在此范围的未完成事项。
          </p>
          <article v-for="f in dashboard.due" :key="f.id" class="b-row">
            <strong>{{ f.body.title }}</strong>
            <p>{{ f.body.due }} · {{ f.body.responsible || "责任人待确认" }}</p>
            <small>{{
              isPastDue(f.body.due)
                ? "已到期，尚无完成记录"
                : "临近约定，尚无完成记录"
            }}</small
            ><button
              @click="
                tab = 'followups';
                editing = null;
              "
            >
              进入跟进
            </button>
          </article>
        </section>
      </div>
      <section class="b-card">
        <h2>
          {{ user.role === "boss" ? "公司近期会谈摘要" : "近期谈了什么" }}
        </h2>
        <article v-for="s in dashboard.sources" :key="s.id" class="b-row">
          <div>
            <strong
              >{{ s.metadata.customer || "客户待归属" }} ·
              {{ s.metadata.title }}</strong
            >
            <p>
              {{ s.owner }} · 实际会谈
              {{ s.metadata.meeting_date || "日期待确认" }}
            </p>
            <p class="b-summary">{{ s.report.summary }}</p>
          </div>
          <button @click="emit('meeting', s.kind, s.id)">查看本场</button>
        </article>
        <p v-if="!dashboard.sources.length">当前范围暂无可用会谈。</p>
      </section></template
    >
    <template v-else-if="tab === 'customers'"
      ><label>查找客户或商机<input v-model="search" /></label
      ><OpportunityPicker
        v-if="user.role === 'sales'"
        :model-value="null"
        label="新建或选择客户商机"
        @update:model-value="
          async (id) => {
            await load();
            const row = opps.find((r) => r.id === id);
            if (row) await perform(() => openOpp(row));
          }
        "
      />
      <div class="b-columns">
        <section class="b-card">
          <h2>客户与商机</h2>
          <button
            v-for="o in visibleOpps"
            :key="o.id"
            class="b-op"
            @click="perform(() => openOpp(o))"
          >
            <strong>{{ o.customer }} / {{ o.title }}</strong
            ><span>{{ o.owner }} · {{ o.stage }}</span>
          </button>
          <p v-if="!visibleOpps.length">
            暂无当前范围可见的商机；同名客户不会自动合并。
          </p>
        </section>
        <section v-if="opportunity" class="b-card">
          <h2>{{ opportunity.customer }} / {{ opportunity.title }}</h2>
          <p>当前阶段：{{opportunity.stage}} · {{opportunity.stage_basis}}，不计算虚构推进百分比。</p>
          <form v-if="user.role !== 'boss'" @submit.prevent="saveStage">
            <label
              >确认阶段<select v-model="stage">
                <option
                  v-for="s in [
                    '需求沟通',
                    '产品演示',
                    '方案报价',
                    '决策签约',
                    '已签约',
                    '已终止',
                  ]"
                  :key="s"
                >
                  {{ s }}
                </option>
              </select></label
            ><label
              >依据的已上报会谈<select v-model="stageSource" required>
                <option value="">请选择已上报来源</option>
                <option
                  v-for="s in opportunity.stage_sources"
                  :key="s.id"
                  :value="s.id"
                >
                  {{ s.metadata.title }}
                </option>
              </select></label
            ><label
              >确认原因<textarea
                v-model="stageReason"
                minlength="2"
                required
              /></label
            ><button :disabled="busy || !stageSource">保存阶段确认</button>
          </form>
          <details v-if="opportunity.history.length">
            <summary>阶段变化依据</summary>
            <p v-for="(h, i) in opportunity.history" :key="i">
              {{ h.actor }}：{{ h.before }} → {{ h.after }} · {{ h.reason }}
            </p>
          </details>
          <h3>历次会谈与新记录</h3>
          <article v-for="s in opportunity.timeline" :key="s.id" class="b-row">
            <strong
              >{{ s.metadata.meeting_date || "时间待确认" }} ·
              {{ s.metadata.title }}</strong
            >
            <p>{{ s.report.summary }}</p>
            <small>{{ s.comparison }}</small>
            <ul>
              <li v-for="(c, i) in s.new_records" :key="i">{{ c.text }}</li>
            </ul>
            <button @click="emit('meeting', s.kind, s.id)">核对本场依据</button>
          </article>
        </section>
      </div></template
    >
    <template v-else
      ><div class="b-head">
        <label>查找待办或客户<input v-model="search" /></label
        ><label v-if="user.role === 'sales'"
          >资料范围<select
            v-model="scope"
            @change="
              editing = null;
              perform(load);
            "
          >
            <option value="mine">我的全部跟进（已上报事项团队可见）</option>
            <option value="personal">个人区事项（反馈仅本人可见）</option>
            <option value="center">已上报跟进事项</option>
          </select></label
        >
      </div>
      <p>以下为会谈提取的后续安排，不是主管派单；没有反馈时不判定失约。</p>
      <article v-for="f in visibleItems" :key="f.id" class="b-card">
        <p class="b-muted">
          {{
            f.source_kind === "center"
              ? "已上报来源 · 本项跟进反馈团队可见"
              : "个人来源 · 本项跟进反馈仅本人可见"
          }}
        </p>
        <div class="b-head">
          <strong>{{ f.body.title }}</strong
          ><span>{{ statusNames[f.body.status] }}</span>
        </div>
        <p>
          {{ f.source.metadata.customer || "客户待归属" }} · 约定日期
          {{ f.body.due || "时间待确认" }} ·
          {{ f.body.responsible || "责任人待确认" }}
        </p>
        <p v-if="f.body.conditions.length">
          条件：{{ f.body.conditions.join("；") }}
        </p>
        <p v-if="f.body.feedback">最近反馈：{{ f.body.feedback }}</p>
        <small>{{ f.body.origin }}</small>
        <div>
          <button @click="emit('meeting', f.source_kind, f.source_id)">
            查看原会谈</button
          ><button v-if="user.role !== 'boss'" @click="editItem(f)">
            记录完成／改期／暂停
          </button>
        </div>
        <form v-if="editing?.id === f.id" @submit.prevent="saveItem">
          <label
            >跟进状态<select v-model="state">
              <option
                v-for="(name, key) in statusNames"
                :key="key"
                :value="key"
              >
                {{ name }}
              </option>
            </select></label
          ><label>约定日期<input v-model="due" type="date" /></label
          ><label
            >原约定责任人（仅记录，不派单）<input
              v-model="responsible"
              maxlength="100" /></label
          ><label
            >完成或变更依据<textarea
              v-model="feedback"
              minlength="2"
              required
            /></label
          ><label
            >本项修订了哪条旧约定（可选）<select v-model="replaces">
              <option value="">不关联旧约定</option>
              <option
                v-for="old in items.filter(
                  (o) =>
                    o.id !== f.id &&
                    o.source.metadata.opportunity_id &&
                    o.source.metadata.opportunity_id ===
                      f.source.metadata.opportunity_id &&
                    !['done', 'cancelled'].includes(o.body.status),
                )"
                :key="old.id"
                :value="old.id"
              >
                {{ old.body.title }}
              </option>
            </select></label
          ><button :disabled="busy">保存跟进反馈</button
          ><button type="button" @click="editing = null">取消</button>
        </form>
        <details v-if="f.history.length">
          <summary>变更历史（{{ f.history.length }}）</summary>
          <p v-for="(h, i) in f.history" :key="i">
            {{ h.actor || "来源修订" }} ·
            {{ h.after?.note || h.note || "已保留上次版本" }} · {{ h.at || "" }}
          </p>
        </details>
      </article>
      <p v-if="!visibleItems.length">当前范围暂无后续事项。</p></template
    >
  </section>
</template>
<style scoped>
.business {
  color: #234438;
}
.b-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  flex-wrap: wrap;
}
.business h1 {
  margin: 6px 0 20px;
}
.business h2 {
  font-size: 18px;
}
.business nav {
  display: flex;
  gap: 10px;
  margin: 14px 0;
}
.business button {
  background: #fff;
  border: 1px solid #cbd7cd;
  border-radius: 7px;
  padding: 9px 13px;
  color: #254c3c;
  cursor: pointer;
  margin: 4px;
}
.business label {
  display: block;
  font-size: 13px;
  margin: 10px 0;
}
.business input,
.business select,
.business textarea {
  display: block;
  width: 100%;
  font: inherit;
  padding: 9px;
  border: 1px solid #c8d5c9;
  border-radius: 7px;
  background: #fff;
  box-sizing: border-box;
  margin: 5px 0;
}
.b-stats {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 15px;
}
.b-stats article {
  padding: 20px;
  background: #e6eee5;
  border-radius: 10px;
}
.b-stats span {
  display: block;
  font-size: 12px;
}
.b-stats strong {
  font-size: 34px;
}
.b-card {
  background: #fff;
  border: 1px solid #dfe7dc;
  border-radius: 12px;
  padding: 20px;
  margin: 16px 0;
  min-width: 0;
}
.b-columns {
  display: grid;
  grid-template-columns: 1fr 1.3fr;
  gap: 18px;
}
.b-row {
  padding: 16px 0;
  border-bottom: 1px solid #e3e9df;
  overflow-wrap: anywhere;
}
.b-muted,
.b-row small {
  color: #6e8073;
  font-size: 12px;
}
.b-notice {
  padding: 12px;
  background: #eaf1e4;
}
.b-error {
  padding: 12px;
  background: #fff0eb;
  color: #8c4031;
}
.b-summary {
  white-space: pre-wrap;
  color: #536e5e;
}
.b-op {
  display: flex;
  flex-direction: column;
  text-align: left;
  width: 100%;
  gap: 6px;
}
.b-op span {
  font-size: 12px;
  color: #6e8073;
}
.b-days {
  max-width: 260px;
}
@media (max-width: 800px) {
  .b-stats {
    grid-template-columns: 1fr 1fr;
  }
  .b-columns {
    grid-template-columns: 1fr;
  }
}
</style>
