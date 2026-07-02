// 分析区块可收起:统一折叠头。用户诉求是「能收起」而非「默认藏起」,所以 defaultOpen 默认 true;
// 折叠状态持久化到 localStorage(按区块 id 记忆,跨 KOL/跨会话生效),读写都 try/catch(隐私模式下
// localStorage 可能抛异常,折叠是增益功能,失败不阻塞渲染)。
// 红线:纯 UI 包裹组件,零业务逻辑/零数据流;收起时 children 不渲染(区块内多为大列表/条件块,省开销)。

import React from "react";
import { ChevronDown } from "lucide-react";

const e = React.createElement;

const STORAGE_PREFIX = "vkpi:drawer-fold:";

function readFoldState(id: string, defaultOpen: boolean): boolean {
  try {
    const raw = window.localStorage.getItem(STORAGE_PREFIX + id);
    if (raw === "open") return true;
    if (raw === "closed") return false;
  } catch {
    // 读失败按默认(不打日志:折叠态丢了没有业务后果)
  }
  return defaultOpen;
}

export function SectionFold({ id, header, defaultOpen = true, children }: any) {
  // 惰性初始化:只在首挂载读一次 localStorage(区块 id 是静态字面量,不会中途变)
  const [open, setOpen] = React.useState<boolean>(() => readFoldState(id, defaultOpen));
  const toggle = () => {
    setOpen((prev: boolean) => {
      const next = !prev;
      try {
        window.localStorage.setItem(STORAGE_PREFIX + id, next ? "open" : "closed");
      } catch {
        // 写失败不阻塞交互,本次会话内 state 仍然生效
      }
      return next;
    });
  };
  return e(React.Fragment, null,
    e("button", {
      type: "button",
      onClick: toggle,
      "aria-expanded": open,
      className: "flex w-full items-center gap-1.5 mb-2 text-left",
    },
      header,
      // 收起时 chevron 转 -90°(指向右),与常见折叠面板方向语义一致
      e(ChevronDown, {
        size: 12,
        className: "ml-auto shrink-0 text-slate-500 transition-transform" + (open ? "" : " -rotate-90"),
      })
    ),
    open ? children : null
  );
}
