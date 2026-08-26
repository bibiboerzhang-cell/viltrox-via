"""垂类词表与口径表(车道 3,2026-08-25)。

只放**数据**:9 个垂类的门面中文、六条文本取证路的门面中文、AND-of-OR 词表、
平台内容分类映射与双阈值、器材品牌表。判定引擎在 ``profile_vertical_signals``。

拆成两个文件是因为词表天然会长(生活方式一类就有 7 条并列规则),把它和引擎放一起
迟早撑破行数守卫。改词表**只改这里**,引擎不用动。

red line:纯数据模块,零 import 副作用、不读库、不写库。
"""
from __future__ import annotations


# ── 垂类与取证路的门面口径(中文,禁内部术语)────────────────────────────────

#: 与前端 ``SmartKolInputPanel.SearchPolicy.tsx`` 的 VERTICAL_OPTIONS 逐项对齐。
VERTICAL_KEYS: tuple[str, ...] = (
    "lens_review",
    "photography_tutorial",
    "gear_comparison",
    "portrait",
    "video_creation",
    "camera_system",
    "vlog",
    "lifestyle",
    "technology",
)

VERTICAL_LABELS_ZH: dict[str, str] = {
    "lens_review": "镜头评测",
    "photography_tutorial": "摄影教程",
    "gear_comparison": "器材对比",
    "portrait": "人像创作",
    "video_creation": "视频创作",
    "camera_system": "相机系统",
    "vlog": "Vlog",
    "lifestyle": "生活方式",
    "technology": "科技",
}

ROUTE_PROFILE_TEXT = "profile_text"
ROUTE_CHANNEL_KEYWORDS = "channel_keywords"
ROUTE_PROFILE_CATEGORY = "profile_category"
ROUTE_VIDEO_TITLES = "video_titles"
ROUTE_CONTENT_LABELS = "content_labels"
ROUTE_USED_LENSES = "used_lenses"
ROUTE_TAGGED_BRANDS = "tagged_brands"
ROUTE_VIDEO_CATEGORY = "video_category"

#: 文本取证路(跑词表规则),按可信度从高到低排,同一垂类同一路只留一条引证。
TEXT_ROUTES: tuple[str, ...] = (
    ROUTE_CHANNEL_KEYWORDS,
    ROUTE_PROFILE_CATEGORY,
    ROUTE_PROFILE_TEXT,
    ROUTE_VIDEO_TITLES,
    ROUTE_CONTENT_LABELS,
    ROUTE_USED_LENSES,
)

ROUTE_LABELS_ZH: dict[str, str] = {
    ROUTE_PROFILE_TEXT: "主页简介",
    ROUTE_CHANNEL_KEYWORDS: "频道关键词",
    ROUTE_PROFILE_CATEGORY: "平台身份标注",
    ROUTE_VIDEO_TITLES: "作品标题",
    ROUTE_CONTENT_LABELS: "作品主题标记",
    ROUTE_USED_LENSES: "作品里识别到的镜头",
    ROUTE_TAGGED_BRANDS: "作品里标记的品牌",
    ROUTE_VIDEO_CATEGORY: "平台内容分类",
}

#: 一个人最多留多少条引证(每垂类)。够解释即可,不把整段语料塞进响应。
MAX_EVIDENCE_PER_VERTICAL = 4


# ── 词表:AND-of-OR 规则。一个垂类可有多条规则,任意一条成立即归类 ──────────────
#
# 兼容性:每个垂类的第一条规则都是历史 ``_VERTICAL_FILTER_GROUPS`` 的原词组(逐字保留,
# 只补了历史靠子串顺带命中的复数形 lenses / vlogs),后面的是本次补的多维度规则。
#
# **不是逐人向后兼容,说清楚**:prod 2034 人快照对照(2026-08-25)显示,历史判得出、
# 新口径判不出的共 58 人,分两类且都是**收紧**,不是漏判:
#   ① 词边界(26 人):历史裸子串把 "tech" 命中 "technique"、"camera" 命中
#      "@spatialcamera" —— 这类误伤本就该断,本次刻意不恢复。
#   ② 一条规则两组词必须落在**同一条取证路**(32 人):历史是把简介+标题+标签拼成
#      一个 blob,于是「简介里有 lens」+「标题里有 review」也算镜头评测。现在证据
#      必须自洽。代价已量化,收益见九垂类覆盖对照(每一类都净增)。

