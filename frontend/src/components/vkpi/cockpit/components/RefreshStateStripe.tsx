// Verbatim from vkpi_v6.15.7_integrated.html


import React from "react";
import { REFRESH_STATE_INFO } from "../data/refreshStateInfo";

const e = React.createElement;

export function RefreshStateStripe({ state }: { state?: any }) {
  const info = (REFRESH_STATE_INFO as any)[state] || REFRESH_STATE_INFO.fresh;
  if (info.stripe === "transparent") return null;
  return e("span", {
    title: info.title,
    style: {
      position: "absolute", left: 0, top: 6, bottom: 6, width: 3, borderRadius: "0 2px 2px 0",
      background: info.stripe,
    }
  });
}
