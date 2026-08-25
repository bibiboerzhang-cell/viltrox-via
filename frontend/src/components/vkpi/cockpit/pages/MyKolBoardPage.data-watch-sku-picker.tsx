import React from "react";

import type {
  VkpiDataWatchSkuCandidate,
  VkpiKolPoolVideoRow,
} from "../../../../services/vkpi/myKolBoard-api";
import { listSku360Options } from "../../../../services/vkpi/sku360-api";
import type { DataWatchSubmitIntent } from "./MyKolBoardPage.data-watch";

// 内容墙 sku_required 诚实回落：让员工当场明确选 SKU，再二次提交。
// 不默认勾选、不用候选第一个当真值，最多 5 个（与后端围栏一致）。

export interface PendingDataWatchSkuChoice {
  video: VkpiKolPoolVideoRow;
  candidates: VkpiDataWatchSkuCandidate[];
  intent?: Exclude<DataWatchSubmitIntent, "auto">;
}

let skuDirectoryCache: Array<{ sku: string; name: string }> | null = null;

function parseManualSkus(value: string): string[] {
  return [...new Set(value.split(/[,，;；\n]+/).map((item) => item.trim()).filter(Boolean))];
}

function candidateRows(candidates: VkpiDataWatchSkuCandidate[]): Array<{ sku: string; name: string; evidence: string }> {
  const rows = new Map<string, { name: string; evidence: string }>();
  for (const candidate of candidates) {
    const sku = String(candidate.sku_code || "").trim();
    if (!sku || rows.has(sku)) continue;
    const labels = (candidate.modalities || []).map((value) => ({ visual: "画面", text: "字幕·文字", voice: "口播", unspecified: "未注明" }[value] || value));
    rows.set(sku, {
      name: String(candidate.sku_name || "").trim(),
      evidence: candidate.match_source === "final_v1_lens_evidence_v2" ? `深析候选${labels.length ? `：${labels.join("/")}` : ""}` : "",
    });
  }
  return [...rows.entries()].map(([sku, detail]) => ({ sku, ...detail }));
}

