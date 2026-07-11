// 溯源三组件 · RecordPreview —— 库记录预览卡(全站复用):溯源链库节点点开后展示底层库记录。
// 视觉 1:1 移植 demo #recview .rec/.rc/.rr:小标题(accent 大写眉题)+ 键值行(值 mono 右对齐)。
// 数据契约:title 缺省「库记录预览」;rows = [字段名, 值][],值为真实库字段
// (表名 / id / captured_at / 关联字段等),原样渲染不做措辞改写。
// 样式:./provenance.css(全 var(--ds-*) token,入场 rise 动画,reduced-motion 下关闭)。

import React from "react";
import { useT } from "../../lib/i18n";
import "./provenance.css";

const e = React.createElement;

export interface RecordPreviewProps {
  title?: string;
  rows: Array<[string, string]>;
}

export function RecordPreview({ title, rows }: RecordPreviewProps): React.ReactElement {
  const { t } = useT();
  return e(
    "div",
    { className: "vkpi-prov-rec" },
    e("div", { className: "vkpi-prov-rec-cap" }, title ?? t("库记录预览")),
    rows.map(([k, v], i) =>
      e("div", { className: "vkpi-prov-rr", key: `${k}-${i}` }, e("span", null, k), e("b", null, v)),
    ),
  );
}
