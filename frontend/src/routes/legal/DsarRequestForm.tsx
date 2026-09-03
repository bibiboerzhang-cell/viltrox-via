// 公开 DSAR 申请表(删除 / 勿联系 / 查询)。匿名可用;提交到 /api/public/dsar/requests(IP 限流 5 次/小时)。
// 表单自身不展示任何已收录数据(后端也刻意不回显「是否命中」),只回一个回执号。
import { useState, type FormEvent } from "react";

import { useLocale } from "../../app/providers/LocaleProvider";
import { DSAR_SLA_DAYS_FALLBACK, pick, type Lang } from "./legalContent";
import {
  dsarErrorCode,
  submitDsarRequest,
  type DsarRequestPayload,
  type DsarRequestReceipt,
  type DsarRequestType,
  type LegalPolicy,
} from "./legalApi";

const REQUEST_TYPES: Array<{ value: DsarRequestType; zh: string; en: string }> = [
  { value: "erasure", zh: "删除我的档案", en: "Delete my profile" },
  { value: "do_not_contact", zh: "请勿联系我", en: "Do not contact me" },
  { value: "access", zh: "查询你们持有的关于我的数据", en: "Tell me what data you hold about me" },
];

const PLATFORMS: Array<{ value: string; label: string }> = [
  { value: "youtube", label: "YouTube" },
  { value: "instagram", label: "Instagram" },
  { value: "tiktok", label: "TikTok" },
  { value: "bilibili", label: "Bilibili" },
  { value: "other", label: "Other / 其他" },
];

const ERROR_MESSAGES: Record<string, { zh: string; en: string }> = {
  request_type_invalid: { zh: "请选择申请类型。", en: "Please choose a request type." },
  platform_invalid: { zh: "请选择平台。", en: "Please choose a platform." },
  handle_invalid: { zh: "账号名只能包含字母、数字、点、下划线和连字符。", en: "The handle may only contain letters, digits, dots, underscores and hyphens." },
  profile_url_invalid: { zh: "主页链接必须是 https:// 开头的完整地址。", en: "The profile link must be a full https:// address." },
  subject_missing: { zh: "请至少填写账号名或主页链接。", en: "Enter at least a handle or a profile link." },
  contact_email_invalid: { zh: "回复邮箱格式无效。", en: "The reply email address is invalid." },
  consent_required: { zh: "请确认你是该账号本人或其授权代表。", en: "Please confirm you own this account or represent its owner." },
  captcha_failed: { zh: "验证码校验未通过,请重试。", en: "The verification check failed; please try again." },
  captcha_mode_invalid: { zh: "验证码服务暂不可用,请稍后再试。", en: "Verification is temporarily unavailable; please try again later." },
  channel_unavailable: { zh: "申请通道暂不可用,请通过页脚邮箱联系我们。", en: "The request channel is unavailable right now; please email us at the address in the footer." },
  rate_limited: { zh: "提交过于频繁(每小时最多 5 次),请稍后再试。", en: "Too many submissions (at most 5 per hour); please try again later." },
  rejected: { zh: "请求被拒绝。", en: "The request was rejected." },
};

const EMAIL_RE = /^[^@\s]{1,64}@[^@\s]{1,189}\.[A-Za-z]{2,24}$/;
const HANDLE_RE = /^[A-Za-z0-9._-]{1,120}$/;

function emptyPayload(): DsarRequestPayload {
  return {
    request_type: "",
    platform: "",
    handle: "",
    profile_url: "",
    contact_email: "",
    message: "",
    consent_confirmed: false,
    captcha_token: "",
    website: "",
  };
}

/** 与后端 validate_public_request 同口径的前置校验;返回稳定 code(空串 = 通过)。 */
export function localValidationCode(payload: DsarRequestPayload): string {
  if (!payload.request_type) return "request_type_invalid";
  if (!payload.platform) return "platform_invalid";
  const handle = payload.handle.trim().replace(/^@/, "");
  if (handle && !HANDLE_RE.test(handle)) return "handle_invalid";
  const url = payload.profile_url.trim();
  if (url && !/^https:\/\/\S+$/i.test(url)) return "profile_url_invalid";
  if (!handle && !url) return "subject_missing";
  if (!EMAIL_RE.test(payload.contact_email.trim().toLowerCase())) return "contact_email_invalid";
  if (!payload.consent_confirmed) return "consent_required";
  return "";
}

function errorMessage(lang: Lang, code: string, fallback: string): string {
  const known = ERROR_MESSAGES[code];
  if (known) return pick(lang, known.zh, known.en);
  return fallback || pick(lang, "提交失败,请稍后再试。", "Submission failed; please try again later.");
}

interface Props {
  policy: LegalPolicy | null;
}

