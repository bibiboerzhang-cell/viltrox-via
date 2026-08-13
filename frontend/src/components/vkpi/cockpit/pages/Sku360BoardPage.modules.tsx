import React from "react";
import { formatLocal } from "../../lib/timeLocal";
import { EmptyLine } from "./MarketVoicePage.modules";
import { platformBadge } from "./MarketVoicePage.dialogs";
import { asArray, asRow, fmtZhCompact, num, str, type Row } from "./Sku360BoardPage.charts";
import { kolHumanDisplayName } from "../lib/kolIdentity";

// SKU 360° · 板块页范式辅助件(Sku360BoardPage 专用,页内拆件不入公共桶)。
//   通用骨架件(ModuleCard/PendingCard/EmptyLine/ErrorCard/LoadingLine/KpiCard)直接
//   复用 MarketVoicePage.modules(金样板放行「引组件」);本文件只放 SKU 页专属件:
//   MODULE_SOURCES 溯源注册表(label=真实端点/表名,rows=2026-07-12 本地实测口径,禁编造)/
//   CatalogBody 产品档案 / KnowledgeBody 知识库画像 / AnglesBody 推广方向 /
//   FitBody 内容契合 / ContentRowLine+ContentsBody 提及内容清单。
// 红线:本文件零直连网络(取数住 page 层);评分/契合只读展示绝不写回;
//   颜色全 token 类零写死色;零 opacity 修饰类;诚实空态;卡面零技术术语
//   (表名/端点/模型名只进 SrcChip rows / tooltip);知识库画像=AI 生成 →
//   卡面明标「AI 生成」徽,来源模型/批次/生成时间进 SrcChip 与卡脚。

/* ============ 溯源注册表(金样板 MODULE_SOURCES 同构;行数=2026-07-12 本地实测) ============ */
export const MODULE_SOURCES: Record<string, { label: string; rows: Array<[string, string]> }> = {
  kpiS: {
    label: "sku/{sku}/profile · 实时聚合",
    rows: [
      ["口径", "提及内容/创作者/曝光/互动率 = 别名命中内容实时聚合(请求时现扫,非时序)"],
      ["匹配", "vkpi_product_aliases(1,655 行)归一化 token 边界命中,置信 ≥0.5"],
      ["内容源", "vkpi_analysis_cache 深析产物(497 就绪)+ vkpi_kol_video_evidence 标题(2,175 活跃)"],
      ["趋势线", "无逐 SKU 历史时序端点 → 四卡诚实虚线,零环比药丸,绝不编 series"],
    ],
  },
  catalog: {
    label: "vkpi_products",
    rows: [
      ["主表", "vkpi_products(369 行 · official 183 / priced 158 / no_price_set 28 · 2026-07-12 本地实测)"],
      ["规格", "specs_json 目录规格 + fit_tags_json 适配标签(原样展示,缺=不摆)"],
      ["解析", "SKU 码 / 别名 / 型号片段三级解析(vkpi_product_aliases 兜底)"],
      ["更新", "catalog_updated_at = 目录行更新日(日粒度)"],
    ],
  },
  knowledge: {
    label: "vkpi_product_persona · AI 生成",
    rows: [
      ["知识库表", "vkpi_product_persona(353/369 SKU 已生成 · 2026-07-12 本地实测)"],
      ["产出方式", "离线批跑 LLM 生成(model=gpt-5.4-mini-2026-03-17 · source=llm_persona_v1,行级字段原样透出)"],
      ["置信度", "该表无数值置信度列 → 如实不摆置信徽,只标模型与生成时间"],
      ["诚实", "未生成画像的 SKU = 空态如实标,绝不现编"],
    ],
  },
  angles: {
    label: "vkpi_product_persona · AI 生成",
    rows: [
      ["字段", "promotion_angles / ideal_creator_types / avoid_types(知识库同行,LLM 离线批产)"],
      ["产出方式", "model=gpt-5.4-mini-2026-03-17 · source=llm_persona_v1 · 行级 generated_at"],
      ["性质", "推广方向建议 = AI 生成参考,不构成自动执行指令"],
    ],
  },
  contents: {
    label: "vkpi_kol_video_evidence · 深析产物",
    rows: [
      ["证据表", "vkpi_kol_video_evidence(2,175 活跃有标题 · 2026-07-12 本地实测)"],
      ["深析", "vkpi_analysis_cache 视频深析产物(497 就绪)· 产品识别 > 在场 > 标题 > 品牌露出 > 摘要 五级命中"],
      ["排序", "按互动(赞+评)降序 · 单次最多回传 100 条"],
      ["时间", "发布日期为源数据日粒度,原样绝对展示"],
    ],
  },
  fit: {
    label: "vkpi_analysis_cache · content_fit 缓存",
    rows: [
      ["缓存", "content_fit 判断缓存(234 条就绪,其中标注到具体 SKU 仅 1 条 · 2026-07-12 本地实测)"],
      ["产出方式", "AI 判断产物只读透传 · 行级 confidence 原样展示"],
      ["红线", "只读展示,本页零写回零打分"],
    ],
  },
  creators: {
    label: "sku/{sku}/profile · top_creators",
    rows: [
      ["口径", "命中该 SKU 的内容按创作者聚合,曝光合计降序 TOP5"],
      ["身份", "创作者 = vkpi_kol_pool 池内档案,可跳 KOL 档案页"],
    ],
  },
  voice: {
    label: "vkpi_comments · 别名命中",
    rows: [
      ["评论库", "vkpi_comments(本地 875 行 · 2026-07-12 实测)—— 覆盖有限,零命中如实空"],
      ["扫描", "点赞降序扫前 5,000 条,别名归一化命中"],
      ["语言", "language_detected 行级字段原样展示"],
    ],
  },
  bh: {
    label: "vkpi_bh_reviews",
    rows: [
      ["口碑表", "vkpi_bh_reviews(表已建 · 本地 0 行 · 2026-07-12 实测)—— 已接待喂数"],
      ["命中", "SKU 精确匹配 + 商品名别名命中双轨"],
    ],
  },
  candidates: {
    label: "product-campaign-card · 启发式",
    rows: [
      ["候选源", "vkpi_kol_pool 档案语料 × vkpi_product_spec_facts(369 行)别名/规格命中打分"],
      ["市场信号", "vkpi_competitor_signals(49 行 · 2026-07-12 本地实测)"],
      ["性质", "启发式圈选分 · 候选必须带证据 · 人工复核后才可建联(端点自带 human_approval_required)"],
      ["红线", "纯读零写 · 不触发采集 · 不自动建项目"],
    ],
  },
};

