import React from "react";

type ViewerContextArgs = {
  apiToken: string;
  item: any;
};

/**
 * Loads the current viewer's share/claim context and owns the claim-release
 * interaction. Keeping this lifecycle together prevents the drawer component
 * from carrying another independent async state machine.
 */
export function useKOLDrawerViewerContext({ apiToken, item }: ViewerContextArgs) {
  const [viewerCtx, setViewerCtx] = React.useState<any>(null);
  const [releaseBusy, setReleaseBusy] = React.useState(false);
  const [releaseMsg, setReleaseMsg] = React.useState("");

  React.useEffect(() => {
    setViewerCtx(null);
    setReleaseBusy(false);
    setReleaseMsg("");
    if (!apiToken || !item?.id) return;
    let cancelled = false;
    void import("../../../../services/vkpi/kol-api")
      .then(({ getMyKolViewerContext }) => getMyKolViewerContext(apiToken, item.id))
      .then((payload: any) => {
        if (!cancelled) setViewerCtx(payload && typeof payload === "object" ? payload : null);
      })
      .catch(() => {
        if (!cancelled) setViewerCtx(null);
      });
    return () => {
      cancelled = true;
    };
  }, [apiToken, item?.id]);

  const handleReleaseClaim = React.useCallback(() => {
    const claimId = viewerCtx?.claim?.id;
    if (!apiToken || !claimId || releaseBusy) return;
    const kolLabel = String(item?.display_name || item?.handle || "该 KOL");
    if (!window.confirm(`确认释放对「${kolLabel}」的认领?释放后该 KOL 可被其他成员认领。`)) return;

    setReleaseBusy(true);
    setReleaseMsg("");
    const snapshot = viewerCtx;
    setViewerCtx((previous: any) => (previous ? { ...previous, claim: null } : previous));
    void import("../../../../services/vkpi/kol-api")
      .then(({ releaseKolClaim }) => releaseKolClaim(apiToken, String(claimId)))
      .then(() => setReleaseMsg("已释放认领"))
      .catch((error: any) => {
        setViewerCtx(snapshot);
        setReleaseMsg("释放失败:" + String(error?.message || "请重试").slice(0, 80));
      })
      .finally(() => setReleaseBusy(false));
  }, [apiToken, viewerCtx, releaseBusy, item?.display_name, item?.handle]);

  return {
    viewerCtx,
    releaseBusy,
    releaseMsg,
    handleReleaseClaim,
  };
}
