# Nikon / Canon / Sony / Godox 美国 Dealer 官方来源审计

- 取证时间：2026-07-16 19:02–20:40 UTC
- 代码基线：`ab86b4054b36e51ec877801a93ffce9637c35f24`
- 审计范围：官方来源身份、现场记录数、字段粒度、访问限制与地图导入判定
- 写入边界：只更新来源注册表、离线适配器、测试夹具、测试和本文；没有写入 Dealer 业务表、没有创建地图点位、没有提交或部署

## 结论

四家官方来源都能证明某种厂商关系，但没有一家能够直接、完整、稳定地生成“全美实体分店地图”。Nikon、Canon、Sony 当前只适合作为组织候选宇宙；Godox 只发布三个美国 distributor/online-store 联系记录，而且官方条款明确禁止自动抓取与提取。因此本轮正确结果是：保留来源与证据、全部 fail-closed、导入数为零，再通过获批数据源或零售商自有门店页逐一解析实体分店。

厂商授权只证明该厂商关系，不证明 Viltrox 授权、Viltrox 产品存在、实时库存、销量、当地影响力或门店活动。

### 与实体店地图的边界

厂商大目录只用来生成待匹配组织名单，不直接上图。地图首批另行使用 5 家零售商官网已证明的线下门店：B&H New York、Adorama New York、Samy's Los Angeles、Samy's Orange County 和 Samy's Pasadena。只保留门店名、完整地址、电话、官网/来源和地址级坐标；Google Maps 搜索仅用于人工复核，没有可审计回执时保持 `pending`。

## 官方来源现场结果