/* ============ 展示原子 ============ */

export function KV({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-line py-1.5 text-[11.5px] last:border-0">
      <span className="flex-none text-muted">{label}</span>
      <span className="min-w-0 break-words text-right text-ink-2">{value}</span>
    </div>
  );
}

function chipsLine(label: string, values: string[], cls: string) {
  if (values.length === 0) return null;
  return (
    <div className="space-y-1">
      <div className="text-[9.5px] font-semibold uppercase tracking-[0.14em] text-muted">{label}</div>
      <div className="flex flex-wrap gap-1.5">
        {values.slice(0, 6).map((v, i) => (
          <span key={i} className={`rounded-full border px-2 py-0.5 text-[10.5px] ${cls}`} title={v}>
            {v.length > 42 ? `${v.slice(0, 42)}…` : v}
          </span>
        ))}
        {values.length > 6 ? <span className="rounded-full border border-line px-2 py-0.5 text-[10.5px] text-muted">+{values.length - 6}</span> : null}
      </div>
    </div>
  );
}

const strList = (v: unknown): string[] =>
  asArray(v)
    .map((x) => (typeof x === "string" ? x : str(asRow(x)?.text) || str(asRow(x)?.label)))
    .filter(Boolean);

/** AI 生成徽(知识库/推广方向共用;模型名进 tooltip 与 SrcChip,卡面不摆术语) */
export function AiBadge({ model, generatedAt }: { model: string; generatedAt: string }) {
  return (
    <span
      className="flex-none rounded-[5px] border border-accent-2 px-1.5 py-px text-[8.5px] font-bold tracking-[0.05em] text-accent-2"
      title={`AI 生成${model ? ` · 模型 ${model}` : ""}${generatedAt ? ` · 生成于 ${formatLocal(generatedAt, { year: "numeric" })}` : ""}`}
    >
      AI 生成
    </span>
  );
}

