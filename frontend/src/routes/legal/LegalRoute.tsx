// 公测法务页(L-legal-dsar):/legal 目录页 + /legal/{terms|privacy|data-sources|request}。
// 匿名可达;与 /activate 同款独立门面(不进 cockpit 壳);全 token 皮肤;文案全部标「草案,待法务审」。
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { useLocale } from "../../app/providers/LocaleProvider";
import { frontendBuildInfo, shortBuildSha } from "../../lib/buildInfo";
import { PUBLIC_SURFACE_NAME } from "../../lib/publicSurface";
import { ThemeSwitch } from "../../shared/ThemeSwitch";
import { DsarRequestForm } from "./DsarRequestForm";
import { fetchLegalPolicy, type LegalPolicy } from "./legalApi";
import {
  CONTACT_EMAIL_PLACEHOLDER,
  DRAFT_NOTICE,
  HUB_BLURBS,
  LEGAL_DOCS,
  LEGAL_DRAFT_VERSION,
  LEGAL_PAGE_KEYS,
  PAGE_TITLES,
  RETENTION_FALLBACK,
  RETENTION_GATE_ENV,
  RETENTION_TASK_KEY,
  isLegalPageKey,
  pick,
  type Lang,
  type LegalDoc,
  type LegalPageKey,
  type RetentionRow,
} from "./legalContent";
import "../../styles/admin.css";
import "../../styles/legal.css";

type PolicyState = "loading" | "live" | "fallback";

function retentionRows(policy: LegalPolicy | null): RetentionRow[] {
  if (!policy || !Array.isArray(policy.retention) || policy.retention.length === 0) return [...RETENTION_FALLBACK];
  return policy.retention.map((row) => ({
    bucket: row.bucket,
    policyKey: row.policy_key,
    days: Number(row.days) || 0,
    defaultDays: Number(row.default_days) || 0,
    labelZh: row.label_zh,
    labelEn: row.label_en,
  }));
}

function LegalHub({ lang }: { lang: Lang }) {
  return (
    <div className="legal-doc">
      <h1>{pick(lang, "法务与隐私", "Legal & privacy")}</h1>
      <p className="legal-intro">
        {pick(lang, "以下四页说明我们如何使用平台、处理数据,以及创作者如何提出删除或勿联系申请。", "These four pages explain how the platform may be used, how data is handled, and how creators can ask for deletion or no contact.")}
      </p>
      <div className="legal-hub">
        {LEGAL_PAGE_KEYS.map((key) => (
          <Link key={key} to={`/legal/${key}`}>
            <strong>{pick(lang, PAGE_TITLES[key].zh, PAGE_TITLES[key].en)}</strong>
            <span>{pick(lang, HUB_BLURBS[key].zh, HUB_BLURBS[key].en)}</span>
          </Link>
        ))}
      </div>
    </div>
  );
}

function LegalDocument({ doc }: { doc: LegalDoc }) {
  return (
    <div className="legal-doc">
      <h1>{doc.title}</h1>
      <p className="legal-intro">{doc.intro}</p>
      {doc.sections.map((section) => (
        <section key={section.h}>
          <h2>{section.h}</h2>
          {section.p?.map((text) => <p key={text}>{text}</p>)}
          {section.li ? <ul>{section.li.map((item) => <li key={item}>{item}</li>)}</ul> : null}
        </section>
      ))}
    </div>
  );
}

