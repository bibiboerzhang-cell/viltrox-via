import React from "react";
import {
  listProductCosts,
  verifyProductCost,
  type VkpiProductCostRow,
} from "../../../../services/vkpi/cost-api";

function cents(value: unknown, currency = "USD"): string {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return "—";
  return new Intl.NumberFormat("en-US", { style: "currency", currency }).format(amount / 100);
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : "成本核验失败";
}

export function SettingsProductCostVerificationPanel({ apiToken }: { apiToken?: string }) {
  const tokenKey = String(apiToken || "");
  const tokenRef = React.useRef(tokenKey);
  tokenRef.current = tokenKey;
  const loadGenerationRef = React.useRef(0);
  const loadAbortRef = React.useRef<AbortController | null>(null);
  const submitGenerationRef = React.useRef(0);
  const [rowSnapshot, setRowSnapshot] = React.useState<{ token: string; rows: VkpiProductCostRow[] }>({
    token: "",
    rows: [],
  });
  const rows = rowSnapshot.token === tokenKey ? rowSnapshot.rows : [];
  const [selectedSku, setSelectedSku] = React.useState("");
  const [sourceType, setSourceType] = React.useState("supplier_invoice");
  const [sourceRef, setSourceRef] = React.useState("");
  const [sourceObservedAt, setSourceObservedAt] = React.useState("");
  const [authorizationRef, setAuthorizationRef] = React.useState("");
  const [reason, setReason] = React.useState("");
  const [confirmed, setConfirmed] = React.useState(false);
  const [loading, setLoading] = React.useState(false);
  const [submitting, setSubmitting] = React.useState(false);
  const [error, setError] = React.useState("");
  const [message, setMessage] = React.useState("");

  const load = React.useCallback(async () => {
    const requestToken = String(apiToken || "");
    const generation = loadGenerationRef.current + 1;
    loadGenerationRef.current = generation;
    loadAbortRef.current?.abort();
    loadAbortRef.current = null;
    const isCurrent = () => (
      loadGenerationRef.current === generation
      && tokenRef.current === requestToken
    );
    if (!requestToken) {
      if (isCurrent()) {
        setRowSnapshot({ token: requestToken, rows: [] });
        setLoading(false);
        setError("缺少管理员会话，无法读取成本核验队列。");
      }
      return;
    }
    const controller = new AbortController();
    loadAbortRef.current = controller;
    setLoading(true);
    setError("");
    try {
      const payload = await listProductCosts(requestToken, { limit: 200, signal: controller.signal });
      if (!isCurrent()) return;
      const nextRows = Array.isArray(payload?.product_costs) ? payload.product_costs : [];
      setRowSnapshot({ token: requestToken, rows: nextRows });
      setSelectedSku((current) => {
        if (current && nextRows.some((row) => String(row.product_sku || "") === current)) return current;
        return String(nextRows.find((row) => row.verification_status !== "verified")?.product_sku || nextRows[0]?.product_sku || "");
      });
    } catch (cause) {
      if (isCurrent() && !controller.signal.aborted) setError(errorText(cause));
    } finally {
      if (isCurrent()) {
        if (loadAbortRef.current === controller) loadAbortRef.current = null;
        setLoading(false);
      }
    }
  }, [apiToken]);

  React.useEffect(() => {
    void load();
    return () => {
      loadGenerationRef.current += 1;
      loadAbortRef.current?.abort();
      loadAbortRef.current = null;
    };
  }, [load]);

  React.useEffect(() => {
    submitGenerationRef.current += 1;
    setSubmitting(false);
    setMessage("");
    return () => {
      submitGenerationRef.current += 1;
    };
  }, [tokenKey]);

  const selected = rows.find((row) => String(row.product_sku || "") === selectedSku) || null;
  const verifiedCount = rows.filter((row) => row.verification_status === "verified").length;
  const canVerify = Boolean(
    apiToken
      && selectedSku
      && selected?.verification_status !== "verified"
      && Number.isInteger(Number(selected?.id))
      && Number(selected?.id) > 0
      && Number.isFinite(Number(selected?.unit_cost_cents))
      && Number.isInteger(Number(selected?.row_version))
      && Number(selected?.row_version) > 0
      && String(selected?.currency || "").trim()
      && String(selected?.updated_at || "").trim()
      && sourceType.trim()
      && sourceRef.trim()
      && sourceObservedAt
      && authorizationRef.trim()
      && reason.trim()
      && confirmed,
  );

  const submit: React.FormEventHandler = async (event) => {
    event.preventDefault();
    if (!apiToken || !canVerify) return;
    const requestToken = String(apiToken);
    const generation = submitGenerationRef.current + 1;
    submitGenerationRef.current = generation;
    const isCurrent = () => (
      submitGenerationRef.current === generation
      && tokenRef.current === requestToken
    );
    const observed = new Date(sourceObservedAt);
    if (!Number.isFinite(observed.getTime())) {
      setError("来源观测时间无效。");
      return;
    }
    setSubmitting(true);
    setError("");
    setMessage("");
    try {
      await verifyProductCost(requestToken, selectedSku, {
        sourceType: sourceType.trim(),
        sourceRef: sourceRef.trim(),
        sourceObservedAt: observed.toISOString(),
        authorizationRef: authorizationRef.trim(),
        reason: reason.trim(),
        confirmedByHuman: true,
        expectedId: Number(selected?.id),
        expectedUnitCostCents: Number(selected?.unit_cost_cents),
        expectedCurrency: String(selected?.currency || "").trim().toUpperCase(),
        expectedRowVersion: Number(selected?.row_version),
        expectedUpdatedAt: String(selected?.updated_at || ""),
      });
      if (!isCurrent()) return;
      setMessage(`${selectedSku} 已形成带来源与人工授权的实际成本证据。`);
      setConfirmed(false);
      await load();
    } catch (cause) {
      if (isCurrent()) setError(errorText(cause));
    } finally {
      if (isCurrent()) setSubmitting(false);
    }
  };

  return (
    <section className="vkpi-card vkpi-action-card vkpi-action-card--wide" aria-labelledby="product-cost-verification-title">
      <div className="vkpi-table-card__header">
        <div>
          <h2 id="product-cost-verification-title">实际成本核验队列</h2>
          <span>{loading ? "读取中…" : `${verifiedCount}/${rows.length} 已核验`}</span>
        </div>
        <button className="vkpi-button" type="button" onClick={() => void load()} disabled={loading || submitting || !apiToken}>
          {loading ? "刷新中" : "刷新队列"}
        </button>
      </div>
      <p className="vkpi-settings-hint">
        参考成本不会进入真实 ROI。只有 Owner/Admin 在全局人工业务写入闸开启后，提交来源、观测时间与授权回执，才会晋升为已核验实际成本。
      </p>
      {error ? <div className="vkpi-inline-message is-error">{error}</div> : null}
      {message ? <div className="vkpi-inline-message">{message}</div> : null}
      {rows.length ? (
        <form className="vkpi-form-stack" onSubmit={submit}>
          <label>
            成本记录
            <select aria-label="待核验成本记录" value={selectedSku} onChange={(event) => setSelectedSku(event.target.value)}>
              {rows.map((row) => {
                const sku = String(row.product_sku || "");
                const state = row.verification_status === "verified" ? "已核验" : "待核验";
                return <option key={sku} value={sku}>{sku} · {cents(row.unit_cost_cents, String(row.currency || "USD"))} · {state}</option>;
              })}
            </select>
          </label>
          <div className="vkpi-settings-meta-grid">
            <small>SKU<b>{selectedSku || "—"}</b></small>
            <small>当前状态<b>{selected?.verification_status === "verified" ? "已核验" : "参考/待核验"}</b></small>
            <small>来源<b>{selected?.source_ref || "待补"}</b></small>
          </div>
          <label>
            来源类型
            <select aria-label="成本来源类型" value={sourceType} onChange={(event) => setSourceType(event.target.value)}>
              <option value="supplier_invoice">供应商发票</option>
              <option value="finance_erp">财务 / ERP</option>
              <option value="approved_quote">已批准报价</option>
              <option value="warehouse_cost_sheet">仓库成本表</option>
            </select>
          </label>
          <input aria-label="成本来源引用" value={sourceRef} onChange={(event) => setSourceRef(event.target.value)} placeholder="来源单号 / 文档 URL / 不含密钥的证据引用" />
          <label>
            来源观测时间
            <input aria-label="成本来源观测时间" type="datetime-local" value={sourceObservedAt} onChange={(event) => setSourceObservedAt(event.target.value)} />
          </label>
          <input aria-label="成本授权回执" value={authorizationRef} onChange={(event) => setAuthorizationRef(event.target.value)} placeholder="审批单 / 变更单 / 授权 ticket" />
          <textarea aria-label="成本核验原因" value={reason} onChange={(event) => setReason(event.target.value)} placeholder="为什么这条来源足以作为当前实际成本" rows={3} />
          <label className="vkpi-checkbox">
            <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
            我已人工核对来源、SKU、币种、金额与观测时间
          </label>
          <button className="vkpi-button vkpi-button--primary" type="submit" disabled={!canVerify || submitting}>
            {submitting ? "核验中…" : selected?.verification_status === "verified" ? "该记录已核验" : "提交带授权的核验"}
          </button>
        </form>
      ) : !loading && !error ? (
        <div className="vkpi-empty-state">尚无成本记录；先录入参考成本，再进行人工核验。</div>
      ) : null}
    </section>
  );
}
