from __future__ import annotations

OFFICIAL_MATRIX = [
    {"platform": "instagram", "handle": "viltrox.flash", "name": "Viltrox.Flash"},
    {"platform": "instagram", "handle": "viltrox.cine", "name": "Viltrox.Cine"},
    {"platform": "instagram", "handle": "viltroxcommunity", "name": "viltroxcommunity"},
    {"platform": "instagram", "handle": "viltrox.thailand", "name": "Viltrox.Thailand"},
    {"platform": "facebook", "handle": "viltrox.official", "name": "Viltrox.Official"},
    {"platform": "facebook", "handle": "viltrox.us", "name": "Viltrox.US"},
    {"platform": "facebook", "handle": "viltrox.flash", "name": "Viltrox.Flash"},
    {"platform": "facebook", "handle": "viltrox.cine", "name": "Viltrox.Cine"},
    {"platform": "twitter", "handle": "ViltroxOfficial", "name": "Viltrox.Official"},
    {"platform": "youtube", "handle": "viltroxofficial", "name": "Viltrox.Official"},
    {"platform": "tiktok", "handle": "viltrox.global", "name": "Viltrox.Global"},
    {"platform": "tiktok", "handle": "viltrox.usa", "name": "Viltrox.USA"},
    {"platform": "tiktok", "handle": "viltrox.flash", "name": "Viltrox.Flash"},
    {"platform": "tiktok", "handle": "viltrox.gear", "name": "Viltrox.Gear"},
    {"platform": "tiktok", "handle": "viltrox.store", "name": "Viltrox.Store"},
    {"platform": "reddit", "handle": "VILTROX_GLOBAL", "name": "VILTROX_GLOBAL"},
    {"platform": "discord", "handle": "viltrox", "name": "Viltrox"},
    {"platform": "fb group", "handle": "viltrox.global.user.group", "name": "Viltrox Global User Group"},
]

POSITIVE_LEXICON = {
    "sharp", "lightweight", "affordable", "portable", "fast", "beautiful", "great", "clean", "love",
    "性价比", "轻便", "轻量", "锐", "锐利", "好看", "好用", "舒服", "稳定", "喜欢", "值得",
}
NEGATIVE_LEXICON = {
    "soft", "price", "expensive", "heavy", "hunting", "compatibility", "issue", "problem", "bad", "slow",
    "兼容", "贵", "虚焦", "拉风箱", "对焦慢", "问题", "翻车", "不稳", "失望", "卡顿",
}
PURCHASE_LEXICON = {
    "buy", "ordered", "price", "worth", "should i", "where", "coupon", "link", "compare", "wishlist",
    "买吗", "想买", "下单", "链接", "在哪里买", "优惠", "值得买吗", "对比", "入手", "种草",
}
CRISIS_LEXICON = {
    "refund", "fake", "broken", "lawsuit", "scam", "angry", "boycott", "return",
    "退货", "退款", "假", "骗局", "生气", "拉黑", "别买", "质量问题",
}

COMPETITOR_GROUPS = {
    "first_party_oem": ["sony", "canon", "nikon", "fuji", "fujifilm", "panasonic"],
    "third_party_pro": ["sigma", "tamron", "samyang", "tokina"],
    "chinese_rivals": ["7artisans", "ttartisan", "yongnuo", "meike", "laowa"],
    "cine_specialists": ["dzofilm", "sirui", "nisi", "blazar"],
    "flash_light": ["godox", "neweer", "nanlite", "aputure"],
    "accessories": ["gimbal", "monitor", "cage", "peak design", "smallrig"],
    "wallet_share": ["iphone", "samsung", "dji", "insta360", "gopro"],
    "rental_buy": ["lensrentals", "sharegrid", "kitsplit"],
}

VISUAL_LIFE_SCENES = {
    "people": ["portrait", "family", "fashion", "wedding", "street", "人像", "家庭", "婚礼", "街拍", "模特"],
    "places": ["travel", "hotel", "architecture", "city", "room", "旅行", "建筑", "空间", "店", "房产"],
    "things": ["product", "food", "gear", "car", "shoe", "产品", "美食", "器材", "汽车", "鞋"],
    "stories": ["vlog", "documentary", "narrative", "bts", "纪录", "故事", "日常", "开箱"],
    "motion": ["sports", "dance", "wildlife", "kids", "运动", "舞蹈", "跑步", "宠物", "儿童"],
    "extreme": ["drone", "macro", "underwater", "astro", "医疗", "显微", "星空", "水下"],
}

AUDIENCE_ARCHETYPES = {
    "pro_cinema": ["dp", "cinema", "commercial", "filmmaker", "电影", "商业拍摄"],
    "pro_photo": ["wedding", "portrait", "real estate", "product", "婚礼", "写真", "房产", "产品拍摄"],
    "content_creator": ["youtube", "tiktok", "vlog", "livestream", "短视频", "直播", "博主"],
    "enthusiast": ["street", "landscape", "hobby", "learning", "街拍", "风光", "爱好"],
    "life_capture": ["kids", "travel", "food", "daily", "亲子", "旅行", "日常"],
    "business": ["e-commerce", "cafe", "gym", "salon", "店主", "电商", "咖啡", "健身房"],
    "education": ["school", "tutorial", "online", "教程", "教育", "课堂"],
    "crossover": ["drone", "macro", "medical", "scientific", "无人机", "显微", "医疗"],
}