function RetentionTable({ lang, policy, state }: { lang: Lang; policy: LegalPolicy | null; state: PolicyState }) {
  const rows = retentionRows(policy);
  const purgeEnabled = policy?.purge_enabled ?? false;
  return (
    <section className="legal-doc" aria-label={pick(lang, "保留期表", "Retention table")}>
      <h2>{pick(lang, "保留期一览(策略键 · 当前生效天数)", "Retention at a glance (policy key · effective days)")}</h2>
      <div className="legal-table-wrap">
        <table className="legal-table">
          <thead>
            <tr>
              <th>{pick(lang, "数据", "Data")}</th>
              <th>{pick(lang, "策略键", "Policy key")}</th>
              <th>{pick(lang, "保留天数", "Days")}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.policyKey}>
                <td>{pick(lang, row.labelZh, row.labelEn)}</td>
                <td><code className="legal-key">{row.policyKey}</code></td>
                <td>{row.days === 0 ? pick(lang, "即时", "immediate") : String(row.days)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="legal-meta">
        {pick(lang, "执行任务", "Purge task")} <code className="legal-key">{policy?.purge_task_key || RETENTION_TASK_KEY}</code>
        {" · "}
        {pick(lang, "放量闸", "Gate")} <code className="legal-key">{policy?.purge_gate_env || RETENTION_GATE_ENV}</code>
        {" · "}
        {purgeEnabled ? pick(lang, "已开启(按表清理)", "enabled (purging per table)") : pick(lang, "默认关闭(每日只报数)", "off by default (daily count only)")}
        {" · "}
        {state === "live"
          ? pick(lang, "数值为当前生效值", "values are the live configuration")
          : state === "loading"
            ? pick(lang, "正在读取当前配置…", "loading live configuration…")
            : pick(lang, "接口暂不可用,显示默认值", "live configuration unavailable; showing defaults")}
      </p>
    </section>
  );
}

export default function LegalRoute() {
  const { page } = useParams<{ page?: string }>();
  const { lang } = useLocale();
  const pageKey: LegalPageKey | null = isLegalPageKey(page) ? page : null;
  const [policy, setPolicy] = useState<LegalPolicy | null>(null);
  const [policyState, setPolicyState] = useState<PolicyState>("loading");

  useEffect(() => {
    let cancelled = false;
    fetchLegalPolicy()
      .then((result) => {
        if (cancelled) return;
        setPolicy(result);
        setPolicyState("live");
      })
      .catch(() => {
        if (!cancelled) setPolicyState("fallback");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const contactEmail = policy?.contact_email || CONTACT_EMAIL_PLACEHOLDER;
  const contactConfigured = policy?.contact_email_configured ?? false;
  const doc = pageKey ? LEGAL_DOCS[pageKey][lang] : null;

  return (
    <div className="legal-viewport">
      <div style={{ position: "absolute", top: 18, right: 18, zIndex: 2 }}>
        <ThemeSwitch />
      </div>
      <main className="legal-card" role="main">
        <header className="legal-card__brand">
          <span className="admin-root__mark">V</span>
          <div>
            <div className="legal-card__brand-text">{PUBLIC_SURFACE_NAME}</div>
            <div className="legal-card__brand-sub">{pick(lang, "法务与隐私", "Legal & privacy")}</div>
          </div>
        </header>

        <nav className="legal-nav" aria-label={pick(lang, "法务页导航", "Legal pages")}>
          {LEGAL_PAGE_KEYS.map((key) => (
            <Link key={key} to={`/legal/${key}`} className={key === pageKey ? "is-active" : undefined} aria-current={key === pageKey ? "page" : undefined}>
              {pick(lang, PAGE_TITLES[key].zh, PAGE_TITLES[key].en)}
            </Link>
          ))}
        </nav>

        <div className="legal-draft" role="note">{pick(lang, DRAFT_NOTICE.zh, DRAFT_NOTICE.en)}</div>

        {doc ? <LegalDocument doc={doc} /> : <LegalHub lang={lang} />}
        {pageKey === "privacy" ? <RetentionTable lang={lang} policy={policy} state={policyState} /> : null}
        {pageKey === "request" ? <DsarRequestForm policy={policy} /> : null}

        <footer className="legal-foot">
          <span>{pick(lang, "草案版本", "Draft version")} {policy?.version || LEGAL_DRAFT_VERSION}</span>
          <span>
            {pick(lang, "联系邮箱", "Contact")} <a href={`mailto:${contactEmail}`}>{contactEmail}</a>
            {contactConfigured ? "" : pick(lang, "(占位,待确认)", " (placeholder, to be confirmed)")}
          </span>
          <Link to="/login">{pick(lang, "返回登录", "Back to sign in")}</Link>
          <span title={`${frontendBuildInfo.gitBranch} · ${frontendBuildInfo.gitSha} · built ${frontendBuildInfo.builtAt}`}>
            {pick(lang, "前端版本", "Build")} {shortBuildSha(frontendBuildInfo.gitSha)}
          </span>
        </footer>
      </main>
    </div>
  );
}
