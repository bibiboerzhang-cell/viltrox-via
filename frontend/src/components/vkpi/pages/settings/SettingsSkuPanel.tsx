import React from 'react';
import type { VkpiProductCatalogItem } from '../../vkpiTypes';
import {
  ProductCatalogPreviewCard,
  ProductCostFormCard,
} from './SettingsAdminCards';

interface SettingsSkuPanelProps {
  costSku: string;
  costProductName: string;
  unitCostUsd: string;
  costNote: string;
  selectedCatalogProduct: VkpiProductCatalogItem | null;
  busy: boolean;
  canUpsert: boolean;
  productCatalog: VkpiProductCatalogItem[];
  productCatalogLoading: boolean;
  productCatalogError: string;
  productSearch: string;
  onCostSkuChange: (value: string) => void;
  onCostProductNameChange: (value: string) => void;
  onUnitCostUsdChange: (value: string) => void;
  onCostNoteChange: (value: string) => void;
  onProductSearchChange: (value: string) => void;
  onSelectProduct: (product: VkpiProductCatalogItem) => void;
  onSubmitProductCost: React.FormEventHandler;
}

export function SettingsSkuPanel({
  costSku,
  costProductName,
  unitCostUsd,
  costNote,
  selectedCatalogProduct,
  busy,
  canUpsert,
  productCatalog,
  productCatalogLoading,
  productCatalogError,
  productSearch,
  onCostSkuChange,
  onCostProductNameChange,
  onUnitCostUsdChange,
  onCostNoteChange,
  onProductSearchChange,
  onSelectProduct,
  onSubmitProductCost,
}: SettingsSkuPanelProps) {
  return (
    <section className="vkpi-settings-product-row">
      <ProductCostFormCard
        costSku={costSku}
        costProductName={costProductName}
        unitCostUsd={unitCostUsd}
        costNote={costNote}
        selectedProduct={selectedCatalogProduct}
        busy={busy}
        canUpsert={canUpsert}
        onCostSkuChange={onCostSkuChange}
        onCostProductNameChange={onCostProductNameChange}
        onUnitCostUsdChange={onUnitCostUsdChange}
        onCostNoteChange={onCostNoteChange}
        onSubmit={onSubmitProductCost}
      />
      <ProductCatalogPreviewCard
        products={productCatalog}
        loading={productCatalogLoading}
        error={productCatalogError}
        query={productSearch}
        selectedSku={selectedCatalogProduct?.sku}
        onQueryChange={onProductSearchChange}
        onSelectProduct={onSelectProduct}
      />
    </section>
  );
}
