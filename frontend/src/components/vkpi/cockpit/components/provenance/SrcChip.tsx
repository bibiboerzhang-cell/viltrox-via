// 溯源三组件 · SrcChip —— 模块卡头的「⛁ 数据来源」chip(全站复用,板块页范式要素④「溯源可点进」)。
// 视觉 1:1 移植 demo .src/.srctip:mono 小 chip,hover 浮出来源卡(「数据来源 · 可溯」+ 键值行),
// 点击 = 打开该模块的溯源弹窗(弹窗由调用方接管,组件本身不含弹窗)。
// 数据契约:label = chip 文案(如 "market_insights · 5m",真实表名/刷新口径,禁编造);
// rows = [键, 值][] 来源卡键值行(值为真实底表/口径);onOpen 可选,缺省时仅保留 hover 来源卡。
// 样式:./provenance.css(全 var(--ds-*) token,无写死色)。

import React from "react";
import { useT } from "../../lib/i18n";
import "./provenance.css";

const e = React.createElement;

export interface SrcChipProps {
  label: string;
  rows: Array<[string, string]>;
  onOpen?: () => void;
}

export function SrcChip({ label, rows, onOpen }: SrcChipProps): React.ReactElement {
  const { t } = useT();
  // demo 用 span.src;这里补 role/tabIndex/键盘触发(Enter/Space),保持可点语义且不引入 button 的嵌套限制。
  return e(
    "span",
    {
      className: "vkpi-prov-src",
      role: onOpen ? "button" : undefined,
      tabIndex: onOpen ? 0 : undefined,
      onClick: onOpen,
      onKeyDown: onOpen
        ? (ev: React.KeyboardEvent<HTMLSpanElement>) => {
            if (ev.key === "Enter" || ev.key === " ") {
              ev.preventDefault();
              onOpen();
            }
          }
        : undefined,
      title: onOpen ? t("点击查看本模块数据溯源") : undefined,
    },
    // 文案单独成层才能省略号收缩(外层要给 hover 来源卡留 position:relative,不能 overflow:hidden)
    e("span", { className: "vkpi-prov-src__label", title: onOpen ? undefined : label }, label),
    e(
      "span",
      { className: "vkpi-prov-srctip", role: "tooltip" },
      e("div", { className: "vkpi-prov-st-cap" }, t("数据来源 · 可溯")),
      rows.map(([k, v], i) =>
        e("div", { className: "vkpi-prov-st-r", key: `${k}-${i}` }, e("span", null, k), e("b", null, v)),
      ),
    ),
  );
}
