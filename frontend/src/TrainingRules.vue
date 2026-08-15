<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api, post } from "./api";
type Row = Record<string, any>;
const cards = ref<Row[]>([]),
  current = ref<Row | null>(null),
  busy = ref(false),
  error = ref(""),
  notice = ref("");
async function load() {
  cards.value = (await api("/workspace/quality/policy")).cards;
}
function choose(card: Row) {
  current.value = JSON.parse(JSON.stringify(card));
  error.value = "";
  notice.value = "";
}
async function save() {
  busy.value = true;
  error.value = "";
  notice.value = "";
  try {
    const card = current.value!;
    await post("/workspace/quality/policy", {
      base_id: card.id,
      name: card.name,
      product_notes: card.product_notes,
      rules: card.rules,
    });
    await load();
    choose(cards.value.find((c) => c.stage === card.stage)!);
    notice.value = "已发布新版本；历史质检继续保留原规则，后续整理采用新版。";
  } catch (e) {
    error.value = e instanceof Error ? e.message : "发布失败";
  } finally {
    busy.value = false;
  }
}
onMounted(async () => {
  try {
    await load();
    if (cards.value.length) choose(cards.value[0]);
  } catch (e) {
    error.value = e instanceof Error ? e.message : "读取失败";
  }
});
</script>
<template>
  <section class="training">
    <h1>销售培训规则</h1>
    <p>
      公司统一配置一次，销售日常无需逐场选择评分卡。默认五个场景均为示例，请按实际培训制度调整。
    </p>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-if="notice" role="status">{{ notice }}</p>
    <nav aria-label="培训场景">
      <button v-for="card in cards" :key="card.id" @click="choose(card)">
        {{ card.stage }} · v{{ card.version }}
      </button>
    </nav>
    <form v-if="current" @submit.prevent="save">
      <label
        >规则集名称<input
          v-model="current.name"
          required
          maxlength="150" /></label
      ><label
        >已确认的产品能力与承诺边界<textarea
          v-model="current.product_notes"
          rows="5"
          placeholder="没有已确认资料时留空，相关承诺将保留无法判断。"
        />
      </label>
      <p>
        权重合计：{{
          current.rules.reduce((n: number, r: Row) => n + Number(r.weight), 0)
        }}
        / 100
      </p>
      <article v-for="rule in current.rules" :key="rule.id">
        <label>培训要求<input v-model="rule.title" required /></label
        ><label
          >权重<input
            type="number"
            v-model.number="rule.weight"
            min="1"
            max="100"
            required /></label
        ><label>评价内容<textarea v-model="rule.criterion" required /></label
        ><label
          >适用条件<textarea v-model="rule.applicability" required /></label
        ><label>达标依据<textarea v-model="rule.pass_anchor" required /></label
        ><label
          >部分达标依据<textarea v-model="rule.partial_anchor" required />
        </label>
      </article>
      <button :disabled="busy">发布培训规则新版本</button>
    </form>
  </section>
</template>
<style scoped>
.training {
  max-width: 850px;
}
.training nav {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin: 20px 0;
}
.training label {
  display: block;
  margin: 12px 0;
  font-size: 13px;
}
.training input,
.training textarea {
  display: block;
  width: 100%;
  box-sizing: border-box;
  border: 1px solid #cad6cb;
  border-radius: 6px;
  background: #fff;
  padding: 10px;
  font: inherit;
  margin-top: 6px;
}
.training article {
  background: #fff;
  border: 1px solid #dbe3d9;
  border-radius: 10px;
  padding: 15px;
  margin: 15px 0;
}
.training button {
  border: 1px solid #b9cabc;
  background: #fff;
  border-radius: 7px;
  padding: 10px 15px;
  color: #234c3e;
  cursor: pointer;
}
.training button[type="submit"],
.training form > button {
  background: #234c3e;
  color: #fff;
}
</style>
