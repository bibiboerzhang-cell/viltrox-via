// A·上框:视频 URL 的创作者账号信息卡(从 UrlSummary.tsx 原样抽出,行为不变)。
// 主信息由 creator_identity/profile_data/池档案合并,video_metadata 只补平台/频道标识;
// 原始字段退到折叠区。红线:纯展示,绝不写任何 viltrox_fit_score。
import { useState } from "react";

import { proxiedImageUrl } from "../../shared/mediaProxy";
import { cleanText, numberLabel, type Row } from "./SmartKolInputPanel.helpers";
import { firstSafeHttpUrl, publicFieldRows, safeHttpUrl } from "./SmartKolInputPanel.UrlSummary.shared";
import { containsOpaqueKolChannelId, isOpaqueKolChannelId, kolHumanDisplayName, kolHumanProfileLinkLabel } from "../lib/kolIdentity";

export function VideoCreatorCard({
  creator,
  metadata,
  onOpen,
}: {
  creator: Row;
  metadata: Row;
  onOpen?: () => void;
}) {
  const [failedAvatar, setFailedAvatar] = useState("");
  const [expanded, setExpanded] = useState(false);
  const avatar = proxiedImageUrl(safeHttpUrl(creator.avatar_url));
  const identity = {
    ...creator,
    channel_id: creator.channel_id || metadata.channel_id,
    channel_name: creator.channel_name || metadata.channel_name,
  };
  const platform = cleanText(creator.platform || metadata.platform);
  const name = kolHumanDisplayName(identity, "创作者");
  const followers = numberLabel(creator.followers ?? creator.subscriber_count);
  const posts = numberLabel(creator.posts_count ?? creator.video_count);
  const bio = cleanText(creator.bio || creator.description);
  const profileUrl = firstSafeHttpUrl(creator.profile_url, creator.channel_url);
  const profileLinkLabel = kolHumanProfileLinkLabel(identity);
  const showImg = Boolean(avatar) && failedAvatar !== avatar;
  // 全部字段(creator_identity 优先,video_metadata 兜底),空值过滤。
  const allFields = publicFieldRows(metadata, creator).filter(([key, value]) => (
    key !== "channel_id" && !isOpaqueKolChannelId(value, identity) && !containsOpaqueKolChannelId(value, identity)
  ));
  return (
    <div className="mt-2 rounded-md border border-white/[0.07] bg-black/20 px-2.5 py-2">
      <div className="flex items-start gap-3">
        <span
          className="flex h-11 w-11 shrink-0 items-center justify-center overflow-hidden rounded-full text-[14px] font-bold text-white"
          style={{ background: "linear-gradient(135deg,#7c3aed,#06b6d4)" }}
        >
          {showImg ? (
            <img
              src={avatar}
              alt=""
              className="h-full w-full rounded-full object-cover"
              referrerPolicy="no-referrer"
              onError={() => setFailedAvatar(avatar)}
            />
          ) : (
            name.slice(0, 1).toUpperCase()
          )}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="truncate text-[12px] font-medium text-slate-100">{name}</span>
            {platform ? (
              <span className="shrink-0 rounded border border-white/[0.08] px-1 text-[9px] text-slate-400">{platform}</span>
            ) : null}
            {followers ? (
              <span className="shrink-0 rounded bg-amber-400/[0.10] px-1 text-[9px] font-semibold text-amber-200/90">{followers} 粉</span>
            ) : null}
            {posts ? (
              <span className="shrink-0 rounded bg-cyan-400/[0.10] px-1 text-[9px] font-semibold text-cyan-200/90">{posts} 帖</span>
            ) : null}
            {onOpen ? (
              <button
                type="button"
                onClick={onOpen}
                className="shrink-0 text-[9px] font-medium text-cyan-300/80 hover:text-cyan-100"
              >
                查看档案 →
              </button>
            ) : null}
          </div>
          {bio ? (
            <p className="mt-1 line-clamp-2 text-[10.5px] leading-relaxed text-slate-400">{bio}</p>
          ) : null}
          {profileUrl ? (
            <a
              href={profileUrl}
              target="_blank"
              rel="noreferrer noopener"
              title={profileLinkLabel}
              className="mt-1 inline-block truncate text-[10px] text-cyan-300/80 hover:text-cyan-200 hover:underline"
            >
              {profileLinkLabel}
            </a>
          ) : null}
        </div>
        {allFields.length ? (
          <button
            type="button"
            onClick={() => setExpanded((cur) => !cur)}
            className="shrink-0 rounded border border-white/[0.1] px-2 py-0.5 text-[9.5px] text-slate-400 transition-colors hover:border-cyan-300/30 hover:text-cyan-100"
          >{expanded ? "收起原始字段" : `原始字段 ${allFields.length}`}</button>
        ) : null}
      </div>
      {expanded && allFields.length ? (
        <div className="mt-2 grid gap-x-3 gap-y-1 border-t border-white/[0.06] pt-2 text-[10px] sm:grid-cols-2">
          {allFields.map(([key, value]) => (
            <div key={key} className="flex min-w-0 gap-1.5">
              <span className="shrink-0 text-slate-600">{key}</span>
              <span className="min-w-0 flex-1 truncate text-slate-300" title={value}>{value}</span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