| 厂商 | 官方来源 | 现场记录 | 官方字段 | 实际粒度 | 地图导入判定 |
| --- | --- | ---: | --- | --- | --- |
| Nikon USA | [Nikon Imaging Authorized Dealers PDF](https://www.nikonusa.com/where-to-buy/nikon_img_auth_dealers.pdf) | 175 行 / 175 个组织 / 42 个州或 DC | 组织名、城市、州、dealer type（NPD/NID） | 授权组织，不是分店 | 不可直接导入；缺街道、邮编、电话、网站、经纬度，且条款未授予商业自动复用 |
| Canon USA | [Canon Authorized Dealers PDF](https://www.usa.canon.com/content/dam/canon-assets/authorized-dealers/canon-ad-06-15-26.pdf) | 307 行 / 305 个唯一组织；13 页；46 个州或 DC，另有 GU；11 行无州 | 组织名、总部州 | 授权组织总部，不是分店；官方说明可能是实体或在线零售商且未必销售全部 Canon 产品 | 不可直接导入；缺分店和联系字段，条款限制普通使用为个人、非商业用途 |
| Sony USA | [Sony Where to Buy](https://www.sony.com/electronics/support/articles/00025266) / [官方 retailer directory](https://electronics.sony.com/retailers?path=%2Fretailers) | 页面渲染 2,224 行 / 2,223 个唯一名称 / 1 个重复 | retailer name | 零售商名称，不是实体门店 | 不可直接导入；没有地址、州、电话、网站或坐标，也没有自动商业复用授权 |
| Godox | [United States Authorized Distributor](https://www.godox.com/authorized-distributor/?ext_country=536) | 3 行 / 3 个组织 / 1 个州 | 组织名、国家、联系地址、邮箱、电话、online-store URL | distributor / online-store 联系记录，不是全国分店清单 | 严禁自动导入；官方条款 6.8、6.9 禁止 harvesting/mining 及 robot/crawler/scraper 自动提取 |

### Nikon 快照

- PDF：4 页；`Last-Modified: Thu, 16 Jul 2026 10:00:11 GMT`
- SHA-256：`406032919ca69701a93f6f355dd568cc92cef535ad7ff61068953c0da26ffedd`
- `robots.txt` 返回 200，未发现 PDF 路径被显式禁止；这不等于取得商业复用许可。
- [Nikon Terms of Use](https://www.nikonusa.com/content/terms-of-use) 将网站内容使用描述为个人、非商业和信息用途。本系统保持 `blocked_pending_publisher_permission`。

### Canon 快照

- PDF 标注 effective 2026-05-15；其州字段是总部州，不是分店覆盖。
- 官方 URL 的直接脚本访问受 Akamai 403 限制；`robots.txt` 也返回 403，无法据此判断路径规则。
- [Canon Terms of Use](https://www.usa.canon.com/terms-of-use) 未提供自动商业目录复用授权。本系统保持 `blocked_pending_publisher_permission`。

### Sony 快照

- 当前官方目录只暴露 retailer name；2,224 是页面条目数，不是门店数。
- `sony.com` 与 `electronics.sony.com` 的 `robots.txt` 在本次检查中均返回 403。
- 找到的 Sony 条款主要约束直接购买，没有发现目录自动商业复用授权。本系统保持 `blocked_pending_publisher_permission`。

### Godox 美国三条官方记录

| 组织 | 官方分类 | 联系地址 | 邮箱 | 电话 | 官方链接 |
| --- | --- | --- | --- | --- | --- |
| Adorama | Online Store | 42 West 18th St, New York, NY 10011 USA | sales@adorama.com | (800) 223-2500 | [adorama.com](https://adorama.com) |
| B&H | Online Store | 420 9th Ave, New York, NY 10001 USA | sales@bhphotovideo.com | (800) 606-6969 | [bhphotovideo.com](https://bhphotovideo.com) |
| GodoxUSA | Online Store | 75 Virginia Road, White Plains, NY 10603 | sales@godoxledusa.com | (914) 265-9119 | [godox.us](https://godox.us/) |

- 官方页面快照 SHA-256：`99dcc930a85e1a52bd4b2c7110e55fc7dc9082b3b093b2c45a60c163dccf2138`
- 三个地址只能记录为发布者给出的渠道联系地址；在没有门店证据前，不得自动变成三个实体门店地图点。
- `robots.txt` 返回 404；[Godox Terms of Use](https://www.godox.com/terms-of-use/) 第 6.8、6.9 节明确禁止自动访问和提取。因此注册表状态为 `blocked_terms_prohibit_automated_access_and_extraction`。

## Flashpoint 关系裁决

[Adorama 官方品牌页](https://www.adorama.com/g/adorama-brands) 将 Flashpoint 列为 Adorama exclusive brand；[Flashpoint 商品页](https://www.adorama.com/l/?sel=Brand_Flashpoint) 描述其 Godox 兼容性和 Adorama 提供的美国保修/支持。本次没有找到独立的 Flashpoint 美国授权 dealer 网络。因此只能建立：

`Flashpoint -> Adorama exclusive brand / sales and support channel`

不得把 Flashpoint 推导成另一份全国 Dealer 名录，也不得把 Godox 兼容性写成 Godox 对所有 Adorama 分店的授权或库存证明。

## Dealer 信息应该拆成什么

每个事实必须独立保存来源、取证时间和置信状态，不能用一个“Dealer”标签混为一体：

1. **组织身份**：法律/品牌名称、官网与稳定 ID。
2. **厂商关系**：Nikon、Canon、Sony、Godox 或 Viltrox 的授权范围和证据。
3. **实体分店**：门店名、精确地址、电话、营业状态、坐标；总部或在线渠道不能替代。
4. **线上渠道**：电商 URL、服务国家、是否为官方列出的 online store。
5. **产品证据**：独立的 Viltrox 商品 URL、抓取时间和可售状态；商品页不等于实时库存。
6. **社媒与当地覆盖**：只从零售商自有官网/官方账号映射，组织账号不能自动复制给每家分店。
7. **活动**：必须通过确切门店/组织关系链接 Event Radar，禁止靠名称或城市模糊猜测。
8. **服务与教育**：保修、维修、租赁、课程、展会或线下活动分别记录，不能由“授权零售商”推导。

## 实体解析和地图上线顺序

1. 保存官方来源 URL、检查时间、哈希和字段合同，来源注册表继续 `enabled=false`。
2. Nikon 175、Canon 305、Sony 2,223 只生成离线组织候选，不直接写 Dealer 业务表。
3. 通过厂商批准的 API/CSV、发布者书面许可，或零售商自有 store locator 获取实体分店。
4. 以组织域名、规范名称、精确地址和电话做身份解析与去重；连锁总部与分店分层建模。
5. 坐标只能由有来源的完整地址地理编码产生，并保存 geocoder、时间和置信度；不得伪造或用城市中心点。
6. 网站、电话、Instagram 等从零售商自有官方页逐字段取证；缺失就保持空值。
7. Viltrox 产品证据单独采集、单独过期，不把产品页面冒充库存或授权。
8. 人工复核后才发布地图；活动只投影 exact dealer-event relation。

### 各来源的下一步

- Nikon：175 个组织候选进入获批的 branch resolver；PDF 城市只能作为核对提示，不能当完整地址。
- Canon：305 个唯一组织按总部州做身份消歧，然后进入各零售商自有门店解析。
- Sony：2,223 个唯一名称先解决重复、别名和域名，再获取分店证据。
- Godox：不自动抓取。三条记录只保留来源关系元数据；Adorama/B&H 若已有经核验分店，只链接渠道关系，不新增地图点。

## 验收边界

- 官方来源数增加，不等于 Dealer 地图覆盖率增加。
- 当前四来源自动导入数：`0`。
- 当前四来源直接地图发布数：`0`。
- 只有取得允许使用且具备分店级字段的来源，完成身份解析、地址验证、坐标溯源和人工发布后，才能计入“全美 Dealer 地图”。
- 本文是工程访问与证据审计，不是法律意见；任何批量商业复用仍需发布者/公司法务确认。