/* ============ 产品档案(目录 KV + 描述折叠 + 规格 + 适配标签 + 官网直跳) ============ */
export function CatalogBody({ product }: { product: Row }) {
  const [descOpen, setDescOpen] = React.useState(false);
  const name = str(product.model_name) || str(product.marketing_name) || str(product.sku) || "—";
  const marketing = str(product.marketing_name);
  const desc = str(product.description);
  const url = str(product.product_url);
  const specs = asRow(product.specs) || {};
  const fitTags = strList(product.fit_tags);
  const specEntries = Object.entries(specs)
    .map(([k, v]) => [k, str(v) || (num(v) !== null ? String(num(v)) : "")] as [string, string])
    .filter(([, v]) => Boolean(v))
    .slice(0, 8);
  return (
    <div>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="text-[14px] font-semibold tracking-[-0.01em] text-ink">{name}</span>
        {marketing && marketing !== name ? <span className="text-[11px] text-muted">{marketing}</span> : null}
        <span className="flex-none rounded-[5px] border border-line px-1.5 py-px font-mono text-[9px] text-muted">{str(product.sku) || "—"}</span>
      </div>
      <div className="mt-2">
        <KV label="类目" value={[str(product.category_main), str(product.category_detail)].filter(Boolean).join(" / ") || "—"} />
        <KV label="系列 / 卡口" value={[str(product.series), str(product.mount)].filter(Boolean).join(" / ") || "—"} />
        <KV label="售价" value={num(product.price_usd) !== null ? `$${num(product.price_usd)}` : <span className="text-muted">未定价</span>} />
        <KV label="目录状态" value={str(product.status) || "—"} />
        <KV
          label="目录更新"
          value={
            str(product.catalog_updated_at) ? (
              <span className="font-mono" title="目录行更新日(源数据日粒度 · UTC)">
                {str(product.catalog_updated_at)}
              </span>
            ) : (
              "—"
            )
          }
        />
      </div>
      {desc ? (
        <div className="mt-2 text-[11.5px] leading-relaxed text-ink-2">
          {descOpen || desc.length <= 200 ? desc : `${desc.slice(0, 200)}…`}
          {desc.length > 200 ? (
            <button type="button" onClick={() => setDescOpen((v) => !v)} className="ml-1.5 text-[10px] text-accent transition-colors hover:text-accent-hover">
              {descOpen ? "收起" : "展开"}
            </button>
          ) : null}
        </div>
      ) : null}
      {specEntries.length > 0 ? (
        <div className="mt-2">
          <div className="mb-0.5 text-[9.5px] font-semibold uppercase tracking-[0.14em] text-muted">目录规格</div>
          {specEntries.map(([k, v]) => (
            <KV key={k} label={k} value={v.length > 60 ? `${v.slice(0, 60)}…` : v} />
          ))}
        </div>
      ) : null}
      {fitTags.length > 0 ? <div className="mt-2">{chipsLine("适配标签", fitTags, "border-line text-ink-2")}</div> : null}
      {url ? (
        <div className="mt-2.5 border-t border-line pt-2.5">
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className="rounded-lg border border-line px-2.5 py-1.5 text-[11px] text-ink-2 transition-colors hover:border-accent hover:bg-accent-soft hover:text-accent"
          >
            官网产品页 ↗
          </a>
        </div>
      ) : null}
    </div>
  );
}

/* ============ 知识库画像(AI 生成:它是什么 / 适合谁 / 核心规格点 / 内容垂类) ============ */
export function KnowledgeBody({ persona }: { persona: Row | null }) {
  if (!persona) return <EmptyLine text="知识库未生成该 SKU 画像(离线批产未覆盖,如实空态)。" />;
  const whatIs = str(persona.what_is);
  const idealPersona = str(persona.ideal_persona);
  const keySpecs = asRow(persona.key_specs_json) || {};
  const verticals = strList(persona.verticals_json);
  const specChips = Object.entries(keySpecs)
    .map(([k, v]) => `${k} ${str(v) || (num(v) !== null ? String(num(v)) : "")}`.trim())
    .filter(Boolean);
  const model = str(persona.model);
  const generatedAt = str(persona.generated_at);
  return (
    <div className="space-y-2.5">
      <div className="flex flex-wrap items-center gap-1.5">
        <AiBadge model={model} generatedAt={generatedAt} />
        {str(persona.category) ? <span className="rounded-full border border-line px-2 py-0.5 text-[10px] text-muted">{str(persona.category)}</span> : null}
      </div>
      {whatIs ? (
        <div>
          <div className="mb-0.5 text-[9.5px] font-semibold uppercase tracking-[0.14em] text-muted">它是什么</div>
          <p className="text-[11.5px] leading-relaxed text-ink-2">{whatIs.length > 240 ? `${whatIs.slice(0, 240)}…` : whatIs}</p>
        </div>
      ) : null}
      {idealPersona ? (
        <div>
          <div className="mb-0.5 text-[9.5px] font-semibold uppercase tracking-[0.14em] text-muted">适合谁</div>
          <p className="text-[11.5px] leading-relaxed text-ink-2">{idealPersona.length > 240 ? `${idealPersona.slice(0, 240)}…` : idealPersona}</p>
        </div>
      ) : null}
      {chipsLine("核心规格点", specChips, "border-line text-ink-2")}
      {chipsLine("内容垂类", verticals, "border-info bg-info-soft text-info")}
      {!whatIs && !idealPersona && specChips.length === 0 && verticals.length === 0 ? (
        <EmptyLine text="画像行存在但关键字段为空(不以含糊文案充数)。" />
      ) : null}
      {generatedAt ? (
        <div className="font-mono text-[9px] text-muted" title="UTC 存 · 按浏览器时区显示">
          生成于 {formatLocal(generatedAt, { year: "numeric" })}
        </div>
      ) : null}
    </div>
  );
}

