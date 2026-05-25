export type DataQualityAction = 'resolve' | 'ignore' | 'assign' | 'rerun' | 'evidence' | 'reopen';

export interface DataQualityIssue {
  id: string;
  issue_type: string;
  severity: 'critical' | 'high' | 'medium' | 'low' | 'info' | string;
  title: string;
  entity_type: string;
  entity_id?: string | number | null;
  staff_id?: string | number | null;
  project_id?: string | number | null;
  kol_id?: string | number | null;
  detail?: string;
  evidence?: Record<string, unknown>;
  created_at?: string;
}

export interface DataQualityResponse {
  status: string;
  generated_at: string;
  count: number;
  total_count: number;
  issues: DataQualityIssue[];
  summary?: Record<string, number>;
}
