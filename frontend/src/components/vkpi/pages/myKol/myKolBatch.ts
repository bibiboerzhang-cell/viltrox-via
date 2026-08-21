// 【M2】MY KOL 批量操作纯 helper:CSV 拼装/下载 + 邮箱脱敏 + contacts 挑邮箱。
// 零网络零状态(downloadCsv 只做浏览器端 Blob 下载),端点调用留在页面组件里。

export interface BatchCsvRow {
  name: string;
  platform: string;
  handle: string;
  followers: string;
  fit: number | null;
  email: string;
}

// 邮箱脱敏:照后端读端 pool_common._mask_email 同口径(e***@d***);空/无 @ 原样返回。
// 合规:导出永远不落真邮箱,真值只走 view_kol_contact 二次确认 + 审计端点。
export function maskEmailForExport(value: string): string {
  const text = String(value || '').trim();
  if (!text || !text.includes('@')) return text;
  const at = text.indexOf('@');
  const local = text.slice(0, at);
  const domain = text.slice(at + 1);
  return `${local ? `${local[0]}***` : '***'}@${domain ? `${domain[0]}***` : '***'}`;
}

// 从 aggregate 的 contacts(list of {contact_type, contact_value})里挑第一条邮箱类联系方式。
export function pickEmailFromContacts(contacts: unknown): string {
  if (!Array.isArray(contacts)) return '';
  for (const raw of contacts) {
    if (!raw || typeof raw !== 'object') continue;
    const item = raw as Record<string, unknown>;
    const type = String(item.contact_type || '').toLowerCase();
    const value = String(item.contact_value || '').trim();
    if (!value) continue;
    if (type.includes('email') || value.includes('@')) return value;
  }
  return '';
}

// 外部文本进电子表格前先消除公式前缀。名字/handle 等来自公开平台，攻击者可把
// 首个非空白字符伪装成 =/+/-/@；RFC 4180 引号本身不会阻止 Excel 执行公式。
// 单引号是电子表格的文本标记，普通文本和真正的 number 值保持原样。
function spreadsheetSafeText(value: string): string {
  const text = String(value);
  return /^[=+\-@]/.test(text.trim()) ? `'${text}` : text;
}

// CSV 单元格转义:外部字符串先做公式防护；含逗号/引号/换行再加引号并翻倍
// (RFC 4180)。number 字段不经过文本防护。
function csvCell(value: string | number | null | undefined): string {
  const text = value == null ? '' : typeof value === 'string' ? spreadsheetSafeText(value) : String(value);
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

// 拼选中行 CSV:列序 名字/平台/handle/粉丝/fit/邮箱(脱敏),带 BOM 让 Excel 识别 UTF-8。
export function buildKolCsv(rows: BatchCsvRow[]): string {
  const lines = [['名字', '平台', 'handle', '粉丝', 'fit', '邮箱(脱敏)'].join(',')];
  rows.forEach((row) => {
    lines.push([
      csvCell(row.name),
      csvCell(row.platform),
      csvCell(row.handle),
      csvCell(row.followers),
      csvCell(row.fit != null ? row.fit.toFixed(1) : ''),
      csvCell(maskEmailForExport(row.email)),
    ].join(','));
  });
  return `\ufeff${lines.join('\r\n')}`;
}

// 浏览器端下载:Blob + 临时 <a>,用完回收 objectURL。
export function downloadCsv(filename: string, content: string) {
  if (typeof window === 'undefined') return;
  const blob = new Blob([content], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