/* ============ 推广方向(AI 生成:切入角 / 优先创作者类型 / 规避类型) ============ */
export function AnglesBody({ persona }: { persona: Row | null }) {
  if (!persona) return <EmptyLine text="知识库未生成该 SKU 画像 → 推广方向暂无(如实空态)。" />;
  const angles = strList(persona.promotion_angles_json);
  const creatorTypes = strList(persona.ideal_creator_types_json);
  const avoidTypes = strList(persona.avoid_types_json);
  const model = str(persona.model);
  const generatedAt = str(persona.generated_at);
  if (angles.length === 0 && creatorTypes.length === 0 && avoidTypes.length === 0) {
    return <EmptyLine text="画像行存在但推广方向字段为空(不以含糊文案充数)。" />;
  }
  return (
    <div className="space-y-2.5">
      <div className="flex flex-wrap items-center gap-1.5">
        <AiBadge model={model} generatedAt={generatedAt} />
        <span className="rounded-full border border-line px-2 py-0.5 text-[10px] text-muted">参考建议 · 非自动执行</span>
      </div>
      {angles.length > 0 ? (
        <div className="space-y-1">
          <div className="text-[9.5px] font-semibold uppercase tracking-[0.14em] text-muted">切入角</div>
          {angles.slice(0, 5).map((a, i) => (
            <div key={i} className="flex gap-2 rounded-[9px] border border-line px-2.5 py-1.5 text-[11px] leading-relaxed text-ink-2">
              <span className="flex-none font-mono text-[10px] text-accent">{i + 1}</span>
              <span className="min-w-0">{a.length > 120 ? `${a.slice(0, 120)}…` : a}</span>
            </div>
          ))}
        </div>
      ) : null}
      {chipsLine("优先创作者类型", creatorTypes, "border-good bg-good-soft text-good")}
      {chipsLine("规避类型", avoidTypes, "border-warn bg-warn-soft text-warn")}
    </div>
  );
}

/* ============ 内容契合(AI 判断缓存只读:判定徽 + 置信 + 理由) ============ */

const VERDICT_META: Record<string, { label: string; cls: string }> = {
  fit: { label: "契合", cls: "border-good bg-good-soft text-good" },
  partial_fit: { label: "部分契合", cls: "border-warn bg-warn-soft text-warn" },
  not_fit: { label: "不契合", cls: "border-crit bg-crit-soft text-crit" },
};

