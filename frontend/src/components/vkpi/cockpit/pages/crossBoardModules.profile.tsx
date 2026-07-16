import React from "react";
import { PendingCard } from "./MarketVoicePage.modules";
import { MODULE_SOURCES } from "./KolProfileBoardPage.modules";
import { SignatureEmbed } from "./KolProfileBoardPage.embeds";
import { XbCard, xbNoToken } from "./crossBoardModules.shell";

const BOARD_LABEL = "KOL 档案";
const PROFILE_ID_KEY = "vkpi:kol-profile-id";
const OPEN_PROFILE_EVENT = "vkpi:open-kol-profile";
const source = MODULE_SOURCES.signature;

function readProfileId() {
  try {
    const value = Number(window.sessionStorage.getItem(PROFILE_ID_KEY));
    return Number.isFinite(value) && value > 0 ? value : 0;
  } catch {
    return 0;
  }
}

export function ProfileSignatureXbCard({ apiToken, onOpenBoard }: { apiToken: string; onOpenBoard: () => void }) {
  const [kolId, setKolId] = React.useState(readProfileId);

  React.useEffect(() => {
    const refreshContext = () => setKolId(readProfileId());
    window.addEventListener(OPEN_PROFILE_EVENT, refreshContext);
    return () => window.removeEventListener(OPEN_PROFILE_EVENT, refreshContext);
  }, []);

  return (
    <XbCard
      title="招牌内容"
      cnt={kolId > 0 ? `KOL #${kolId}` : undefined}
      srcLabel={source.label}
      srcRows={[...source.rows, ["Dashboard 上下文", "读取最近一次打开的 KOL 档案 ID；未选择时不猜达人"]]}
      boardLabel={BOARD_LABEL}
      onOpenBoard={onOpenBoard}
      statusLabel={kolId > 0 ? "实时" : "待选择"}
    >
      {!apiToken ? xbNoToken(BOARD_LABEL) : kolId <= 0 ? (
        <PendingCard>
          <b>待选择 KOL</b> —— 先在 KOL Pool 或 KOL 档案打开一位达人，本模块随后读取该档案的真实招牌内容。
        </PendingCard>
      ) : (
        <SignatureEmbed apiToken={apiToken} kolId={kolId} />
      )}
    </XbCard>
  );
}
