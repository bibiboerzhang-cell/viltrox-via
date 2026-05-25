import { useCallback, useEffect, useState } from 'react';
import { getDataQualitySummary } from './api';
import type { DataQualityResponse } from './types';

interface UseDataQualitySummaryOptions {
  enabled: boolean;
  token?: string;
  limit?: number;
}

export function useDataQualitySummary({ enabled, token, limit = 200 }: UseDataQualitySummaryOptions) {
  const [quality, setQuality] = useState<DataQualityResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState('');

  const refresh = useCallback(async () => {
    if (!token || !enabled) return;
    setLoading(true);
    setErrorMessage('');
    try {
      setQuality(await getDataQualitySummary(token, limit));
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '数据质量检查失败');
    } finally {
      setLoading(false);
    }
  }, [enabled, limit, token]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return {
    errorMessage,
    loading,
    quality,
    refresh,
    setErrorMessage,
    setQuality,
  };
}
