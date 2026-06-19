-- down 169:撤活动物料/产品清单表(CASCADE 索引随表一并 drop)。
DROP TABLE IF EXISTS vkpi_event_products;
DROP TABLE IF EXISTS vkpi_event_materials;
