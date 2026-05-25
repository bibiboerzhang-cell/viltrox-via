import type { ReactNode } from 'react';
import { AIBriefCard, type AIBriefCardProps } from './AIBriefCard';
import type { GlassMission } from './tokens';

const defaultMissions: GlassMission[] = [
  { value: '7', suffix: 'actions', label: '今日关键动作' },
  { value: '3', suffix: 'risks', label: '项目 / 竞品风险' },
  { value: '12', suffix: 'KOL', label: '新候选待评估' },
];

interface HeroSectionProps {
  eyebrow?: ReactNode;
  title?: ReactNode;
  body?: ReactNode;
  missions?: GlassMission[];
  summary?: ReactNode;
  control?: ReactNode;
  brief?: AIBriefCardProps;
}

export function HeroSection({
  eyebrow = 'V-KPI MISSION CONTROL',
  title = '全球营销情报中枢',
  body = '重点 / 风险 / 任务',
  missions = defaultMissions,
  summary,
  control,
  brief,
}: HeroSectionProps) {
  return (
    <section className="hero">
      <div className="hero-title">
        <div className="hero-title-head">
          <div className="eyebrow"><span className="orb"></span>{eyebrow}</div>
          {control ? <div className="hero-control">{control}</div> : null}
        </div>
        <h1>{title}</h1>
        <p>{body}</p>
        {missions.length ? (
          <div className="mini-missions">
            {missions.map((mission, index) => (
              <div className="mini-mission" key={index}>
                <b>{mission.value} <span>{mission.suffix}</span></b>
                {mission.label}
              </div>
            ))}
          </div>
        ) : null}
        {summary}
      </div>
      <AIBriefCard {...brief} />
    </section>
  );
}