export function DataWatchSkuPicker({
  pending,
  apiToken = "",
  busy,
  onCancel,
  onSubmit,
}: {
  pending: PendingDataWatchSkuChoice | null;
  apiToken?: string;
  busy: boolean;
  onCancel: () => void;
  onSubmit: (productSkus: string[], intent: Exclude<DataWatchSubmitIntent, "auto">) => void;
}) {
  const evidenceId = Number(pending?.video.evidence_id ?? pending?.video.id) || 0;
  const candidates = React.useMemo(() => candidateRows(pending?.candidates || []), [pending?.candidates]);
  const [selected, setSelected] = React.useState<Set<string>>(new Set());
  const [manualInput, setManualInput] = React.useState("");
  const [directory, setDirectory] = React.useState<Array<{ sku: string; name: string }>>(skuDirectoryCache || []);
  const datalistId = React.useId();
  const rootRef = React.useRef<HTMLDivElement | null>(null);

  React.useEffect(() => {
    setSelected(new Set());
    setManualInput("");
  }, [evidenceId, pending?.intent]);
  React.useEffect(() => {
    if (!pending) return;
    const frame = window.requestAnimationFrame(() => {
      const root = rootRef.current;
      if (!root) return;
      root.scrollIntoView?.({ behavior: "smooth", block: "center" });
      root.querySelector<HTMLInputElement>("input:not(:disabled)")?.focus({ preventScroll: true });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [busy, evidenceId, pending]);
  React.useEffect(() => {
    if (!pending || !apiToken) return;
    if (skuDirectoryCache) {
      setDirectory(skuDirectoryCache);
      return;
    }
    let alive = true;
    listSku360Options(apiToken, "", 200)
      .then((rows) => {
        if (!alive) return;
        skuDirectoryCache = rows.filter((row) => row.sku).map((row) => ({ sku: row.sku, name: row.marketing_name || row.model_name }));
        setDirectory(skuDirectoryCache);
      })
      .catch(() => { /* 可精确手填，服务端仍会校验；目录读失败不伪造选项。 */ });
    return () => { alive = false; };
  }, [apiToken, pending]);
  React.useEffect(() => {
    if (!pending || !apiToken) return;
    const query = manualInput.trim();
    if (!query) return;
    let alive = true;
    const timer = window.setTimeout(() => {
      listSku360Options(apiToken, query, 30)
        .then((rows) => {
          if (!alive) return;
          const found = rows.filter((row) => row.sku).map((row) => ({ sku: row.sku, name: row.marketing_name || row.model_name }));
          setDirectory((current) => {
            const merged = new Map(current.map((row) => [row.sku, row]));
            found.forEach((row) => merged.set(row.sku, row));
            return [...merged.values()];
          });
        })
        .catch(() => { /* 输入仍可提交给服务端做精确 SKU 校验。 */ });
    }, 250);
    return () => { alive = false; window.clearTimeout(timer); };
  }, [apiToken, manualInput, pending]);

  if (!pending) return null;
  const title = String(pending.video.title || pending.video.video_title || `视频 #${evidenceId}`);
  const manualSkus = parseManualSkus(manualInput);
  const chosenSkus = [...new Set([...selected, ...manualSkus])];
  const selectionValid = chosenSkus.length > 0 && chosenSkus.length <= 5;
  const submitIntent: Exclude<DataWatchSubmitIntent, "auto"> = (
    pending.intent === "confirm_detected"
    && manualSkus.length === 0
    && chosenSkus.length === 1
    && candidates.length === 1
    && chosenSkus[0] === candidates[0].sku
  ) ? "confirm_detected" : "manual";
  const submitLabelIntent: Exclude<DataWatchSubmitIntent, "auto"> = (
    pending.intent === "confirm_detected"
    && manualSkus.length === 0
    && candidates.length === 1
  ) ? "confirm_detected" : submitIntent;
  const toggle = (sku: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(sku)) next.delete(sku);
      else if (next.size < 5) next.add(sku);
      return next;
    });
  };

  return (
    <div
      ref={rootRef}
      className="mb-2 rounded-[10px] border border-accent bg-accent-soft px-3 py-2.5"
      role="group"
      aria-label="为数据关注选择 SKU"
      aria-describedby={`${datalistId}-state`}
      data-vkpi-data-watch-sku-picker={evidenceId}
    >
      <div className="text-[12px] font-semibold text-ink">第 2 步 · 确认这条视频对应的单品</div>
      <div className="mt-0.5 truncate text-[11px] text-muted" title={title}>{title}</div>
      <div id={`${datalistId}-state`} className="mt-1 text-[10.5px] leading-4 text-accent">
        当前还没有进入单品播放；确认成功后会自动打开对应 SKU，并定位到这条视频。
      </div>
      <div className="mt-2 grid gap-1.5 sm:grid-cols-2 xl:grid-cols-3">
        {candidates.map((row) => {
          const checked = selected.has(row.sku);
          return (
            <label key={row.sku} className="flex min-h-9 cursor-pointer items-center gap-2 rounded-lg border border-line bg-card px-2.5 py-1.5 text-[11px] text-ink-2">
              <input
                type="checkbox"
                checked={checked}
                disabled={busy || (!checked && selected.size >= 5)}
                onChange={() => toggle(row.sku)}
                className="accent-[var(--ds-accent)]"
              />
              <span className="min-w-0">
                <b className="font-mono text-ink">{row.sku}</b>
                {row.name && row.name !== row.sku ? <span className="ml-1.5 text-muted">{row.name}</span> : null}
                {row.evidence ? <span className="ml-1.5 text-accent">{row.evidence}</span> : null}
              </span>
            </label>
          );
        })}
      </div>
      <div className="mt-2">
        <label htmlFor={`${datalistId}-input`} className="mb-1 block text-[10.5px] font-semibold text-ink-2">
          候选里没有？搜索或精确输入 SKU
        </label>
        <input
          id={`${datalistId}-input`}
          aria-label="搜索或输入产品 SKU"
          type="text"
          list={datalistId}
          value={manualInput}
          disabled={busy}
          onChange={(event) => setManualInput(event.target.value)}
          placeholder="输入 SKU，多个用逗号分隔（最多 5 个）"
          className="min-h-9 w-full rounded-lg border border-line bg-card px-3 py-1.5 text-[11.5px] text-ink-2 outline-none focus:border-accent"
        />
        <datalist id={datalistId}>
          {directory.map((row) => <option key={row.sku} value={row.sku}>{row.name}</option>)}
        </datalist>
        {chosenSkus.length > 5 ? <div className="mt-1 text-[10.5px] text-warn">一条视频最多关联 5 个 SKU，请减少后再提交。</div> : null}
      </div>
      {candidates.length === 0 ? (
        <div className="mt-2 text-[11px] text-warn">自动候选为空；可在上方查找/精确输入 SKU，服务端会校验目录，不会写入伪关联。</div>
      ) : null}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <button
          type="button"
          disabled={busy || !selectionValid}
          onClick={() => onSubmit(chosenSkus, submitIntent)}
          className="min-h-8 rounded-lg border border-accent bg-accent px-3 py-1 text-[11px] font-semibold text-white disabled:cursor-default disabled:border-line disabled:bg-card disabled:text-muted"
        >
          {busy ? "关联提交中…" : `${submitLabelIntent === "confirm_detected" ? "确认系统识别并关注" : "确认关联并关注"}${chosenSkus.length ? `（${chosenSkus.length}）` : ""}`}
        </button>
        <button type="button" disabled={busy} onClick={onCancel} className="min-h-8 rounded-lg border border-line bg-card px-3 py-1 text-[11px] text-muted hover:text-ink disabled:cursor-default">
          取消
        </button>
        <span className="text-[10px] text-muted">
          {pending.intent === "confirm_detected"
            ? "当前尚未登记为员工确认；勾选唯一系统识别项后才确认。改选或手填仍记为员工手工关联。"
            : "当前尚未登记所选 SKU；只有你勾选后才会记为员工手工关联，支持 1–5 个 SKU。"}
        </span>
      </div>
    </div>
  );
}
