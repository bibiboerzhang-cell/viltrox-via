import React, { useState } from "react";
import { Camera, Package } from "lucide-react";
import MarketingMaterialsPanel from "./MarketingMaterialsPanel";
import ProductPrepPanel from "./ProductPrepPanel";
import type { EventVm, MaterialVm, ProductVm, StockItem } from "../shared/types";

const e = React.createElement;

interface MaterialsTabProps {
  ev: EventVm;
  stock: StockItem[];
  token: string;
  materials?: MaterialVm[];
  products?: ProductVm[];
  loading?: boolean;
  error?: string;
  reload?: () => void;
}

export default function MaterialsTab({ ev, stock, token, materials = [], products = [], loading, error, reload }: MaterialsTabProps) {
  const [sub, setSub] = useState("marketing");

  return e("div", null,
    // sub tabs
    e("div", { className: "flex items-center gap-1 px-5 pt-3 border-b border-white/[0.04]" },
      [
        { k: "marketing", l: "营销物料", i: Package },
        { k: "products",  l: "产品准备 (镜头/设备)", i: Camera },
      ].map(s => {
        const active = sub === s.k;
        const IS = s.i;
        return e("button", { key: s.k, onClick: () => setSub(s.k),
          className: `px-3 py-2 text-[11.5px] font-medium border-b-2 flex items-center gap-1.5 ${active ? "text-purple-300 border-purple-500" : "text-slate-400 border-transparent hover:text-white"}`
        }, e(IS, { size: 11 }), s.l);
      })
    ),
    sub === "marketing" && e(MarketingMaterialsPanel, { ev, token, materials, loading, error, reload }),
    sub === "products"  && e(ProductPrepPanel, { ev, stock, token, products, loading, error, reload })
  );
}