TEXT_RULES: dict[str, tuple[tuple[tuple[str, ...], ...], ...]] = {
    "lens_review": (
        (("lens", "lenses", "镜头"), ("review", "reviewer", "comparison", "评测", "测评", "对比")),
        (("lens", "lenses", "镜头", "定焦", "变焦", "glass"),
         ("versus", "vs", "compare", "hands on", "first look", "实测", "开箱", "横评", "上手")),
        (("lens review", "lens test", "镜头评测", "镜头测评"),),
    ),
    "photography_tutorial": (
        (("photo", "photography", "摄影"), ("tutorial", "tips", "guide", "教程", "教学")),
        (("photo", "photography", "photographer", "摄影", "拍照", "lightroom", "photoshop"),
         ("how to", "howto", "learn", "lesson", "course", "workshop", "技巧", "入门", "课程", "干货")),
        (("photography tutorial", "photography tips", "learn photography", "摄影教程", "摄影技巧"),),
    ),
    "gear_comparison": (
        # "lenses" 是历史裸子串("lens" 命中 "lenses")顺带覆盖到的形,封边界后必须显式补回。
        (("gear", "camera", "lens", "lenses", "器材", "相机", "镜头"),
         ("comparison", "versus", "review", "对比", "横评", "评测")),
        (("gear", "camera", "lens", "lenses", "器材", "相机", "镜头", "body", "bodies", "kit"),
         ("vs", "compare", "shootout", "showdown", "which one", "best", "横评", "对比评测", "选购")),
    ),
    "portrait": (
        (("portrait", "fashion", "人像", "肖像"),),
        (("portraiture", "headshot", "boudoir", "glamour", "模特", "写真", "wedding photo",
          "wedding photographer", "婚纱", "婚礼摄影", "beauty photography", "studio portrait"),),
    ),
    "video_creation": (
        (("video", "filmmaker", "filmmaking", "cinematic", "视频", "影视", "电影"),),
        (("filmmak", "cinematograph", "videograph", "short film", "documentary", "video editing",
          "video production", "color grading", "b-roll", "broll", "davinci", "premiere pro",
          "final cut", "剪辑", "调色", "运镜", "短片", "分镜"),),
    ),
    "camera_system": (
        (("camera system", "camera", "相机系统", "相机"),),
        (("mirrorless", "dslr", "full frame", "full-frame", "aps-c", "medium format", "camera body",
          "sensor", "firmware", "lens mount", "卡口", "机身", "无反", "微单", "单反", "全画幅",
          "中画幅", "传感器", "固件"),),
    ),
    "vlog": (
        # "vlogs" 同理:历史 "vlog" 子串把复数一并吃下(prod 快照里 23 人正是写 "vlogs"),
        # 封边界后必须显式补回,否则这一类的主流写法反而搜不到人。
        (("vlog", "vlogs", "vlogger", "日常记录"),),
        (("vlogging", "daily vlog", "travel vlog", "day in my life", "day in the life", "blog",
          "blogs", "blogger", "博主", "生活记录", "日常分享", "随手拍"),),
    ),
    # 生活方式刻意做成多条并列规则(旅行 / 美食 / 时尚美妆 / 健身 / 家居亲子),
    # 用户原话:「生活方式要的多维度但是要能搜到人」。每条都单独引证,不混成一锅。
    "lifestyle": (
        (("lifestyle", "生活方式", "生活记录"),),
        (("daily life", "everyday life", "slow living", "生活日常", "日常生活", "记录生活"),),
        (("travel", "traveling", "travelling", "wanderlust", "road trip", "hiking", "camping",
          "outdoor", "adventure", "旅行", "旅拍", "旅游", "户外", "露营", "徒步"),),
        (("food", "foodie", "cooking", "recipe", "restaurant", "coffee", "cafe", "美食", "咖啡",
          "探店", "料理", "烘焙"),),
        (("fashion", "outfit", "ootd", "streetwear", "beauty", "makeup", "skincare", "时尚",
          "穿搭", "美妆", "护肤"),),
        (("fitness", "workout", "gym", "wellness", "yoga", "running", "健身", "瑜伽", "跑步"),),
        (("home decor", "interior design", "gardening", "家居", "家装", "园艺", "parenting",
          "family life", "亲子", "育儿", "母婴"),),
    ),
    "technology": (
        (("technology", "tech", "科技", "数码"),),
        (("gadget", "smartphone", "iphone", "android", "laptop", "consumer electronics",
          "unboxing", "手机评测", "开箱", "电子产品", "极客"),),
    ),
}


