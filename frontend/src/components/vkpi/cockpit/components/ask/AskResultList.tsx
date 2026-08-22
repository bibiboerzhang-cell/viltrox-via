// Ask ⌘K 候选列表(懒加载):分组渲染 + 全局序号 + 三态诚实空态。焦点常驻输入框,
// 选中态用 aria-activedescendant 指向 #vkpi-ask-result-{index},鼠标悬停同步选中。

import React from "react";
import {
  type LucideIcon,
  Activity,
  AlertCircle,
  ArrowRight,
  Boxes,
  Briefcase,
  Calendar,
  Clock3,
  Compass,
  Search,
  ShieldAlert,
  Sparkles,
  Users,
} from "lucide-react";
import type { AskCandidate, AskCandidateKind, AskEmptyKind } from "./askGrammar";
import type { AskGroup } from "./useAskCandidates";

const ICONS: Record<AskCandidateKind, LucideIcon> = {
  kol: Users,
  project: Briefcase,
  event: Calendar,
  sku: Boxes,
  nav: Compass,
  recent: Clock3,
  job: Activity,
  suggestion: Sparkles,
};

export interface AskResultListProps {
  groups: AskGroup[];
  activeIndex: number;
  zoneActive: boolean;
  emptyKind: AskEmptyKind | null;
  hasPrefix: boolean;
  loading: boolean;
  t: (text: string) => string;
  onHover: (index: number) => void;
  onActivate: (candidate: AskCandidate) => void;
}

function emptyCopy(kind: AskEmptyKind, hasPrefix: boolean, t: (text: string) => string): { title: string; body: string; tone: string; Icon: LucideIcon } {
  if (kind === "scope") {
    return { title: t("你的范围内没有"), body: t("当前账号可见范围内没有匹配；管理员可扩权后再试"), tone: "is-warning", Icon: ShieldAlert };
  }
  if (kind === "unavailable") {
    return { title: t("该来源暂不可用"), body: t("这不是零结果，稍后重试或改用其他前缀"), tone: "is-error", Icon: AlertCircle };
  }
  return {
    title: t("没有匹配的 @KOL / #项目 / $SKU"),
    body: hasPrefix ? t("换个前缀或关键词试试") : t("按 Tab 切到问 AI"),
    tone: "is-empty",
    Icon: Search,
  };
}

export default function AskResultList({ groups, activeIndex, zoneActive, emptyKind, hasPrefix, loading, t, onHover, onActivate }: AskResultListProps) {
  let cursor = 0;
  // 首屏三区(进行中/最近/建议)空也照渲染(诚实空态);检索分组由引擎只在有命中时给出。
  const total = groups.reduce((sum, group) => sum + group.candidates.length, 0);
  return (
    <div id="vkpi-ask-result-list" className={`vkpi-ask-dialog__results ${zoneActive ? "is-zone-active" : ""}`} role="listbox" aria-label={t("候选")}>
      {groups.map((group) => (
        <div className="vkpi-ask-result-group" key={group.key} data-group={group.key}>
          <h3>
            {group.title}
            {group.note ? <em>{group.note}</em> : null}
          </h3>
          {group.candidates.length === 0 && group.note ? null : group.candidates.length === 0 ? (
            <p className="vkpi-ask-result-group__empty">{t("暂无")}</p>
          ) : null}
          {group.candidates.map((candidate) => {
            const index = cursor++;
            const selected = zoneActive && index === activeIndex;
            const Icon = ICONS[candidate.origin || candidate.kind] || Search;
            return (
              <button
                id={`vkpi-ask-result-${index}`}
                type="button"
                role="option"
                key={candidate.id}
                className={selected ? "is-active" : ""}
                aria-selected={selected}
                data-kind={candidate.kind}
                onMouseEnter={() => onHover(index)}
                onClick={() => onActivate(candidate)}
              >
                <Icon size={13} />
                <span>{candidate.label}</span>
                <small>{candidate.detail}</small>
                <ArrowRight size={12} />
              </button>
            );
          })}
        </div>
      ))}
      {total === 0 && !loading && emptyKind ? (() => {
        const copy = emptyCopy(emptyKind, hasPrefix, t);
        return (
          <div className={`vkpi-ask-dialog__state ${copy.tone}`} role="status">
            <copy.Icon size={16} />
            <strong>{copy.title}</strong>
            <span>{copy.body}</span>
          </div>
        );
      })() : null}
    </div>
  );
}
