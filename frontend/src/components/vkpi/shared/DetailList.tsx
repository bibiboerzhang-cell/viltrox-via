import React from 'react';

export function DetailList({
  title,
  rows,
  empty,
  children,
}: {
  title: string;
  rows: Array<Record<string, unknown>>;
  empty: string;
  children: (row: Record<string, unknown>) => React.ReactNode;
}) {
  if (!rows.length) {
    return <article><div><strong>{title}</strong><span>0 条</span></div><p>{empty}</p></article>;
  }
  return <>{rows.map((row) => children(row))}</>;
}
