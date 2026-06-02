export const INITIAL_STOCK = [
  // 镜头 - 样品库 (主要用于展会演示)
  { id: "s_135lab_sample",  name: "Viltrox 135mm LAB",       category: "lens", qty: 5,   location: "深圳样品仓", sku: "VTX-135-LAB-S",  note: "首批量产前样品 · 已通过 QA", isSample: true },
  { id: "s_27t2_sample",    name: "Viltrox 27mm T2 Cine",    category: "lens", qty: 4,   location: "深圳样品仓", sku: "VTX-27-T2-S",    note: "Cine 系列样品", isSample: true },
  { id: "s_16pro_sample",   name: "Viltrox 16mm F1.8 Pro",   category: "lens", qty: 2,   location: "深圳样品仓", sku: "VTX-16-PRO-S",   note: "新品 · 预发布样机", isSample: true },
  // 镜头 - 量产库存
  { id: "s_85pro",          name: "Viltrox 85mm F1.8 Pro",   category: "lens", qty: 200, location: "深圳量产仓", sku: "VTX-85-PRO",     note: "量产现货" },
  { id: "s_56pro",          name: "Viltrox 56mm F1.4 Pro",   category: "lens", qty: 0,   location: "深圳量产仓", sku: "VTX-56-PRO",     note: "缺货 · 工厂排产中" },
  { id: "s_75lab",          name: "Viltrox 75mm F1.4 LAB",   category: "lens", qty: 80,  location: "深圳量产仓", sku: "VTX-75-LAB",     note: "量产现货" },
  { id: "s_24pro",          name: "Viltrox 24mm F1.8 Pro",   category: "lens", qty: 150, location: "深圳量产仓", sku: "VTX-24-PRO",     note: "量产现货" },
  // 配件
  { id: "s_filter",         name: "镜头滤镜套装 (ND/CPL)",    category: "accessory", qty: 8,   location: "深圳样品仓",  sku: "ACC-FILTER", note: "用于现场演示" },
  { id: "s_lenscap",        name: "镜头帽 (周边)",            category: "accessory", qty: 300, location: "深圳量产仓",  sku: "ACC-CAP",    note: "礼品 / 备用" },
  { id: "s_usb_kit",        name: "USB-C 转接套装",          category: "accessory", qty: 15,  location: "美国仓 (LA)", sku: "ACC-USBC",   note: "演示用 · LA 仓直发" },
  { id: "s_strap",          name: "镜头肩带 (品牌)",          category: "accessory", qty: 50,  location: "深圳量产仓",  sku: "ACC-STRAP",  note: "礼品" },
  // 设备
  { id: "s_pelican",        name: "Pelican 大箱 (1620)",     category: "equipment", qty: 6,   location: "深圳运输仓",  sku: "EQ-PELICAN-1620", note: "用于跨境运输" },
  { id: "s_pelican_small",  name: "Pelican 中箱 (1510)",     category: "equipment", qty: 8,   location: "深圳运输仓",  sku: "EQ-PELICAN-1510" },
  { id: "s_banner",         name: "展位 KV 海报 (备用)",      category: "equipment", qty: 2,   location: "美国仓 (LA)", sku: "EQ-BANNER",  note: "通用版,无 event 特定信息" },
];
