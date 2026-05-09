import { useEffect, useState } from 'react';
import {
  drilldownByValueId,
  drilldownLatestByMetric,
  type VkpiDrilldownResponse,
  type VkpiDrilldownRow,
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
  const fallbackLabel = sourceMetric ? `${sourceMetric.toUpperCase()} 输入` : row.source_type;
  const label = textValue(
    orderLabel || row.evidence_ref || snapshot.note || snapshot.cost_type || fallbackLabel,
    '证据行',
  );
  return {
    id: String(row.id || row.source_id),
    metric,
    label,
    source: textValue(orderLabel ? 'Shopify Order' : row.evidence_type || row.source_type, 'lineage'),
    amount,
    amountUnit,
    projectId: row.project?.id ? String(row.project.id) : undefined,
    kolName: row.kol?.name,
    ownerName: row.staff?.name || row.staff?.email,
    confidence: textValue(snapshot.confidence || orderSnapshot.financial_status || orderSnapshot.fulfillment_status, '') || undefined,
    occurredAt: row.occurred_at || textValue(orderSnapshot.processed_at, '') || undefined,
    rawRef: textValue(orderSnapshot.shopify_order_id || row.evidence_ref, '') || undefined,
  };
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

  useEffect(() => {
    if (!metric) {
      setRows([]);
      setLineageInfo(null);
      setUsedFallback(false);
      setLoading(false);
      return;
    }
    if (!apiToken) {
      setRows(fallbackRows(metric, fallbackEvidence));
      setLineageInfo(null);
      setUsedFallback(true);
      setLoading(false);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setUsedFallback(false);
    const request = metricValueId
      ? drilldownByValueId(apiToken, metricValueId, { limit: 200 })
      : drilldownLatestByMetric(apiToken, metric, { scopeType: 'all', limit: 200 });

    request
      .then((response) => {
        if (cancelled) return;
        if (response.empty_reason || !response.value || !response.rows.length) {
          setRows(fallbackRows(metric, fallbackEvidence));
          setLineageInfo(response.run);
          setUsedFallback(true);
          return;
        }
        setRows(response.rows.map((row) => mapLineageRowToEvidence(row, metric)));
        setLineageInfo(response.run);
        setUsedFallback(false);
      })
      .catch(() => {
        if (cancelled) return;
        setRows(fallbackRows(metric, fallbackEvidence));
        setLineageInfo(null);
        setUsedFallback(true);
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
  };
}
