import { useEffect, useState } from 'react';
import {
  drilldownByValueId,
  drilldownLatestByMetric,
  getOfficialViewsEvidence,
  type VkpiDrilldownResponse,
  type VkpiDrilldownRow,
  type VkpiOfficialViewsPlatform,
  type VkpiOfficialViewsEvidenceRow,
} from '../../../services/vkpi.lineage-api';
import type { VkpiDashboardData, VkpiEvidenceRow, VkpiMetricEvidenceKey } from '../vkpiTypes';
import { objectValue, safeNumber, textValue } from '../shared/vkpiDataUtils';

interface UseMetricEvidenceArgs {
  apiToken?: string;
  metric: VkpiMetricEvidenceKey | null;
  metricValueId?: number | null;
  fallbackEvidence: VkpiDashboardData['evidence'];
}

function fallbackRows(metric: VkpiMetricEvidenceKey | null, evidence: VkpiDashboardData['evidence']) {
  if (!metric) return [];
  return evidence[metric] || [];
}

function mapLineageRowToEvidence(row: VkpiDrilldownRow, metric: VkpiMetricEvidenceKey): VkpiEvidenceRow {
  const snapshot = row.snapshot || {};
  const orderSnapshot = row.order_snapshot || objectValue(snapshot.shopify_order);
  const rawAmount = safeNumber(row.contribution_amount);
  const sourceMetric = textValue(snapshot.source_metric, '');
  const isDerivedCurrencyInput = row.source_type === 'metric_value' && ['gmv', 'cost'].includes(sourceMetric);
  const amountUnit: VkpiEvidenceRow['amountUnit'] =
    metric === 'gmv' || metric === 'cost' || metric === 'net_contribution' || isDerivedCurrencyInput
      ? 'currency'
      : metric === 'roi'
        ? 'ratio'
        : 'number';
  const amount = amountUnit === 'currency' ? rawAmount / 100 : rawAmount;
  const orderLabel = textValue(orderSnapshot.order_name || orderSnapshot.order_number || orderSnapshot.shopify_order_id, '');
  const contentTitle = textValue(snapshot.title, '');
  const contentPlatform = textValue(snapshot.platform, '');
  const contentRef = textValue(row.evidence_ref, '');
  const fallbackLabel = sourceMetric ? `${sourceMetric.toUpperCase()} 输入` : row.source_type;
  const label = textValue(
    orderLabel || contentTitle || contentRef || snapshot.note || snapshot.cost_type || fallbackLabel,
    '证据行',
  );
  return {
    id: String(row.id || row.source_id),
    metric,
    label,
    source: textValue(orderLabel ? 'Shopify Order' : contentPlatform || row.evidence_type || row.source_type, 'lineage'),
    amount,
    amountUnit,
    projectId: row.project?.id ? String(row.project.id) : undefined,
    kolName: row.kol?.name,
    ownerName: row.staff?.name || row.staff?.email,
    confidence: textValue(snapshot.confidence || orderSnapshot.financial_status || orderSnapshot.fulfillment_status, '') || undefined,
    occurredAt: row.occurred_at || textValue(orderSnapshot.processed_at, '') || undefined,
    rawRef: textValue(orderSnapshot.shopify_order_id || contentRef, '') || undefined,
  };
}

function mapOfficialRowToEvidence(row: VkpiOfficialViewsEvidenceRow): VkpiEvidenceRow {
  return {
    id: row.id,
    metric: 'views',
    label: row.label,
    source: row.source || row.platformLabel || 'Viltrox 自营账号',
    amount: safeNumber(row.amount),
    amountUnit: 'number',
    ownerName: row.staffName || row.ownerName || row.accountHandle,
    accountName: row.accountName || row.ownerName || row.accountHandle,
    accountHandle: row.accountHandle,
    accountUrl: row.accountUrl,
    staffName: row.staffName,
    platform: row.platform,
    platformLabel: row.platformLabel,
    attributionType: row.attributionType || 'owned_official',
    mediaUrl: row.mediaUrl,
    kolName: row.kolName,
    confidence: row.confidence,
    occurredAt: row.occurredAt,
    rawRef: row.rawRef || row.accountUrl,
  };
}

