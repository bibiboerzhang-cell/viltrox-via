import React, { useEffect, useMemo, useState } from "react";
import type { VkpiDashboardData } from "../../vkpiTypes";
import type { ProjectsPageProps } from "../../pages/ProjectsPage.types";
import { listProductCatalog } from "../../../../domains/products";
import { Drow, ModalShell } from "./MarketVoicePage.dialogs";
import { kolHumanDisplayName, kolHumanPublicHandle } from "../lib/kolIdentity";

// Projects 板块页 · 弹窗族。
//   ProjectCreateModal  旧 ProjectsPage 新建推广表单原样搬家(字段/launch 分支/SKU 目录
//                       带价/payload 口径零改动;样式类沿用 project-board-modals.css,
//                       旧页保留为回滚垫)。
//   MiniListModal       行模块全量列表壳(金样板 FeedListModal 同构,ModalShell 复用)。
//   MiniDetailModal     单条详情 + ‹#n/N› + ↑↓ 方向键连续翻(观察窗口/归因行共用)。
// 红线:本文件唯一网络 = listProductCatalog(SKU 目录只读);不触 fit/rule_v0;
//   颜色全 token(复用既有 CSS 类/ModalShell);零 opacity 修饰类。

const campaignStatusOptions = ["规划中", "进行中", "收尾中", "已结束", "已取消"];

// 逗号/换行分隔的多值字段 → 去重保序的非空字符串列表(对齐后端 _as_list 清洗口径)。
function splitLaunchList(raw: string): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  raw
    .split(/[\n,]/)
    .map((item) => item.trim())
    .forEach((item) => {
      if (item && !seen.has(item)) {
        seen.add(item);
        out.push(item);
      }
    });
  return out;
}