# ── 平台内容分类路(YouTube 视频分类直方图)─────────────────────────────────
#
# prod 实测直方图 772 行里 728 行只有 1 条样本 —— 单条视频不足以给整个频道定性,
# 因此双阈值:该分类至少 3 条、且至少占该人已采样视频的 30%。
CATEGORY_MIN_COUNT = 3
CATEGORY_MIN_SHARE = 0.3

CATEGORY_LABELS_ZH: dict[str, str] = {
    "1": "电影与动画", "2": "汽车", "10": "音乐", "15": "宠物与动物", "17": "体育",
    "19": "旅行与活动", "20": "游戏", "22": "人物与博客", "23": "喜剧", "24": "娱乐",
    "25": "新闻与政治", "26": "生活妙招与风格", "27": "教育", "28": "科学与技术",
}

#: 只映射语义直给的分类;「教育」「娱乐」等泛分类刻意留空(见模块文档)。
CATEGORY_VERTICALS: dict[str, tuple[str, ...]] = {
    "1": ("video_creation",),
    "19": ("lifestyle",),
    "22": ("vlog",),
    "26": ("lifestyle",),
    "28": ("technology",),
}


# ── 品牌标记路 ────────────────────────────────────────────────────────────
#
# 口径(写死在这里,免得日后越claim越多):**品牌标记只回答「他做器材内容」,不回答
# 「他做评测」**。所以它只喂 camera_system(器材/相机系统)与 video_creation(影视制作
# 器材与工具),绝不直接把人判成「镜头评测」或「器材对比」—— 那两类要靠词表里的
# 评测 / 对比词自己出面。
#: (品类中文, 归到哪些垂类, 品牌词)。品牌词按长优先排,免得 "sony" 先吃掉 "sonycine"。
BRAND_GROUPS: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("镜头品牌", ("camera_system",), (
        "viltrox", "sigmaphoto", "sigma", "tamron", "samyang", "rokinon", "7artisans",
        "ttartisan", "thypoch", "brightin", "meike", "laowa", "venuslens", "voigtlander",
        "zeiss", "sirui",
    )),
    ("相机品牌", ("camera_system",), (
        "sonyalpha", "sony", "canon", "nikon", "fujifilm", "lumix", "panasonic", "leica",
        "hasselblad", "olympus", "omsystem", "pentax", "ricoh", "kodak",
    )),
    ("灯光器材品牌", ("camera_system",), (
        "godox", "nanlite", "aputure", "westcott", "profoto", "neewer", "cheetahstand",
    )),
    ("滤镜品牌", ("camera_system",), ("nisi",)),
    ("影视器材品牌", ("video_creation",), (
        "sonycine", "smallrig", "tilta", "zhiyun", "hollyland", "atomos", "blackmagic",
        "freewell", "pgytech",
    )),
    ("影像设备品牌", ("video_creation",), ("insta360", "dji", "osmo", "gopro")),
    ("剪辑与调色工具", ("video_creation",), ("davinci", "dehancer")),
    ("影视配乐库", ("video_creation",), ("epidemicsound", "artlist")),
)

BRAND_VERTICALS: tuple[tuple[str, tuple[str, ...], str], ...] = tuple(
    sorted(
        ((token, verticals, kind) for kind, verticals, tokens in BRAND_GROUPS for token in tokens),
        key=lambda item: (-len(item[0]), item[0]),
    )
)