export function FitBody({ matches }: { matches: Row[] }) {
  const rows = matches.map((m) => asRow(m)).filter((m): m is Row => Boolean(m));
  if (rows.length === 0) return <EmptyLine text="暂无针对该 SKU 的创作者契合判断缓存(判断按需生成,零命中如实空)。" />;
  return (
    <div className="space-y-1.5">
      {rows.slice(0, 8).map((f, i) => {
        const verdict = VERDICT_META[str(f.fit_verdict)] || { label: str(f.fit_verdict) || "—", cls: "border-line text-ink-2" };
        const conf = num(f.confidence);
        const reasons = strList(f.fit_reasons);
        return (
          <div key={i} className="rounded-lg border border-line px-3 py-2">
            <div className="flex flex-wrap items-center gap-2">
              <span className={`flex-none rounded-md border px-2 py-0.5 text-[10px] ${verdict.cls}`}>{verdict.label}</span>
              <span className="min-w-0 truncate text-[12px] text-ink">{kolHumanDisplayName(f)}</span>
              {str(f.platform) ? (
                <span className="flex-none rounded-[5px] bg-accent-soft px-1.5 py-0.5 text-[8.5px] font-semibold text-ink-2">{platformBadge(str(f.platform))}</span>
              ) : null}
              {conf !== null ? (
                <span className="ml-auto flex-none font-mono text-[9.5px] text-muted" title="AI 判断行级置信度,原样展示">
                  置信 {Math.round(conf * 100)}%
                </span>
              ) : null}
            </div>
            {reasons.length > 0 ? (
              <ul className="mt-1 list-disc pl-5 text-[10.5px] leading-relaxed text-ink-2">
                {reasons.slice(0, 2).map((r, j) => (
                  <li key={j}>{r.length > 110 ? `${r.slice(0, 110)}…` : r}</li>
                ))}
              </ul>
            ) : null}
            {str(f.updated_at) ? (
              <div className="mt-1 font-mono text-[9px] text-muted" title="判断缓存更新时间(UTC 存 · 按浏览器时区显示)">
                {formatLocal(str(f.updated_at), { year: "numeric" })}
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

/* ============ 提及内容清单(行:平台徽 + 标题 + 创作者 + 曝光/互动 + 命中依据 + 日期 + ↗) ============ */

export const MATCH_FIELD_LABEL: Record<string, string> = {
  final_v1_products: "深析·产品识别",
  final_v1_presence: "深析·产品在场",
  evidence_title: "标题命中",
  final_v1_brand_exposure: "深析·品牌露出",
  final_v1_summary: "深析·摘要",
};

export function ContentRowLine({ item, index, onOpen }: { item: Row; index: number; onOpen: (i: number) => void }) {
  const match = asRow(item.match);
  const kol = asRow(item.kol);
  const url = str(item.content_url);
  const fieldLabel = MATCH_FIELD_LABEL[str(match?.field)] || str(match?.field) || "—";
  return (
    <div
      className="group flex min-w-0 cursor-pointer items-center gap-2 border-b border-line py-2 last:border-0"
      role="button"
      tabIndex={0}
      onClick={() => onOpen(index)}
      onKeyDown={(ev) => {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          onOpen(index);
        }
      }}
    >
      <span className="min-w-[46px] flex-none rounded-[5px] bg-accent-soft px-1.5 py-0.5 text-center text-[8.5px] font-semibold text-ink-2">
        {platformBadge(str(item.platform))}
      </span>
      <span className="min-w-0 flex-1 truncate text-[11.5px] text-ink-2 transition-colors group-hover:text-accent" title={str(item.title)}>
        {str(item.title) || "—"}
      </span>
      <span className="hidden max-w-[110px] flex-none truncate text-[10px] text-muted sm:block">
        {kolHumanDisplayName(kol, "—")}
      </span>
      <span className="flex-none font-mono text-[9.5px] text-muted" title="曝光(实测播放)">
        {fmtZhCompact(num(item.view_count))}
      </span>
      <span
        className="hidden flex-none rounded border border-line px-1.5 py-0.5 text-[8.5px] text-muted md:block"
        title={`命中依据:${fieldLabel} · 命中别名「${str(match?.alias) || "—"}」${item.has_deep_analysis === true ? "" : " · 仅标题层"}`}
      >
        {fieldLabel}
      </span>
      <span className="flex-none font-mono text-[9.5px] text-muted" title="发布日期(源数据日粒度)">
        {str(item.posted_at) || "—"}
      </span>
      {url ? (
        <a
          className="vkpi-prov-pchip vkpi-prov-pchip--ext vkpi-prov-pchip--mini flex-none"
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          title="直跳原内容"
          onClick={(ev) => ev.stopPropagation()}
        >
          ↗
        </a>
      ) : null}
    </div>
  );
}

export function ContentsBody({
  items,
  note,
  onOpenDetail,
  onOpenList,
}: {
  items: Row[];
  note: string;
  onOpenDetail: (i: number) => void;
  onOpenList: () => void;
}) {
  if (items.length === 0) {
    return <EmptyLine text={note || "该 SKU 暂无匹配内容(深析与标题均未命中别名,如实空态)。"} />;
  }
  return (
    <div>
      {items.slice(0, 6).map((it, i) => (
        <ContentRowLine key={i} item={it} index={i} onOpen={onOpenDetail} />
      ))}
      {items.length > 6 ? (
        <button
          type="button"
          onClick={onOpenList}
          className="mt-2 w-full rounded-[9px] border border-dashed border-line-strong px-3 py-1.5 text-center text-[10.5px] text-accent transition-colors hover:border-accent hover:bg-accent-soft"
        >
          ≡ 查看全量({items.length} 条)
        </button>
      ) : null}
    </div>
  );
}