export function ProjectCreateModal({
  data,
  apiToken,
  defaultOwnerName,
  onClose,
  onCreateProject,
  onMessage,
}: {
  data: VkpiDashboardData;
  apiToken?: string;
  defaultOwnerName: string;
  onClose: () => void;
  onCreateProject?: ProjectsPageProps["onCreateProject"];
  onMessage: (message: string) => void;
}) {
  const [projectName, setProjectName] = useState("");
  const [kolId, setKolId] = useState("");
  const [productName, setProductName] = useState("");
  const [price, setPrice] = useState("");
  const [campaignStatus, setCampaignStatus] = useState("规划中");
  const [targetKolCount, setTargetKolCount] = useState("10");
  const [budgetUsd, setBudgetUsd] = useState("0");
  const [spentUsd, setSpentUsd] = useState("0");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [ownerName, setOwnerName] = useState(defaultOwnerName);
  const [campaignType, setCampaignType] = useState("上市推广");
  // N3 新品发布(Launch):勾选后走 source_type='launch' 分支,结构化卖点/竞品/目标市场
  // 塞进 metadata.launch(后端 launch_project.normalize_launch_metadata 同款字段)。
  const [isLaunch, setIsLaunch] = useState(false);
  const [launchPriceBand, setLaunchPriceBand] = useState("");
  const [launchTargetCountries, setLaunchTargetCountries] = useState("");
  const [launchSellingPoints, setLaunchSellingPoints] = useState("");
  const [launchCompetitors, setLaunchCompetitors] = useState("");
  const [launchTargetAudience, setLaunchTargetAudience] = useState("");
  const [launchHypotheses, setLaunchHypotheses] = useState("");
  const [busy, setBusy] = useState(false);

  // P-SKU-2:接真 369 SKU 库;选 SKU → 自动带出价格。非 manager 拉不到时静默回落文本输入。
  const [skuCatalog, setSkuCatalog] = useState<Array<{ productSku: string; productName: string; price: number | null }>>([]);
  useEffect(() => {
    if (!apiToken) return;
    let cancelled = false;
    void listProductCatalog(apiToken, { limit: 500 })
      .then((resp: any) => {
        if (cancelled) return;
        const rows = Array.isArray(resp?.products) ? resp.products : [];
        setSkuCatalog(
          rows
            .map((r: any) => ({
              productSku: String(r.sku || r.product_sku || ""),
              productName: String(r.marketing_name || r.model_name || r.product_name || r.sku || ""),
              price: r.price_usd === null || r.price_usd === undefined || r.price_usd === "" ? null : Number(r.price_usd),
            }))
            .filter((x: any) => x.productSku),
        );
      })
      .catch(() => {
        if (!cancelled) setSkuCatalog([]);
      });
    return () => {
      cancelled = true;
    };
  }, [apiToken]);

  const productChoices = useMemo(() => {
    const bySku = new Map<string, { id: string; productSku: string; productName: string; sourceLabel: string; price?: number | null }>();
    // 真 SKU 库优先(带价格),再补旧来源(成本目录/产品发布)
    skuCatalog.forEach((item) => {
      bySku.set(item.productSku, { id: item.productSku, productSku: item.productSku, productName: item.productName, sourceLabel: "产品库", price: item.price });
    });
    data.productCosts
      .filter((item) => item.active !== false)
      .forEach((item) => {
        if (!item.productSku || bySku.has(item.productSku)) return;
        bySku.set(item.productSku, {
          id: item.id || item.productSku,
          productSku: item.productSku,
          productName: item.productName || item.productSku,
          sourceLabel: "成本目录",
        });
      });
    data.productLaunches.forEach((launch) => {
      const sku = launch.productSku || launch.id;
      if (!sku || bySku.has(sku)) return;
      bySku.set(sku, {
        id: launch.id || sku,
        productSku: sku,
        productName: launch.productName || launch.launchName || sku,
        sourceLabel: launch.status ? `产品发布 · ${launch.status}` : "产品发布",
      });
    });
    return Array.from(bySku.values()).sort((a, b) => a.productName.localeCompare(b.productName));
  }, [data.productCosts, data.productLaunches, skuCatalog]);

  const matchedProduct = useMemo(() => {
    const normalized = productName.trim().toLowerCase();
    if (!normalized) return undefined;
    return productChoices.find(
      (item) => item.productName.toLowerCase() === normalized || item.productSku.toLowerCase() === normalized,
    );
  }, [productChoices, productName]);

  // 命中 SKU 且库里有价 → 自动带出价格;未命中(自由文本)不动用户已输入。
  useEffect(() => {
    const p = (matchedProduct as any)?.price;
    if (p != null && Number.isFinite(Number(p))) setPrice(`$${Number(p)}`);
  }, [matchedProduct]);

  // Launch 分支:SKU 是后端 required 字段;无匹配 SKU 时回落主推产品自由文本。
  const launchSku = (matchedProduct?.productSku || productName.trim()).trim();
  const launchSkuMissing = isLaunch && !launchSku;

  const closeModal = () => {
    if (busy) return;
    onClose();
  };

  const submitProject = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!onCreateProject || !projectName.trim()) return;
    if (launchSkuMissing) {
      onMessage("新品发布(Launch)需要先指定 SKU / 主推产品。");
      return;
    }
    const noteLines = [
      isLaunch ? "类型：新品发布(Launch)" : "",
      campaignStatus ? `状态：${campaignStatus}` : "",
      campaignType.trim() ? `推广类型：${campaignType.trim()}` : "",
      price.trim() ? `价格：${price.trim()}` : "",
      targetKolCount.trim() ? `目标 KOL 数：${targetKolCount.trim()}` : "",
      budgetUsd.trim() ? `预算 USD：${budgetUsd.trim()}` : "",
      spentUsd.trim() ? `已花 USD：${spentUsd.trim()}` : "",
      startDate ? `开始时间：${startDate}` : "",
      endDate ? `计划结束：${endDate}` : "",
      ownerName.trim() ? `负责人：${ownerName.trim()}` : "",
    ].filter(Boolean);

    // N3 launch 元数据:字段名与后端 launch_project.LAUNCH_PROJECT_FIELDS 一一对齐。
    const launchMetadata = isLaunch
      ? {
          launch: {
            sku: launchSku,
            price_band: launchPriceBand.trim(),
            target_countries: splitLaunchList(launchTargetCountries),
            selling_points: splitLaunchList(launchSellingPoints),
            competitors: splitLaunchList(launchCompetitors),
            target_audience: launchTargetAudience.trim(),
            validation_hypotheses: splitLaunchList(launchHypotheses),
          },
          project_type: "launch",
        }
      : undefined;

    setBusy(true);
    try {
      await onCreateProject({
        projectName: projectName.trim(),
        kolId: kolId.trim() || undefined,
        productSku: isLaunch ? launchSku : matchedProduct?.productSku,
        productName: productName.trim() || matchedProduct?.productName,
        products: matchedProduct ? [{ productSku: matchedProduct.productSku, productName: matchedProduct.productName }] : undefined,
        sourceType: isLaunch ? "launch" : "cockpit_projects_ui",
        note: noteLines.length ? noteLines.join("\n") : undefined,
        metadata: launchMetadata,
      });
      onClose();
      onMessage(
        isLaunch
          ? "新品发布(Launch)项目已创建。KOL 候选、内容验证任务和观察窗口请在项目详情里推进。"
          : "推广项目已创建。后续阶段推进、费用、物流和证据请在项目详情里处理。",
      );
    } catch (error) {
      onMessage(error instanceof Error ? error.message : "推广项目创建失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="vkpi-project-modal-backdrop" role="presentation">
      <form className="vkpi-project-create-modal" onSubmit={submitProject} role="dialog" aria-label="新建推广">
        <header>
          <div>
            <h2>新建推广</h2>
            <p>提交后写入现有项目接口；阶段、费用、物流和证据在项目详情继续处理。</p>
          </div>
          <button type="button" onClick={closeModal}>关闭</button>
        </header>
        <div className="vkpi-project-create-grid">
          <label className="is-full vkpi-project-launch-toggle">
            <input type="checkbox" checked={isLaunch} onChange={(event) => setIsLaunch(event.target.checked)} />
            新品发布(Launch)—— 建成 source_type=launch 项目，带卖点/竞品/目标市场，进项目仪表盘
          </label>
          <label className="is-full">推广名称
            <input value={projectName} onChange={(event) => setProjectName(event.target.value)} placeholder="例：AF 35mm F1.2 LAB FE 上市推广" />
          </label>
          <label>主推产品
            <input list="vkpi-projects-board-product-options" value={productName} onChange={(event) => setProductName(event.target.value)} placeholder="35mm F1.2 LAB FE" />
            <datalist id="vkpi-projects-board-product-options">
              {productChoices.map((product) => (
                <option key={product.id || product.productSku} value={product.productName}>
                  {product.productSku} · {product.sourceLabel}
                  {(product as any).price != null ? ` · $${(product as any).price}` : ""}
                </option>
              ))}
            </datalist>
          </label>
          <label>价格
            <input value={price} onChange={(event) => setPrice(event.target.value)} placeholder="$999" />
          </label>
          <label>状态
            <select value={campaignStatus} onChange={(event) => setCampaignStatus(event.target.value)}>
              {campaignStatusOptions.map((option) => <option key={option} value={option}>{option}</option>)}
            </select>
          </label>
          <label>目标 KOL 数
            <input value={targetKolCount} onChange={(event) => setTargetKolCount(event.target.value)} inputMode="numeric" placeholder="10" />
          </label>
          <label>预算 USD
            <input value={budgetUsd} onChange={(event) => setBudgetUsd(event.target.value)} inputMode="decimal" placeholder="0" />
          </label>
          <label>已花 USD
            <input value={spentUsd} onChange={(event) => setSpentUsd(event.target.value)} inputMode="decimal" placeholder="0" />
          </label>
          <label>开始时间
            <input value={startDate} onChange={(event) => setStartDate(event.target.value)} type="date" />
          </label>
          <label>计划结束
            <input value={endDate} onChange={(event) => setEndDate(event.target.value)} type="date" />
          </label>
          <label>负责人
            <input value={ownerName} onChange={(event) => setOwnerName(event.target.value)} placeholder="Jianbo" />
          </label>
          <label>推广类型
            <input value={campaignType} onChange={(event) => setCampaignType(event.target.value)} placeholder="上市推广" />
          </label>
          {isLaunch ? (
            <>
              <label className="is-full">SKU（必填）
                <input value={launchSku} readOnly placeholder="先在「主推产品」选 SKU" />
                {launchSkuMissing ? <small className="vkpi-project-launch-hint">请在主推产品里选/填 SKU。</small> : null}
              </label>
              <label>价格带
                <input value={launchPriceBand} onChange={(event) => setLaunchPriceBand(event.target.value)} placeholder="99-149 USD" />
              </label>
              <label>目标人群
                <input value={launchTargetAudience} onChange={(event) => setLaunchTargetAudience(event.target.value)} placeholder="入门级视频创作者" />
              </label>
              <label className="is-full">目标国家（逗号/换行分隔）
                <input value={launchTargetCountries} onChange={(event) => setLaunchTargetCountries(event.target.value)} placeholder="US, JP, DE" />
              </label>
              <label className="is-full">核心卖点（逗号/换行分隔）
                <textarea value={launchSellingPoints} onChange={(event) => setLaunchSellingPoints(event.target.value)} rows={2} placeholder="F1.2 大光圈, LAB 旗舰画质, 紧凑轻量" />
              </label>
              <label className="is-full">竞品（逗号/换行分隔）
                <input value={launchCompetitors} onChange={(event) => setLaunchCompetitors(event.target.value)} placeholder="Sony 35mm F1.4 GM, Sigma 35mm F1.2" />
              </label>
              <label className="is-full">验证假设（逗号/换行分隔）
                <textarea value={launchHypotheses} onChange={(event) => setLaunchHypotheses(event.target.value)} rows={2} placeholder="大光圈人像是核心传播点, 价格带可下探至入门用户" />
              </label>
            </>
          ) : null}
          {data.kolOptions.length ? (
            <label className="is-full">合作 KOL（可选）
              <select value={kolId} onChange={(event) => setKolId(event.target.value)}>
                <option value="">稍后从项目详情或 KOL 库添加</option>
                {data.kolOptions.map((kol) => (
                  <option key={kol.id} value={kol.id}>{[
                    kolHumanDisplayName(kol as unknown as Record<string, unknown>),
                    kolHumanPublicHandle(kol as unknown as Record<string, unknown>),
                    kol.platform,
                  ].filter(Boolean).join(" · ")}</option>
                ))}
              </select>
            </label>
          ) : null}
        </div>
        <footer>
          <button className="vkpi-project-modal-button" type="button" onClick={closeModal} disabled={busy}>取消</button>
          <button className="vkpi-project-modal-button is-primary" type="submit" disabled={busy || !onCreateProject || !projectName.trim() || launchSkuMissing}>
            {isLaunch ? "创建新品发布" : "创建推广"}
          </button>
        </footer>
      </form>
    </div>
  );
}