function uniqueEvidenceRows(rows: VkpiEvidenceRow[]) {
  const seen = new Set<string>();
  return rows.filter((row) => {
    const key = `${row.source}|${row.ownerName || ''}|${row.rawRef || ''}|${row.label}|${row.amount}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function useMetricEvidence({
  apiToken,
  metric,
  metricValueId,
  fallbackEvidence,
}: UseMetricEvidenceArgs) {
  const [rows, setRows] = useState<VkpiEvidenceRow[]>([]);
  const [lineageInfo, setLineageInfo] = useState<VkpiDrilldownResponse['run'] | null>(null);
  const [loading, setLoading] = useState(false);
  const [usedFallback, setUsedFallback] = useState(true);
  const [officialViewsMatrix, setOfficialViewsMatrix] = useState<VkpiOfficialViewsPlatform[]>([]);

  useEffect(() => {
    if (!metric) {
      setRows([]);
      setLineageInfo(null);
      setUsedFallback(false);
      setLoading(false);
      setOfficialViewsMatrix([]);
      return;
    }
    if (!apiToken) {
      setRows(fallbackRows(metric, fallbackEvidence));
      setLineageInfo(null);
      setUsedFallback(true);
      setLoading(false);
      setOfficialViewsMatrix([]);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setUsedFallback(false);
    if (metric !== 'views') setOfficialViewsMatrix([]);
    const request = metricValueId
      ? drilldownByValueId(apiToken, metricValueId, { limit: 200 })
      : drilldownLatestByMetric(apiToken, metric, { scopeType: 'all', limit: 200 });
    const officialViewsRequest =
      metric === 'views' ? getOfficialViewsEvidence(apiToken, { limit: 200 }).catch(() => null) : Promise.resolve(null);

    Promise.all([
      request.then((response) => ({ ok: true as const, response })).catch(() => ({ ok: false as const, response: null })),
      officialViewsRequest,
    ])
      .then(([lineageResult, officialViews]) => {
        if (cancelled) return;
        const officialRows = officialViews?.rows?.map(mapOfficialRowToEvidence) || [];
        setOfficialViewsMatrix(metric === 'views' ? officialViews?.platforms || [] : []);
        if (!lineageResult.ok || !lineageResult.response) {
          if (officialRows.length) {
            setRows(uniqueEvidenceRows(officialRows));
            setLineageInfo(null);
            setUsedFallback(false);
            return;
          }
          setRows(fallbackRows(metric, fallbackEvidence));
          setLineageInfo(null);
          setUsedFallback(true);
          return;
        }
        const response = lineageResult.response;
        const lineageRows =
          response.empty_reason || !response.value || !response.rows.length
            ? []
            : response.rows.map((row) => mapLineageRowToEvidence(row, metric));
        const combinedRows = metric === 'views' ? uniqueEvidenceRows([...officialRows, ...lineageRows]) : lineageRows;
        if (!combinedRows.length) {
          setRows(fallbackRows(metric, fallbackEvidence));
          setLineageInfo(response.run);
          setUsedFallback(true);
          return;
        }
        setRows(combinedRows);
        setLineageInfo(response.run);
        setUsedFallback(false);
      })
      .catch(() => {
        if (cancelled) return;
        setRows(fallbackRows(metric, fallbackEvidence));
        setLineageInfo(null);
        setUsedFallback(true);
        setOfficialViewsMatrix([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [apiToken, metric, metricValueId, fallbackEvidence]);

  return {
    rows,
    lineageInfo,
    loading,
    usedFallback,
    officialViewsMatrix,
  };
}
