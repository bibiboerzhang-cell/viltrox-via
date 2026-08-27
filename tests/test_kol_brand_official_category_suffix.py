"""品牌官号漏网补强:品牌词 + 品类后缀(2026-08-27 Samyang 官方号穿闸案)。

取证(真数据,非杜撰):YouTube Data API channels.list 实拉
``UCJUhH8nOoZ26flxew8RJBPA`` —— customUrl ``@samyanglens``、名字
「Samyang Lens 엘케이삼양」、48,600 订阅、KR;证据视频标题
「AF 135mm F1.8 FE | The Ultimate Portrait Prime.」。它穿过了建档闸、发现墙的品牌官号
闸与全部 8 道严格闸,直接进达人池。同批漏网的还有 TamronVids(Tamron 官方视频号,
bio 是产品文案)。

三条旧判据为什么全落空,是这批断言存在的理由:
- 建档闸只认「品牌词 + 官方/地区后缀」,余段 ``lens`` 不在表里;
- 词表路要 bio 企业口吻 —— 它的 bio 是 LK 集团愿景,强/弱口吻词一个都不含;
- 动态路要官号形态(official/global/驼峰/全大写)—— 它一个都没有;
- exact-handle 快路要 URL 里带公开 handle —— 发现 item 给的是 /channel/UC...(按设计 fail-open)。

防误杀是本刀的第一优先,所以下半段全是**必须放行**的真样本(来自同一次实验的
209 个候选全量回归,新规则只新增判了 TamronVids 一个)。
"""
from __future__ import annotations

from app.domains.kol import brand_official_gate, discovery_filters
from app.domains.kol.brand_official_gate import discovery_wall_verdict

# 真数据(YouTube API 实拉 / 本次实验实际返回),不是编的。
SAMYANG_OFFICIAL = {
    "platform": "youtube",
    "handle": "samyanglens",
    "channel_name": "Samyang Lens 엘케이삼양",
    "channel_url": "https://www.youtube.com/channel/UCJUhH8nOoZ26flxew8RJBPA",
    "followers": 48600,
    "sample_title": "AF 135mm F1.8 FE | The Ultimate Portrait Prime.",
    "bio": (
        "LK SAMYANG is committed to becoming a global solution partner, creating a future "
        "beyond what we see. We aim for a global solution partner based on the glass mold "
        "material technology accumulated over 50 years."
    ),
}
TAMRON_OFFICIAL = {
    "platform": "youtube",
    "handle": "tamronvids",
    "channel_name": "TamronVids",
    "channel_url": "https://www.youtube.com/channel/UCtamronvids",
    "followers": 30000,
    "bio": (
        "Tamron lenses deliver a superb fusion of performance and value, enabling today's "
        "mirrorless cameras to perform at their peak."
    ),
}
# ↓ 必须放行的真人/真店(本次实验里真实出现过的行)
MUST_PASS = [
    {   # 真达人:名字以 lenses 结尾,但 altbuzz 不是品牌词 → 不许被吃
        "platform": "youtube", "handle": "altbuzzlenses", "channel_name": "Alt Buzz Lenses",
        "channel_url": "https://www.youtube.com/channel/UCaltbuzz", "followers": 3860,
        "bio": "Welcome to Alt Buzz Lenses, a YouTube channel about camera lenses, vintage lenses.",
    },
    {   # 二手器材商:keh 不是品牌词 → 本闸不管它(店铺是另一道闸的事)
        "platform": "youtube", "handle": "kehcamera", "channel_name": "KEH Camera",
        "channel_url": "https://www.youtube.com/channel/UCkeh", "followers": 16200,
        "bio": "We're KEH, the original camera reseller since 1979.",
    },
    {   # 真人 Sonya:品牌词只在名字开头的一部分,余段 a_official 不是纯后缀
        "platform": "instagram", "handle": "sonya_official", "channel_name": "Sonya",
        "bio": "I am a portrait photographer based in Berlin.",
    },
    {   # 资讯号:rumors 不在后缀表里
        "platform": "youtube", "handle": "sonyalpharumors", "channel_name": "Sony Alpha Rumors",
        "channel_url": "https://www.youtube.com/channel/UCrumors", "bio": "Sony rumor site.",
    },
    {   # 真人评测者:证据标题里提到 Samyang,但身份字段跟品牌无关
        "platform": "youtube", "handle": "sebastiandylag", "channel_name": "Sebastian Dylag",
        "channel_url": "https://www.youtube.com/channel/UCseb", "followers": 13000,
        "sample_title": "Samyang 85mm f1.4 - Sony FE - Review",
    },
    {   # 大号真人:与品牌词毫无关系
        "platform": "youtube", "handle": "jessicawhitaker", "channel_name": "Jessica Whitaker",
        "channel_url": "https://www.youtube.com/channel/UCjw", "followers": 221000,
        "bio": "Portrait photographer and educator.",
    },
]


