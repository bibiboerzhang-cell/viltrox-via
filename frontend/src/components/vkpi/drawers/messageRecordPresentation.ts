/** Raw message rows describe manual records, never transport verification. */
export function messageRecordDirectionLabel(value: unknown): string {
  const direction = typeof value === 'string' ? value.trim().toLowerCase() : '';
  if (['outbound', 'out', 'sent'].includes(direction)) return '我方沟通（手工记录）';
  if (['inbound', 'in', 'reply', 'received'].includes(direction)) return '对方回复（手工记录）';
  if (direction === 'internal_note') return '内部备注';
  if (direction === 'draft') return '草稿记录';
  return '沟通记录（方向待确认）';
}
