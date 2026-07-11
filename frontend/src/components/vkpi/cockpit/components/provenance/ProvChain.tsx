// 溯源三组件 · ProvChain —— 溯源链(全站复用,板块页范式要素④):
// 原帖 ↗(外链)→ 身份跳(KOL 档案/官号矩阵)→ 采集 job → 库记录 #id → LLM 批注 → 聚合表。
// 视觉 1:1 移植 demo .provchain/.pchip/.parrow/.provnote:链式 chips,节点间 → 分隔;
// href 节点 = 外链 pchip(accent 色 + ↗,新开页);rec 节点 = 库节点(点击回调 onRecord,
// 由调用方展开 RecordPreview);两者皆无 = 纯文本节点。链下一行 provnote 小注。
// 数据契约:steps 顺序即链序;href = 真实原帖 URL;rec = 记录类别 key(如 feedback/apify/kol),
// rid = 该类别下的记录 id(可空)。样式:./provenance.css(全 var(--ds-*) token)。

import React from "react";
import { useT } from "../../lib/i18n";
import "./provenance.css";

const e = React.createElement;

export interface ProvStep {
  label: string;
  href?: string;
  rec?: string;
  rid?: string;
}

export interface ProvChainProps {
  steps: ProvStep[];
  onRecord?: (rec: string, rid?: string) => void;
}

export function ProvChain({ steps, onRecord }: ProvChainProps): React.ReactElement {
  const { t } = useT();
  const nodes: React.ReactNode[] = [];
  steps.forEach((step, i) => {
    if (i > 0) nodes.push(e("span", { className: "vkpi-prov-arrow", key: `arr-${i}` }, "→"));
    const { label, href, rec, rid } = step;
    if (href) {
      // 外链节点:新开页(与 demo 一致 target=_blank rel=noopener;补 noreferrer 防泄 referrer)
      nodes.push(
        e(
          "a",
          {
            className: "vkpi-prov-pchip vkpi-prov-pchip--ext",
            href,
            target: "_blank",
            rel: "noopener noreferrer",
            key: `step-${i}`,
          },
          `${label} ↗`,
        ),
      );
    } else if (rec) {
      // 库节点:点击交回调用方(展开 RecordPreview / 身份跳档案)
      nodes.push(
        e(
          "button",
          {
            type: "button",
            className: "vkpi-prov-pchip",
            onClick: () => onRecord?.(rec, rid),
            key: `step-${i}`,
          },
          label,
        ),
      );
    } else {
      nodes.push(e("span", { className: "vkpi-prov-pchip", key: `step-${i}` }, label));
    }
  });
  return e(
    React.Fragment,
    null,
    e("div", { className: "vkpi-prov-chain" }, nodes),
    e("div", { className: "vkpi-prov-note" }, t("链上每跳可点 · 外链新开页 · 库节点展开记录预览")),
  );
}
