// 从 ProjectDetailModals.tsx 抽出的纯函数 / 常量 / 类型(行为不变搬运)。

export function filePayload(file: File | null) {
  return file ? { file_name: file.name, file_type: file.type, file_size: file.size } : {};
}

export type CostEntryType = 'cash_fee' | 'shipping' | 'product';

export function dateInputValue(value?: string) {
  const raw = String(value || '').trim();
  if (!raw) return '';
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return raw.slice(0, 10);
  return parsed.toISOString().slice(0, 10);
}

// 发票 LLM 提取字段 → 合同模板槽位(党 b / 收款组)。目标槽不在当前模板里就跳过。
export const INVOICE_SLOT_MAP: Array<[source: string, targets: string[]]> = [
  ['party_name', ['party_b_name']],
  ['address', ['party_b_address', 'account_address']],
  ['account_name', ['account_name']],
  ['account_number_or_iban', ['iban', 'account_number_or_iban', 'account_number']],
  ['swift', ['swift']],
  ['bank_name', ['bank_name']],
  ['bank_address', ['bank_address']],
];

// 金额槽位:paid 模板 campaign_fee / event 模板 payment_usd;仅在为空时回填。
export const INVOICE_FEE_SLOTS = ['campaign_fee', 'payment_usd'];

export interface PolishPreviewItem {
  key: string;
  label: string;
  original: string;
  polished: string;
}

// 合同模板槽位类型(GenerateContractModal 的 templates[].slots 元素;转发给抽出的字段区子组件用)。
export interface ContractSlot {
  key: string;
  label: string;
  group?: string;
  type?: string;
  options?: string[];
  required?: boolean;
}