export function DsarRequestForm({ policy }: Props) {
  const { lang } = useLocale();
  const [payload, setPayload] = useState<DsarRequestPayload>(emptyPayload);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [receipt, setReceipt] = useState<DsarRequestReceipt | null>(null);
  const slaDays = policy?.dsar_sla_days ?? DSAR_SLA_DAYS_FALLBACK;

  const update = <K extends keyof DsarRequestPayload>(key: K, value: DsarRequestPayload[K]) => {
    setPayload((current) => ({ ...current, [key]: value }));
  };

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    const code = localValidationCode(payload);
    if (code) {
      setError(errorMessage(lang, code, ""));
      return;
    }
    setSubmitting(true);
    try {
      const result = await submitDsarRequest({
        ...payload,
        handle: payload.handle.trim(),
        profile_url: payload.profile_url.trim(),
        contact_email: payload.contact_email.trim(),
      });
      setReceipt(result);
    } catch (err) {
      const fallback = err instanceof Error ? err.message : String(err);
      setError(errorMessage(lang, dsarErrorCode(err), fallback));
    } finally {
      setSubmitting(false);
    }
  };

  if (receipt) {
    return (
      <div className="legal-alert legal-alert--ok" role="status" aria-live="polite">
        <div>{pick(lang, "申请已收到。你的回执号:", "Request received. Your reference:")} <strong>{receipt.public_ref}</strong></div>
        <div>
          {pick(
            lang,
            `我们会在 ${receipt.sla_days || slaDays} 天内通过你留下的邮箱回复。`,
            `We will reply to the email you provided within ${receipt.sla_days || slaDays} days.`,
          )}
        </div>
        {receipt.suppression ? (
          <div>{pick(lang, "你自报的邮箱已进入勿联系名单。", "The email you provided is now on the do-not-contact list.")}</div>
        ) : null}
      </div>
    );
  }

  return (
    <form className="legal-form" onSubmit={handleSubmit} noValidate aria-label={pick(lang, "申请表", "Request form")}>
      {error ? <div className="legal-alert legal-alert--error" role="alert" aria-live="polite">{error}</div> : null}

      <div className="legal-form__field">
        <label htmlFor="dsar-request-type">{pick(lang, "申请类型", "Request type")}</label>
        <select id="dsar-request-type" value={payload.request_type} onChange={(e) => update("request_type", e.target.value as DsarRequestType | "")} disabled={submitting}>
          <option value="">{pick(lang, "请选择", "Choose one")}</option>
          {REQUEST_TYPES.map((item) => (
            <option key={item.value} value={item.value}>{pick(lang, item.zh, item.en)}</option>
          ))}
        </select>
      </div>

      <div className="legal-form__row">
        <div className="legal-form__field">
          <label htmlFor="dsar-platform">{pick(lang, "平台", "Platform")}</label>
          <select id="dsar-platform" value={payload.platform} onChange={(e) => update("platform", e.target.value)} disabled={submitting}>
            <option value="">{pick(lang, "请选择", "Choose one")}</option>
            {PLATFORMS.map((item) => (
              <option key={item.value} value={item.value}>{item.label}</option>
            ))}
          </select>
        </div>
        <div className="legal-form__field">
          <label htmlFor="dsar-handle">{pick(lang, "账号名", "Handle")}</label>
          <input id="dsar-handle" value={payload.handle} onChange={(e) => update("handle", e.target.value)} placeholder="@your_handle" autoComplete="off" disabled={submitting} />
        </div>
      </div>

      <div className="legal-form__field">
        <label htmlFor="dsar-profile-url">{pick(lang, "主页链接(可选,https:// 开头)", "Profile link (optional, starts with https://)")}</label>
        <input id="dsar-profile-url" value={payload.profile_url} onChange={(e) => update("profile_url", e.target.value)} placeholder="https://" autoComplete="off" disabled={submitting} />
      </div>

      <div className="legal-form__field">
        <label htmlFor="dsar-contact-email">{pick(lang, "回复邮箱", "Reply email")}</label>
        <input id="dsar-contact-email" type="email" value={payload.contact_email} onChange={(e) => update("contact_email", e.target.value)} autoComplete="email" aria-describedby="dsar-contact-email-hint" disabled={submitting} />
        <span id="dsar-contact-email-hint" className="legal-form__hint">{pick(lang, "只用于答复你的申请;不会出现在平台任何列表里。", "Used only to answer this request; it never appears in any platform list.")}</span>
      </div>

      <div className="legal-form__field">
        <label htmlFor="dsar-message">{pick(lang, "留言(可选)", "Message (optional)")}</label>
        <textarea id="dsar-message" value={payload.message} maxLength={2000} onChange={(e) => update("message", e.target.value)} disabled={submitting} />
      </div>

      {/* 蜜罐:真人不可见;机器人填了后端直接拒。 */}
      <div className="legal-form__hp" aria-hidden="true">
        <label htmlFor="dsar-website">Website</label>
        <input id="dsar-website" name="website" tabIndex={-1} autoComplete="off" value={payload.website} onChange={(e) => update("website", e.target.value)} />
      </div>
      {/* 验证码接入位:接 Turnstile / hCaptcha 时把 token 写进这个字段,后端 _captcha_gate 校验。 */}
      <input type="hidden" name="captcha_token" value={payload.captcha_token} data-testid="dsar-captcha-token" />

      <label className="legal-form__check" htmlFor="dsar-consent">
        <input id="dsar-consent" type="checkbox" checked={payload.consent_confirmed} onChange={(e) => update("consent_confirmed", e.target.checked)} disabled={submitting} />
        <span>{pick(lang, "我确认我是该账号本人或其授权代表,所填信息属实。", "I confirm I own this account or represent its owner, and the information above is accurate.")}</span>
      </label>

      <button className="legal-btn" type="submit" disabled={submitting}>
        {submitting ? pick(lang, "提交中...", "Submitting...") : pick(lang, "提交申请", "Submit request")}
      </button>
      <span className="legal-form__hint">
        {pick(lang, `每个网络地址每小时最多提交 5 次;我们会在 ${slaDays} 天内回复。`, `At most 5 submissions per hour per network address; we reply within ${slaDays} days.`)}
      </span>
    </form>
  );
}

export default DsarRequestForm;
