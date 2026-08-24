import React from "react";

// 模块尺寸上下文(消灭「放大后黑边」):EditableDashboardBoard 在每个模块 section 内
// 注入当前棋盘格高 / 像素高 / 跨列数,列表型模块(KOL 库 / 内容墙等)据此把硬切片
// 行数换成「按实际可用高度实算」。不读上下文的模块零感知(默认 null → 各自旧口径),
// 独立挂载(测试 / 其他棋盘)同样拿到 null,行为与改造前逐字节一致。

export interface ModuleSizeValue {
  /** 模块高(棋盘行格数,react-grid-layout 的 h) */
  heightRows: number;
  /** 模块像素高:h*行高 + (h-1)*行距(与棋盘渲染公式一致,由棋盘算好注入) */
  heightPx: number;
  /** 模块跨列数(react-grid-layout 的 w;当前列制下的实际值) */
  spanCols: number;
}

export const ModuleSizeContext = React.createContext<ModuleSizeValue | null>(null);

export function useModuleSize(): ModuleSizeValue | null {
  return React.useContext(ModuleSizeContext);
}

/** 纯函数:按模块像素高扣除头尾 chrome 后能放下的行数,夹在 [min, max]。
 *  max < min(数据不足 min 行)以 max 为准;非有限入参 / rowPx<=0 按保底下限处理,
 *  绝不返回负数——调用方拿到的值可直接喂 slice(0, n)。 */
export function adaptiveRowCount({ heightPx, chromePx, rowPx, min, max }: {
  heightPx: number;
  chromePx: number;
  rowPx: number;
  min: number;
  max: number;
}): number {
  const safeMin = Number.isFinite(min) ? Math.max(0, Math.floor(min)) : 0;
  const safeMax = Number.isFinite(max) ? Math.max(0, Math.floor(max)) : safeMin;
  const lower = Math.min(safeMin, safeMax);
  if (!Number.isFinite(heightPx) || !Number.isFinite(chromePx) || !Number.isFinite(rowPx) || rowPx <= 0) {
    return lower;
  }
  const fit = Math.floor((heightPx - chromePx) / rowPx);
  return Math.min(safeMax, Math.max(lower, fit));
}

/** Provider 包装:value 按三个标量 memo,避免棋盘每次渲染都换引用把消费者拖着重渲染。 */
export function ModuleSizeProvider({ heightRows, heightPx, spanCols, children }: ModuleSizeValue & {
  children?: React.ReactNode;
}) {
  const value = React.useMemo(
    () => ({ heightRows, heightPx, spanCols }),
    [heightRows, heightPx, spanCols],
  );
  return React.createElement(ModuleSizeContext.Provider, { value }, children);
}