/* ============ 行模块全量列表壳(金样板 FeedListModal 同构) ============ */

export function MiniListModal({
  title,
  total,
  unit = "条",
  children,
  onClose,
}: {
  title: string;
  total: number;
  unit?: string;
  children: React.ReactNode;
  onClose: () => void;
}) {
  return (
    <ModalShell title={`${title} · 全量`} sub={`共 ${total} ${unit} · 点单条看详情(详情内 ↑↓ 连续翻)`} onClose={onClose}>
      {children}
    </ModalShell>
  );
}

/* ============ 单条详情 + ‹#n/N› + ↑↓ 连续翻(观察窗口/归因行共用) ============ */

function NavBar({ index, total, onNav }: { index: number; total: number; onNav: (i: number) => void }) {
  const btn =
    "flex-none rounded-[7px] border border-line px-2 py-0.5 text-[11px] text-muted transition-colors hover:border-accent hover:text-accent disabled:cursor-default disabled:text-muted disabled:hover:border-line";
  return (
    <span className="flex flex-none items-center gap-1.5">
      <button type="button" className={btn} disabled={index <= 0} onClick={() => onNav(index - 1)} aria-label="上一条">‹</button>
      <span className="font-mono text-[10px] text-muted">#{index + 1}/{total}</span>
      <button type="button" className={btn} disabled={index >= total - 1} onClick={() => onNav(index + 1)} aria-label="下一条">›</button>
    </span>
  );
}