def test_enrollment_gate_now_catches_brand_plus_category_handles() -> None:
    for row in (SAMYANG_OFFICIAL, TAMRON_OFFICIAL):
        verdict = brand_official_gate.brand_official_match(
            handle=row["handle"], display_name=row["channel_name"], platform=row["platform"],
        )
        assert verdict.get("reason") == brand_official_gate.BRAND_OFFICIAL_SKIP_REASON, row["handle"]
    # 韩文被归一掉之后,display_name 也整只命中 —— 两个身份字段任一即可。
    assert brand_official_gate.brand_official_match(
        handle="", display_name="Samyang Lens 엘케이삼양", platform="youtube",
    ).get("brand") == "samyang"


def test_discovery_wall_now_drops_them_via_identity_form() -> None:
    """发现墙必须自己判出来 —— 建档闸在它下游,漏在这里就已经上了新发现墙。"""
    assert discovery_wall_verdict(SAMYANG_OFFICIAL) == "identity_form"
    assert discovery_wall_verdict(TAMRON_OFFICIAL) == "identity_form"


def test_enrollment_hard_gate_keeps_its_documented_verdicts() -> None:
    """身份形态只装发现墙:建档硬闸(无显式放行口)的判词一个字都不许变 ——
    否则 allow_brand_official=True 的显式建档会被连坐拒掉。"""
    for row in (SAMYANG_OFFICIAL, TAMRON_OFFICIAL):
        assert discovery_filters._brand_official_verdict(row) == ""
    assert discovery_filters.discovery_account_gate_verdict(
        {"platform": "instagram", "handle": "godox_global", "display_name": "Godox Global"},
    ) == ""


def test_real_creators_and_shops_are_not_eaten() -> None:
    for row in MUST_PASS:
        assert not brand_official_gate.is_brand_official_row(row), row["handle"]
        assert discovery_wall_verdict(row) == "", row["handle"]


def test_suffix_pool_deliberately_excludes_creator_words() -> None:
    """photo / photography / studio / films 是真人常用词,收进来就会误吃达人。"""
    pool = brand_official_gate.official_suffix_tokens()
    assert {"lens", "lenses", "optics", "optical", "vids", "cameras"} <= pool
    # cine 刻意不收:sirui.cine 的「刻意放行」是 profile_basics 文档化过的口径,
    # 改它属于另一次决策,不搭本刀的车。
    assert not ({"photo", "photography", "studio", "films", "vlog"} & pool)


def test_brand_plus_suffix_handle_requires_the_brand_to_lead() -> None:
    assert brand_official_gate.brand_plus_suffix_handle("samyanglens", "samyang")
    assert brand_official_gate.brand_plus_suffix_handle("samyanglensusa", "samyang")
    assert brand_official_gate.brand_plus_suffix_handle("sony", "sony")
    # 品牌词在中间/结尾一律放行(altbuzzlenses 的 lenses 前面不是品牌词)。
    assert not brand_official_gate.brand_plus_suffix_handle("altbuzzlenses", "samyang")
    assert not brand_official_gate.brand_plus_suffix_handle("mysonylensreviews", "sony")
    # 余段不是纯后缀词就不算(sonyalpharumors 的 rumors 不在表里)。
    assert not brand_official_gate.brand_plus_suffix_handle("sonyalpharumors", "sony")


def test_gate_switch_still_turns_everything_off(monkeypatch) -> None:
    monkeypatch.setenv("VKPI_BRAND_OFFICIAL_GATE", "0")
    assert brand_official_gate.brand_official_match(
        handle="samyanglens", display_name="Samyang Lens", platform="youtube",
    ) == {}
