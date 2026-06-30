import React, { useCallback, useEffect, useState } from "react";
import {
  AlertCircle, BookOpen, Camera, CheckCircle2, DollarSign, Save, TrendingUp, Users,
} from "lucide-react";
import {
  getEventRetrospective, finalizeEventRetrospective,
  updateEvent, unwrapItem, toUiEvent,
} from "../../../../../services/vkpi/events-api";
import { EVENT_STATUS } from "../shared/constants.js";

const e = React.createElement;

// 结果字段 ROI/线索/视频/复盘 + 活动状态 的真持久化 tab。
// 编辑 → 保存(PATCH /{id} 落 vkpi_events 的 roi/leads/videos/retrospective/status)→
// 回读聚合确认(getEventRetrospective)。刷新后仍在 = 真持久化(非本地 state)。
// 红线:只动 events 表的结果列,零触 viltrox_fit_score。
export default function RetroTab({ ev, token, reload, onEventPatched }) {
  const [retro, setRetro] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [savedAt, setSavedAt] = useState("");
  const [finalizeMsg, setFinalizeMsg] = useState("");

  // 表单态(受控)。空字符串 = 未填(保存时转 null 不塞 0,与后端"待补"口径一致)。
  const [form, setForm] = useState({ roi: "", leads: "", videos: "", retrospective: "", status: "" });
  const [dirty, setDirty] = useState(false);

  const hydrateForm = useCallback((r, evStatus) => {
    const res = (r && r.results) || {};
    setForm({
      roi: res.roi === null || res.roi === undefined ? "" : String(res.roi),
      leads: res.leads === null || res.leads === undefined ? "" : String(res.leads),
      videos: res.videos === null || res.videos === undefined ? "" : String(res.videos),
      retrospective: res.retrospective ? String(res.retrospective) : "",
      status: (r && r.event_status) || evStatus || "planning",
    });
    setDirty(false);
  }, []);

  const loadRetro = useCallback(() => {
    if (!ev?.id) return Promise.resolve();
    setError("");
    return getEventRetrospective(token, ev.id)
      .then((r) => { setRetro(r); hydrateForm(r, ev.status); })
      .catch((err) => { setError(String(err && err.message ? err.message : err)); })
      .finally(() => { setLoading(false); });
  }, [token, ev?.id, ev?.status, hydrateForm]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    getEventRetrospective(token, ev.id)
      .then((r) => { if (alive) { setRetro(r); hydrateForm(r, ev.status); } })
      .catch((err) => { if (alive) setError(String(err && err.message ? err.message : err)); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [token, ev.id, ev.status, hydrateForm]);

  function setField(key, value) {
    setForm((prev) => ({ ...prev, [key]: value }));
    setDirty(true);
    setSavedAt("");
  }

  // 空 → null(不落 0);数字字段非法 → 跳过(不传该字段)。retrospective/status 直传。
  function buildPayload() {
    const out = {};
    const numOrNull = (s) => {
      const t = String(s).trim();
      if (t === "") return null;
      const n = Number(t);
      return Number.isFinite(n) ? n : undefined; // undefined = 不合法,调用方跳过
    };
    const roi = numOrNull(form.roi);
    if (roi !== undefined) out.roi = roi;
    const leads = numOrNull(form.leads);
    if (leads !== undefined) out.leads = leads === null ? null : Math.round(leads);
    const videos = numOrNull(form.videos);
    if (videos !== undefined) out.videos = videos === null ? null : Math.round(videos);
    out.retrospective = form.retrospective || "";
    if (form.status) out.status = form.status;
    return out;
  }

  function save() {
    if (saving) return;
    setSaving(true);
    setError("");
    setSavedAt("");
    const payload = buildPayload();
    updateEvent(token, ev.id, payload)
      .then((res) => {
        // 把保存后的服务器真值同步到父级 events 列表(状态/ROI 反映到 banner / 卡片)。
        const row = unwrapItem(res);
        if (row && onEventPatched) onEventPatched(toUiEvent(row));
        return loadRetro();              // 回读聚合确认(真持久化 → 刷新仍在)
      })
      .then(() => {
        if (reload) reload();            // 同步父级 detail tasks/expenses/invites
        const d = new Date();
        setSavedAt(`${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`);
        setDirty(false);
      })
      .catch((err) => { setError("保存失败:" + String(err && err.message ? err.message : err)); })
      .finally(() => { setSaving(false); });
  }

  function finalize() {
    setFinalizeMsg("");
    finalizeEventRetrospective(token, ev.id)
      .then((res) => {
        if (res && res.finalized) {
          setFinalizeMsg("已定格复盘快照 · " + String(res.finalized_at || ""));
        } else {
          setFinalizeMsg("定格未生效:" + String((res && res.reason) || "未知"));
        }
      })
      .catch((err) => { setFinalizeMsg("定格失败:" + String(err && err.message ? err.message : err)); });
  }

  if (loading) {
    return e("div", { className: "p-12 text-center text-[11px] text-slate-400" }, "加载复盘数据…");
  }

  const budget = (retro && retro.budget) || {};
  const tasks = (retro && retro.tasks) || {};
  const kol = (retro && retro.kol) || {};
  const completeness = retro && typeof retro.completeness === "number" ? Math.round(retro.completeness * 100) : 0;
  const missing = (retro && Array.isArray(retro.missing_data)) ? retro.missing_data : [];

  const statusKeys = Object.keys(EVENT_STATUS);

  return e("div", { className: "p-5 space-y-4" },
    // 状态横幅
    error && e("div", { className: "px-3 py-2 rounded-lg border border-rose-500/30 bg-rose-500/10 text-[11px] text-rose-200 flex items-center gap-1.5" },
      e(AlertCircle, { size: 12 }), error),

    // 完整度 + 自动算指标(只读聚合)
    e("div", { className: "rounded-xl border border-white/[0.06] bg-white/[0.012] p-4" },
      e("div", { className: "flex items-center justify-between mb-3" },
        e("div", { className: "flex items-center gap-1.5 text-[12px] font-semibold text-white" },
          e(BookOpen, { size: 13, className: "text-purple-300" }), "复盘完整度"),
        e("div", { className: "text-[18px] font-bold tabular-nums", style: { color: completeness >= 75 ? "#10b981" : completeness >= 50 ? "#fbbf24" : "#f87171" } },
          completeness, "%")
      ),
      e("div", { className: "h-1.5 rounded-full bg-white/[0.06] overflow-hidden mb-3" },
        e("div", { className: "h-full rounded-full transition-all", style: { width: completeness + "%", background: "linear-gradient(90deg,#a855f7,#06b6d4)" } })
      ),
      missing.length > 0 && e("div", { className: "text-[10.5px] text-amber-300 flex items-center gap-1.5" },
        e(AlertCircle, { size: 11 }), "待补:", missing.join(" · ")),
      missing.length === 0 && e("div", { className: "text-[10.5px] text-emerald-300 flex items-center gap-1.5" },
        e(CheckCircle2, { size: 11 }), "结果数据已补齐"),
      // 自动算的 3 个聚合指标
      e("div", { className: "grid grid-cols-3 gap-3 mt-4" },
        e("div", { className: "rounded-lg border border-white/[0.06] bg-white/[0.02] p-3" },
          e("div", { className: "flex items-center gap-1.5 text-[10px] text-slate-400 mb-1.5" }, e(DollarSign, { size: 10 }), "实际花费"),
          e("div", { className: "text-[15px] font-bold text-white tabular-nums" },
            budget.actual_spend_cents === null || budget.actual_spend_cents === undefined ? "—" : String(budget.actual_spend_cents)),
          e("div", { className: "text-[9.5px] text-slate-500" }, "预算 ", budget.budget_total_cents === null || budget.budget_total_cents === undefined ? "—" : String(budget.budget_total_cents))
        ),
        e("div", { className: "rounded-lg border border-white/[0.06] bg-white/[0.02] p-3" },
          e("div", { className: "flex items-center gap-1.5 text-[10px] text-slate-400 mb-1.5" }, e(CheckCircle2, { size: 10 }), "任务完成"),
          e("div", { className: "text-[15px] font-bold text-white tabular-nums" },
            (tasks.done == null ? "—" : tasks.done), " / ", (tasks.total == null ? "—" : tasks.total)),
          e("div", { className: "text-[9.5px] text-slate-500" }, "待办 ", tasks.pending == null ? "—" : tasks.pending)
        ),
        e("div", { className: "rounded-lg border border-white/[0.06] bg-white/[0.02] p-3" },
          e("div", { className: "flex items-center gap-1.5 text-[10px] text-slate-400 mb-1.5" }, e(Users, { size: 10 }), "KOL 邀约"),
          e("div", { className: "text-[15px] font-bold text-white tabular-nums" }, kol.invited == null ? "—" : kol.invited),
          e("div", { className: "text-[9.5px] text-slate-500" }, "建时 ", kol.invited_kols_count == null ? "—" : kol.invited_kols_count)
        )
      )
    ),

    // 结果回填(可编辑 + 真持久化)
    e("div", { className: "rounded-xl border border-white/[0.06] bg-white/[0.012] p-4 space-y-3" },
      e("div", { className: "flex items-center justify-between" },
        e("div", { className: "flex items-center gap-1.5 text-[12px] font-semibold text-white" },
          e(TrendingUp, { size: 13, className: "text-emerald-300" }), "结果回填"),
        e("div", { className: "text-[9.5px] text-slate-500" }, "保存即落库 · 刷新仍在")
      ),
      // 状态选择
      e("div", null,
        e("label", { className: "text-[10px] text-slate-400 mb-1 block" }, "活动状态"),
        e("select", {
          value: form.status,
          onChange: (evt) => setField("status", evt.target.value),
          className: "w-full px-3 py-2 rounded-md bg-white/[0.02] border border-white/[0.08] text-[11.5px] text-white focus:outline-none focus:border-purple-500/40",
        }, statusKeys.map((k) => e("option", { key: k, value: k, style: { background: "#0a0a0d" } }, (EVENT_STATUS[k] && EVENT_STATUS[k].label) || k)))
      ),
      // ROI / leads / videos 数字三列
      e("div", { className: "grid grid-cols-3 gap-3" },
        e("div", null,
          e("label", { className: "text-[10px] text-slate-400 mb-1 block" }, "ROI (x)"),
          e("input", {
            type: "number", step: "0.1", value: form.roi,
            onChange: (evt) => setField("roi", evt.target.value),
            placeholder: "—",
            className: "w-full px-3 py-2 rounded-md bg-white/[0.02] border border-white/[0.08] text-[11.5px] text-white tabular-nums placeholder-slate-600 focus:outline-none focus:border-emerald-500/40",
          })
        ),
        e("div", null,
          e("label", { className: "text-[10px] text-slate-400 mb-1 block" }, "线索数"),
          e("input", {
            type: "number", step: "1", value: form.leads,
            onChange: (evt) => setField("leads", evt.target.value),
            placeholder: "—",
            className: "w-full px-3 py-2 rounded-md bg-white/[0.02] border border-white/[0.08] text-[11.5px] text-white tabular-nums placeholder-slate-600 focus:outline-none focus:border-emerald-500/40",
          })
        ),
        e("div", null,
          e("label", { className: "text-[10px] text-slate-400 mb-1 block flex items-center gap-1" }, e(Camera, { size: 9 }), "视频数"),
          e("input", {
            type: "number", step: "1", value: form.videos,
            onChange: (evt) => setField("videos", evt.target.value),
            placeholder: "—",
            className: "w-full px-3 py-2 rounded-md bg-white/[0.02] border border-white/[0.08] text-[11.5px] text-white tabular-nums placeholder-slate-600 focus:outline-none focus:border-emerald-500/40",
          })
        )
      ),
      // 复盘文本
      e("div", null,
        e("label", { className: "text-[10px] text-slate-400 mb-1 block" }, "复盘总结 (高光 / 痛点 / 改进)"),
        e("textarea", {
          value: form.retrospective,
          onChange: (evt) => setField("retrospective", evt.target.value),
          rows: 5,
          placeholder: "投入 vs 产出、做对了什么、下次怎么改…",
          className: "w-full px-3 py-2 rounded-md bg-white/[0.02] border border-white/[0.08] text-[11.5px] text-slate-100 placeholder-slate-600 focus:outline-none focus:border-purple-500/40 resize-y leading-relaxed",
        })
      ),
      // 保存
      e("div", { className: "flex items-center justify-between pt-1" },
        e("div", { className: "text-[10px] text-slate-500" },
          savedAt && !dirty && e("span", { className: "text-emerald-300 flex items-center gap-1" }, e(CheckCircle2, { size: 11 }), "已保存 ", savedAt),
          dirty && e("span", { className: "text-amber-300" }, "有未保存改动")
        ),
        e("button", {
          onClick: save,
          disabled: saving || !dirty,
          className: `px-3.5 py-2 rounded-md text-[11px] font-medium flex items-center gap-1.5 transition-all ${saving || !dirty ? "bg-white/[0.04] text-slate-500 cursor-not-allowed" : "bg-emerald-500/90 hover:bg-emerald-500 text-white"}`,
        }, e(Save, { size: 12 }), saving ? "保存中…" : "保存复盘")
      )
    ),

    // 定格快照(可选)
    e("div", { className: "rounded-lg border border-white/[0.06] bg-white/[0.012] p-3 flex items-center justify-between" },
      e("div", null,
        e("div", { className: "text-[11px] text-slate-300 font-medium" }, "定格复盘快照"),
        e("div", { className: "text-[9.5px] text-slate-500" }, finalizeMsg || "把当前复盘存为一行历史快照(可多次定格)")
      ),
      e("button", {
        onClick: finalize,
        className: "px-3 py-1.5 rounded-md border border-purple-500/30 bg-purple-500/[0.08] text-[10.5px] text-purple-200 hover:bg-purple-500/[0.15]",
      }, "定格快照")
    )
  );
}