export function MiniDetailModal({
  title,
  pill,
  rows,
  index,
  total,
  onNav,
  onClose,
  footer,
}: {
  title: React.ReactNode;
  pill?: React.ReactNode;
  rows: Array<[string, React.ReactNode]>;
  index: number;
  total: number;
  onNav: (i: number) => void;
  onClose: () => void;
  footer?: React.ReactNode;
}) {
  // ↑↓(以及 ←→)方向键连续翻(金样板同款);Escape 交给 ModalShell。
  React.useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === "ArrowDown" || ev.key === "ArrowRight") {
        ev.preventDefault();
        if (index < total - 1) onNav(index + 1);
      } else if (ev.key === "ArrowUp" || ev.key === "ArrowLeft") {
        ev.preventDefault();
        if (index > 0) onNav(index - 1);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [index, total, onNav]);

  return (
    <ModalShell
      title={
        <span className="flex items-center gap-2">
          <span className="min-w-0 truncate">{title}</span>
          {pill}
        </span>
      }
      sub={<NavBar index={index} total={total} onNav={onNav} />}
      onClose={onClose}
    >
      <div>
        {rows.map(([k, v]) => (
          <Drow key={k} k={k} v={v} />
        ))}
      </div>
      {footer ? <div className="mt-4">{footer}</div> : null}
    </ModalShell>
  );
}
