"""受众画像 ensemble_v1 —— 三平台评论者抽样 -> 推断 -> 聚合 -> audience_estimated_json。

像 Modash / NanoInfluencer 的 Audience Stats:性别环 + Top countries + 语言 + 年龄桶 + 评论情报,
带样本量/覆盖率/置信度,前端标 BETA。数据链路:
  - YouTube:Data API(免费额度)抽 commentThreads 评论者 -> channels.list 批量拿
    自报 country + 白捡字段(bio/订阅数/视频数/频道创建时间,同批 API 零额外配额)。
  - Instagram / TikTok:复用库里已抓的 vkpi_comments(不新写抓取);评论不足则入队抓评论。
国家三层信号(逐层降置信):自报 .9 > 人名词表国籍猜 .4 > 评论语言推市场 .3;
性别人名表 conf .8(未知留空),发布口径另出 gender_normalized(按已判定样本外推 100)。
年龄三路融合(v2,BETA):A=Gemini 批推(llm_gateway,批 50/调用,预算记账+代理)
> B=M3(可选依赖,装了就用文本模式;安装法:.venv/bin/pip install m3inference,含 torch,默认不装)
> C=频道注册年龄弱先验(注册越早年龄下界越高,conf .3)。按 conf 加权投票融合。
聚合后做经验贝叶斯收缩:prior=同垂类已有 audience_estimated 的均值,tau=50,无先验跳过。
评论情报(comment_intel,纯词表/直方零成本)由 app.domains.kol.comment_intel 提供,并入同一 JSON。
身份推断结果落 vkpi_commenter_profiles 缓存(迁移 205/206),同评论者跨 KOL 复用。

红线:绝不写 viltrox_fit_score、不碰 rule_v0(LLM 走 llm_gateway,rule_v0 兜底文本不当真);
全部估算值明示 method/置信度,不冒充官方数据。
网络:本地网络到 googleapis / LLM 需代理 —— 读 env HTTPS_PROXY(runtime_env.sh 会从
YTDLP_PROXY 导出),没配则在错误里提示。
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from app.core.logging import get_logger
from app.domains.kol.audience_language import LANG_TO_MARKETS, detect_lang

logger = get_logger(__name__)

METHOD = "ensemble_v1"
_YT_API_BASE = "https://www.googleapis.com/youtube/v3"
SHRINK_TAU = 50.0
MIN_LOCAL_COMMENTS = 30  # IG/TT 本地评论少于此数 -> 先入队抓评论,不出低质画像
AGE_MIN_DETERMINED = 5  # 年龄分布最小判定人数:低于此不出 bins(防 1 人外推 100%)

# ── 年龄三路融合(v2)──
AGE_BUCKETS = ("0-18", "19-29", "30-39", "40+")
AGE_LLM_BATCH_SIZE = 50   # A 路(Gemini)每次调用批 50 评论者
AGE_LLM_MAX_BATCHES = 4   # 单次刷新 A 路调用上限(成本闸;其余人走 C 路弱先验,下次刷新继续补)
CREATOR_DENSITY_MIN_SUBS = 1000  # 受众创作者浓度口径:订阅数超过 1000

# ── 人名词表(轻量,替代 m3inference 等重依赖;P0 只做西文名,未知留空不猜)──
# 性别表:男/女各约 500 常见名(英语圈 + 欧洲 + 拉美 + 中东/南亚罗马化高频名)。
_MALE_NAMES: frozenset[str] = frozenset("""
james john robert michael william david richard joseph thomas charles christopher daniel matthew anthony mark donald
steven paul andrew joshua kenneth kevin brian george timothy ronald edward jason jeffrey ryan jacob gary nicholas eric
jonathan stephen larry justin scott brandon benjamin samuel gregory alexander patrick frank raymond jack dennis jerry
tyler aaron jose adam nathan henry douglas zachary peter kyle noah ethan jeremy walter christian keith roger terry
austin sean gerald carl harold dylan arthur lawrence jordan jesse bryan billy bruce gabriel joe logan alan juan albert
willie elijah wayne randy vincent mason roy ralph bobby russell bradley philip eugene louis cody caleb luke ricardo
harry howard fred lucas jesus marcus antonio dale danny victor johnny ivan phillip clarence ernest martin craig stanley
leonard nathaniel manuel rodney curtis norman marvin allen glenn jeffery travis jakob leo tim tom mike bill chris dan
matt tony steve dave rick jim ken kev bri geo ed jeff jay nick jon steph gregg alex pat frankie ray jacky den jer
oliver liam aiden jackson carter grayson wyatt julian levi isaac oscar felix theodore sebastian owen connor cameron
hunter adrian landon santiago mateo diego alejandro andres fernando javier miguel rafael pablo pedro sergio carlos
eduardo enrique francisco gerardo guillermo gustavo hector ignacio jaime jorge lorenzo luis marco mario martin_ mauricio
nicolas octavio omar_ raul roberto rodrigo ruben salvador tomas cesar cristian emilio esteban felipe gonzalo hernan
joaquin leandro marcelo matias maximiliano ramiro santino thiago valentino agustin bautista benicio bruno ciro dante
facundo franco genaro lautaro nahuel simon giovanni giuseppe alessandro andrea_it lorenzo_it matteo francesco leonardo
riccardo tommaso davide federico gabriele filippo edoardo antonio_it marco_it luca paolo stefano roberto_it angelo
salvatore vincenzo domenico carmine enzo fabio dario massimo maurizio pierluigi hans klaus jurgen dieter wolfgang
werner helmut manfred uwe bernd rainer horst gunter heinz gerhard rolf lothar norbert friedrich wilhelm heinrich karl
otto fritz ludwig sebastian_de florian tobias lukas jonas finn niklas jannik moritz till lennard hendrik soren pierre
jean michel philippe alain bernard christophe pascal laurent olivier thierry francois didier frederic gerard herve
jacques marcel maurice rene serge yves antoine baptiste clement damien etienne fabien gaspard hugo julien leon mathis
maxime nathan_fr quentin raphael remy romain theo timothe valentin xavier joao pedro_pt tiago goncalo duarte afonso
rodrigo_pt vasco nuno rui paulo sergio_pt fabio_pt andre bruno_pt diogo francisco_pt gustavo_pt henrique leandro_pt
marcos mateus rafael_pt renato ricardo_pt thiago_pt vinicius wesley wellington cauã kaique luiz otavio caio danilo
ahmed mohammed mohamed ali omar hassan hussein ibrahim khalid mahmoud mustafa youssef abdullah amr karim tarek waleed
sami rami ziad fadi nabil bassam adel jamal faisal saad salman sultan turki bandar majed nawaf rakan yazan hamza bilal
usman imran asif rashid javed tariq zafar akram shahid saleem naveed kamran adnan farhan kashif rizwan salman_pk umar
zain arjun rahul rohit amit vijay sanjay rajesh suresh ramesh anil sunil ajay vikram deepak manoj ravi krishna arun
ashok prakash ganesh dinesh mahesh naresh rakesh mukesh santosh venkatesh siddharth aditya abhishek akshay ankit gaurav
harsh karan kunal nikhil pranav rishabh sachin varun vivek yash dev raj mehmet mustafa_tr ahmet ali_tr huseyin hasan
ibrahim_tr osman yusuf murat omer_tr ramazan halil suleyman abdullah_tr salih kemal emre burak baris cem deniz efe eren
kaan mert onur ozan serkan tolga umut volkan yigit ivan dmitry sergey andrey alexey mikhail nikolai vladimir alexandr
pavel maxim anton artem denis egor igor kirill konstantin oleg roman ruslan stanislav timur vadim valentin_ru viktor
vitaly yaroslav boris gennady grigory leonid pyotr semyon stepan budi agus bambang joko slamet sutrisno hendra andi
dedi eko dwi rizky fajar bayu putra aditya_id wahyu yoga arif hadi irfan surya piotr pawel krzysztof andrzej stanislaw
tomasz marcin marek michal grzegorz jozef adam_pl lukasz mateusz kamil jakub szymon wojciech zbigniew tadeusz kazimierz
henryk daan sem finn_nl milan luuk lucas_nl levi_nl jesse_nl thijs bram ruben_nl tim_nl niels sander joost wouter
jeroen maarten bas gijs floris lars erik anders bjorn gunnar hakan johan karl_se magnus mats mikael nils olof per
stefan sven ulf jesper kasper mads rasmus soren_dk emil oskar viktor_se axel hugo_se elias vidar sindre eirik havard
kjetil odd tore terje geir arne knut olav dimitris giorgos kostas nikos panos spiros stavros thanasis vasilis yannis
hiroshi takeshi kenji taro jiro ichiro satoshi kazuo makoto osamu shigeru tadashi yoshio akira daisuke haruto kaito
kenta kota naoki ren riku shota sota takumi yamato yuki_m yuto minjun seojun dohyun jihoo hajoon siwoo juwon minho
jinwoo sungmin taeyang hyunwoo nguyen minh duc quang huy khoa long nam phong son thanh trung tuan viet dung hieu
""".split())

_FEMALE_NAMES: frozenset[str] = frozenset("""
mary patricia jennifer linda elizabeth barbara susan jessica sarah karen lisa nancy betty sandra margaret ashley
kimberly emily donna michelle carol amanda melissa deborah stephanie rebecca sharon laura cynthia dorothy amy kathleen
angela shirley emma brenda pamela nicole anna samantha katherine christine debra rachel carolyn janet maria catherine
heather diane olivia julie joyce victoria ruth virginia lauren kelly christina joan evelyn judith andrea hannah megan
cheryl jacqueline martha madison teresa gloria sara janice ann kathryn abigail sophia frances jean alice judy isabella
julia grace amber denise danielle marilyn beverly charlotte natalie theresa diana brittany doris kayla alexis lori
tiffany kathy tammy crystal erin natasha wendy vanessa peggy monica erika elena colleen allison suzanne bonnie
gabriella heidi vivian carla dana marie rosa yvonne kristen leah renee valerie michele sally molly dawn courtney faith
holly jo joanna lydia meredith miranda nina paula robin rose stella tracy zoe ava mia luna layla nora hazel violet
aurora savannah audrey brooklyn bella claire skylar lucy paisley everly anais camila valentina ximena regina renata
fernanda daniela gabriela alejandra andrea_es adriana beatriz carmen carolina catalina cecilia clara claudia cristina
dolores elisa esperanza estela eva florencia francisca guadalupe ines irene isabel josefina juana julieta laura_es
leticia lourdes lucia luisa magdalena manuela marcela margarita mariana marisol marta mercedes milagros monica_es
natalia noelia olga patricia_es paula_es pilar raquel rocio rosario silvia sofia soledad susana teresa_es veronica
yolanda antonella bianca chiara francesca giulia alessia martina_it giorgia aurora_it beatrice camilla eleonora elisa_it
federica gaia ginevra greta ilaria lucrezia maddalena noemi rebecca_it serena silvia_it valentina_it vittoria
alessandra angela_it carla_it daniela_it donatella emanuela fabiana laura_it loredana manuela_it monica_it paola
roberta rosanna simona stefania hannah_de anna_de lena leonie johanna katharina franziska charlotte_de amelie clara_de
emilia frieda greta_de helene ida lotte luise marlene mathilda nele paula_de sophie theresa_de wilhelmine annika birgit
claudia_de gabi heike ingrid karin monika petra renate sabine silke ursula ute camille chloe lea manon ines_fr jade
louise alice_fr lina eva_fr romane anais_fr juliette margaux mathilde oceane pauline salome solene amandine aurelie
celine delphine elodie emmanuelle isabelle laetitia marion nathalie sandrine severine sophie_fr sylvie valerie_fr
veronique ana_pt beatriz_pt carolina_pt catarina constanca diana_pt francisca_pt ines_pt joana leonor madalena
margarida mariana_pt matilde rita sofia_pt teresa_pt vera larissa leticia_br luana luiza manuela_br rafaela tais
thaissa yasmin camily evelyn_br giovanna isadora julia_br lavinia livia lorena maite marcela_br milena nathalia
pietra rebeca stefany valentina_br vitoria fatima aisha khadija zainab maryam layla_ar noor huda salma dina rania
hanan samira nadia leila yasmine amira farida hala iman jamila karima latifa lubna maha mona najwa rasha ruba sahar
salwa wafa zahra aya malak jana lina_ar sara_ar shahd tala nour priya pooja neha anjali kavita sunita anita rekha
seema shweta swati deepika divya kirti lakshmi meena nisha pallavi rani ritu sapna shilpa sneha sonali usha vandana
aishwarya ananya diya ishita khushi kritika mansi navya prachi riya sakshi shreya siya tanvi vaishnavi zara ayse fatma
emine hatice zeynep elif meryem ozlem sevgi gul derya ebru esra gamze gizem hande merve nur pinar seda selin sibel
tugba yasemin olga_ru elena_ru natalya irina svetlana tatyana ekaterina anastasia maria_ru anna_ru yulia oksana
lyudmila galina valentina_ru vera_ru nadezhda alina alla dasha diana_ru dina_ru inna karina kira ksenia lara lidia
marina milana nika polina regina_ru sofya taisia ulyana varvara veronika viktoria yana zlata siti sri dewi rina yanti
wati lestari indah fitri ayu putri sari ratna dian eka maya intan agnieszka barbara_pl beata dorota elzbieta ewa
grazyna halina irena jadwiga janina jolanta katarzyna krystyna magdalena_pl malgorzata maria_pl marta_pl monika_pl
renata_pl teresa_pl urszula wanda zofia aleksandra alicja amelia_pl hanna_pl julia_pl lena_pl liliana maja natalia_pl
nikola oliwia pola wiktoria zuzanna emma_nl julia_nl mila tess sophie_nl zoe_nl anna_nl eva_nl lieke lotte_nl noa roos
sanne fleur femke iris maud nienke astrid ingrid_no linnea maja_se alva ebba elsa freja saga signe stina tuva wilma
agnes alma clara_se ellen hedda ida_se ines_se lea_se nora_no selma thea tiril vilde amalie andrea_no frida ingeborg
kari marit oda sigrid solveig eleni katerina sofia_gr dimitra georgia ioanna konstantina vasiliki despina evangelia
yuki_f sakura hana akari himari ichika mei mio rin yua yui aoi ema honoka koharu mitsuki riko saki tsumugi yuna seoyeon
jiwoo minseo chaewon dain hayoon jia seoah sua yeonwoo yerin hyejin sunhee younghee mihyun linh huong lan mai phuong
thao trang van anh_vn chi ha hanh hoa hong hue ngoc nga nhung oanh quynh thanh_f thu thuy
""".split())

# 名 -> 国家猜测(仅高辨识度名,~200 条;英语名不进表 —— 英语圈国家分不开,宁缺毋滥)。
_NAME_COUNTRY: dict[str, str] = {
    # 西语(西班牙/拉美,取 ES 作代表市场;拉美细分交给自报/语言层)
    "juan": "ES", "jose": "ES", "carlos": "ES", "miguel": "ES", "javier": "ES", "diego": "ES",
    "alejandro": "ES", "andres": "ES", "fernando": "ES", "sergio": "ES", "pablo": "ES", "pedro": "ES",
    "guadalupe": "MX", "ximena": "MX", "santiago": "MX", "mateo": "MX", "fernanda": "MX", "citlali": "MX",
    "maria": "ES", "carmen": "ES", "lucia": "ES", "pilar": "ES", "rocio": "ES", "marisol": "ES",
    # 葡语(巴西为主)
    "joao": "BR", "thiago": "BR", "tiago": "PT", "goncalo": "PT", "duarte": "PT", "vinicius": "BR",
    "wellington": "BR", "wesley": "BR", "mateus": "BR", "caua": "BR", "kaique": "BR", "larissa": "BR",
    "luana": "BR", "yasmin": "BR", "giovanna": "BR", "vitoria": "BR", "catarina": "PT", "madalena": "PT",
    # 意语
    "giuseppe": "IT", "giovanni": "IT", "alessandro": "IT", "matteo": "IT", "francesco": "IT",
    "lorenzo": "IT", "riccardo": "IT", "tommaso": "IT", "vincenzo": "IT", "salvatore": "IT",
    "giulia": "IT", "chiara": "IT", "francesca": "IT", "alessia": "IT", "antonella": "IT",
    # 德语
    "hans": "DE", "klaus": "DE", "jurgen": "DE", "dieter": "DE", "wolfgang": "DE", "helmut": "DE",
    "gunter": "DE", "heinz": "DE", "gerhard": "DE", "friedrich": "DE", "wilhelm": "DE", "fritz": "DE",
    "katharina": "DE", "franziska": "DE", "annika": "DE", "heike": "DE", "ursula": "DE", "silke": "DE",
    # 法语
    "pierre": "FR", "jean": "FR", "francois": "FR", "philippe": "FR", "laurent": "FR", "thierry": "FR",
    "didier": "FR", "herve": "FR", "baptiste": "FR", "quentin": "FR", "maxime": "FR", "julien": "FR",
    "amandine": "FR", "aurelie": "FR", "elodie": "FR", "oceane": "FR", "solene": "FR", "manon": "FR",
    # 阿语(取 SA 作代表市场)
    "mohammed": "SA", "mohamed": "EG", "ahmed": "EG", "mahmoud": "EG", "mostafa": "EG", "mustafa": "SA",
    "abdullah": "SA", "khalid": "SA", "faisal": "SA", "salman": "SA", "sultan": "SA", "bandar": "SA",
    "youssef": "EG", "amr": "EG", "tarek": "EG", "karim": "EG", "fatima": "SA", "aisha": "SA",
    "khadija": "SA", "zainab": "SA", "maryam": "SA", "amira": "EG", "yasmine": "EG",
    # 土耳其
    "mehmet": "TR", "ahmet": "TR", "huseyin": "TR", "osman": "TR", "murat": "TR", "ramazan": "TR",
    "suleyman": "TR", "kemal": "TR", "emre": "TR", "burak": "TR", "baris": "TR", "ayse": "TR",
    "fatma": "TR", "emine": "TR", "hatice": "TR", "zeynep": "TR", "elif": "TR", "merve": "TR",
    # 俄语
    "ivan": "RU", "dmitry": "RU", "sergey": "RU", "andrey": "RU", "alexey": "RU", "mikhail": "RU",
    "nikolai": "RU", "vladimir": "RU", "maxim": "RU", "artem": "RU", "kirill": "RU", "oleg": "RU",
    "svetlana": "RU", "tatyana": "RU", "ekaterina": "RU", "anastasia": "RU", "yulia": "RU", "oksana": "RU",
    # 印地/南亚
    "rahul": "IN", "rohit": "IN", "amit": "IN", "vijay": "IN", "sanjay": "IN", "rajesh": "IN",
    "suresh": "IN", "ramesh": "IN", "arjun": "IN", "aditya": "IN", "abhishek": "IN", "akshay": "IN",
    "priya": "IN", "pooja": "IN", "neha": "IN", "anjali": "IN", "deepika": "IN", "shreya": "IN",
    "muhammad": "PK", "usman": "PK", "imran": "PK", "kamran": "PK", "adnan": "PK", "farhan": "PK",
    # 印尼
    "budi": "ID", "agus": "ID", "bambang": "ID", "joko": "ID", "slamet": "ID", "hendra": "ID",
    "rizky": "ID", "bayu": "ID", "wahyu": "ID", "siti": "ID", "dewi": "ID", "putri": "ID",
    "lestari": "ID", "fitri": "ID", "intan": "ID",
    # 波兰
    "piotr": "PL", "pawel": "PL", "krzysztof": "PL", "andrzej": "PL", "stanislaw": "PL", "tomasz": "PL",
    "marcin": "PL", "grzegorz": "PL", "lukasz": "PL", "wojciech": "PL", "zbigniew": "PL",
    "agnieszka": "PL", "malgorzata": "PL", "katarzyna": "PL", "zofia": "PL", "oliwia": "PL",
    # 荷兰
    "daan": "NL", "sem": "NL", "luuk": "NL", "thijs": "NL", "bram": "NL", "niels": "NL", "sander": "NL",
    "joost": "NL", "wouter": "NL", "jeroen": "NL", "maarten": "NL", "gijs": "NL", "floris": "NL",
    "lieke": "NL", "sanne": "NL", "femke": "NL", "fleur": "NL", "maud": "NL", "nienke": "NL",
    # 北欧
    "bjorn": "SE", "gunnar": "SE", "magnus": "SE", "mikael": "SE", "jesper": "SE", "linnea": "SE",
    "ebba": "SE", "freja": "SE", "saga": "SE", "tuva": "SE", "rasmus": "DK", "mads": "DK",
    "kasper": "DK", "eirik": "NO", "havard": "NO", "kjetil": "NO", "sigrid": "NO", "solveig": "NO",
    # 希腊
    "dimitris": "GR", "giorgos": "GR", "kostas": "GR", "nikos": "GR", "stavros": "GR", "vasilis": "GR",
    "eleni": "GR", "katerina": "GR", "dimitra": "GR", "ioanna": "GR", "vasiliki": "GR",
    # 日/韩/越(罗马字)
    "hiroshi": "JP", "takeshi": "JP", "kenji": "JP", "satoshi": "JP", "daisuke": "JP", "haruto": "JP",
    "sakura": "JP", "yui": "JP", "honoka": "JP", "minjun": "KR", "seojun": "KR", "jihoo": "KR",
    "hyunwoo": "KR", "seoyeon": "KR", "jiwoo": "KR", "nguyen": "VN", "phuong": "VN", "huong": "VN",
}

_NAME_TOKEN_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]+")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _first_name(display_name: str) -> str:
    """展示名/handle -> 首个字母 token(小写,去数字/emoji/分隔符)。取不到返回空串。"""
    text = str(display_name or "").strip().lstrip("@")
    if not text:
        return ""
    # handle 风格 sweetheart_forever35 -> 按 _ . - 切开取首段字母
    for sep in ("_", ".", "-", " "):
        text = text.replace(sep, " ")
    match = _NAME_TOKEN_RE.search(text)
    return match.group(0).lower() if match else ""


# ── YouTube Data API 抽样层 ──

def _yt_api_key() -> str:
    for key in ("YOUTUBE_API_KEY", "GOOGLE_API_KEY", "GOOGLE_YOUTUBE_API_KEY"):
        value = (os.environ.get(key) or "").strip()
        if value:
            return value
    return ""


def _proxy_hint() -> str:
    if (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or "").strip():
        return ""
    return "本地网络到 googleapis 需代理:export HTTPS_PROXY=<YTDLP_PROXY 值>(见 scripts/runtime_env.sh)"


def _yt_get(endpoint: str, params: dict[str, Any], *, timeout: int = 20) -> dict[str, Any]:
    """GET youtube/v3 端点。urllib 默认吃 env 代理(HTTPS_PROXY)。失败 raise RuntimeError(带提示)。"""
    api_key = _yt_api_key()
    if not api_key:
        raise RuntimeError("missing YOUTUBE_API_KEY / GOOGLE_API_KEY")
    query = {k: v for k, v in params.items() if v is not None and v != ""}
    query["key"] = api_key
    url = f"{_YT_API_BASE}/{endpoint}?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "ViltroxMarketing/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310 - fixed Google API host.
            return json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8")[:400]
        except Exception:
            body = ""
        raise RuntimeError(f"youtube api http {exc.code}: {body}") from exc
    except Exception as exc:
        hint = _proxy_hint()
        raise RuntimeError(f"youtube api unreachable: {exc}" + (f" | {hint}" if hint else "")) from exc


def _resolve_channel_id(channel_id_or_handle: str) -> str:
    """UC 开头当 channel_id 直用;否则按 handle 走 channels.list(forHandle)。解析不到返回空串。"""
    raw = str(channel_id_or_handle or "").strip()
    if not raw:
        return ""
    if raw.startswith("UC") and len(raw) >= 20 and "/" not in raw:
        return raw
    # 从 URL 里抠 /channel/UC…
    match = re.search(r"/channel/(UC[0-9A-Za-z_-]{10,})", raw)
    if match:
        return match.group(1)
    handle = raw
    handle_match = re.search(r"@([^/?#\s]+)", raw)
    if handle_match:
        handle = handle_match.group(1)
    payload = _yt_get("channels", {"part": "id", "forHandle": handle.strip("@")})
    items = payload.get("items") or []
    if items and isinstance(items[0], dict):
        return str(items[0].get("id") or "")
    return ""


def sample_youtube_commenters(channel_id_or_handle: str, max_comments: int = 400) -> dict[str, Any]:
    """YouTube 免费 Data API 抽评论者:commentThreads(全频道) -> channels.list 批量补档案。

    v2 白捡字段:同一批 channels.list 换 part=snippet,statistics,零额外配额顺手带回
    bio(description)/channel_created_at(publishedAt)/subscriber_count/video_count。
    同时带回原始评论列表 comments(text/created_at/like_count/video_key,给 comment_intel 用)。
    返回 {status, channel_id, commenters:[...], comments:[...], comments_scanned, reply_total, api_calls}。
    网络/配置失败诚实返回 {status:..., reason:...}(不 raise)。
    """
    started = time.time()
    if not _yt_api_key():
        return {"status": "not_configured", "reason": "missing YOUTUBE_API_KEY / GOOGLE_API_KEY"}
    api_calls = 0
    try:
        channel_id = _resolve_channel_id(channel_id_or_handle)
        api_calls += 1
    except RuntimeError as exc:
        return {"status": "network_error", "reason": str(exc)[:400]}
    if not channel_id:
        return {"status": "channel_not_found", "reason": f"cannot resolve channel from {channel_id_or_handle!r}"}

    by_author: dict[str, dict[str, Any]] = {}
    comments: list[dict[str, Any]] = []
    comments_scanned = 0
    reply_total = 0
    page_token = ""
    try:
        while comments_scanned < int(max_comments or 400):
            payload = _yt_get(
                "commentThreads",
                {
                    "part": "snippet",
                    "allThreadsRelatedToChannelId": channel_id,
                    "textFormat": "plainText",
                    "maxResults": min(100, int(max_comments) - comments_scanned),
                    "pageToken": page_token or None,
                },
            )
            api_calls += 1
            items = payload.get("items") or []
            for thread in items:
                thread_snippet = (thread or {}).get("snippet") or {}
                snippet = (thread_snippet.get("topLevelComment") or {}).get("snippet", {})
                if not isinstance(snippet, dict):
                    continue
                comments_scanned += 1
                reply_total += int(thread_snippet.get("totalReplyCount") or 0)
                text = str(snippet.get("textOriginal") or snippet.get("textDisplay") or "")
                display_name = str(snippet.get("authorDisplayName") or "").strip()
                comments.append(
                    {
                        "text": text[:500],
                        "author": display_name,
                        "created_at": str(snippet.get("publishedAt") or ""),
                        "like_count": int(snippet.get("likeCount") or 0),
                        "is_reply": False,
                        "video_key": str(snippet.get("videoId") or thread_snippet.get("videoId") or ""),
                    }
                )
                author_id = str(((snippet.get("authorChannelId") or {}).get("value")) or "").strip()
                if not author_id:
                    continue
                entry = by_author.setdefault(
                    author_id,
                    {
                        "platform": "youtube",
                        "author_key": author_id,
                        "display_name": display_name,
                        "comment_text": "",
                        "declared_country": "",
                        "bio": "",
                        "channel_created_at": "",
                        "subscriber_count": None,
                        "video_count": None,
                    },
                )
                # 同一评论者最多攒 3 条评论合喂(2026-07-02:单条判不出年龄,多条口吻/话题互证)。
                if text and str(entry["comment_text"]).count(" || ") < 2:
                    entry["comment_text"] = (
                        f"{entry['comment_text']} || {text[:160]}" if entry["comment_text"] else text[:200]
                    )[:500]
            page_token = str(payload.get("nextPageToken") or "")
            if not page_token or not items:
                break
    except RuntimeError as exc:
        if not by_author:
            return {"status": "network_error", "reason": str(exc)[:400]}
        logger.warning("audience_stats yt comment paging partial: %s", exc)

    # 批量 channels.list(part=snippet,statistics, 50/批):自报 country(强信号 .9)+ 白捡档案字段。
    author_ids = list(by_author.keys())
    declared_hits = 0
    for index in range(0, len(author_ids), 50):
        chunk = author_ids[index : index + 50]
        try:
            payload = _yt_get(
                "channels", {"part": "snippet,statistics", "id": ",".join(chunk), "maxResults": 50}
            )
            api_calls += 1
        except RuntimeError as exc:
            logger.warning("audience_stats yt channels.list batch failed: %s", exc)
            continue
        for item in payload.get("items") or []:
            if not isinstance(item, dict):
                continue
            cid = str(item.get("id") or "")
            if cid not in by_author:
                continue
            snippet = item.get("snippet") or {}
            stats = item.get("statistics") or {}
            entry = by_author[cid]
            country = str(snippet.get("country") or "").strip().upper()
            if country:
                entry["declared_country"] = country
                declared_hits += 1
            entry["bio"] = str(snippet.get("description") or "").strip()[:500]
            entry["channel_created_at"] = str(snippet.get("publishedAt") or "").strip()
            try:
                if not stats.get("hiddenSubscriberCount"):
                    entry["subscriber_count"] = int(stats.get("subscriberCount"))
            except (TypeError, ValueError):
                pass
            try:
                entry["video_count"] = int(stats.get("videoCount"))
            except (TypeError, ValueError):
                pass
    return {
        "status": "ok",
        "channel_id": channel_id,
        "commenters": list(by_author.values()),
        "comments": comments,
        "comments_scanned": comments_scanned,
        "reply_total": reply_total,
        "declared_country_hits": declared_hits,
        "api_calls": api_calls,
        "elapsed_sec": round(time.time() - started, 2),
    }


# ── IG / TikTok:复用库里已抓评论(不新写抓取)──

def sample_local_commenters(kol_pool_id: int, *, conn: Any = None, limit: int = 800) -> dict[str, Any]:
    """从 vkpi_comments 读该 KOL 的评论者(author_handle + comment_text)。

    两条桥(与 audience_language_for_kol 同款):account_id=kol_pool_id;
    或 post_table 属 evidence 口径 + post_id 落在该 KOL 的 video evidence 上
    (历史写入既有 'evidence' 也有 'vkpi_kol_video_evidence',两个都认)。
    """
    from app.db.connection import get_conn

    db = conn or get_conn()
    rows = db.execute(
        "SELECT author_handle, author_id, raw_data_json, comment_text FROM vkpi_comments WHERE account_id=? LIMIT ?",
        (int(kol_pool_id), int(limit)),
    ).fetchall()
    if not rows:
        ev = db.execute(
            "SELECT id FROM vkpi_kol_video_evidence WHERE kol_pool_id=? LIMIT 100",
            (int(kol_pool_id),),
        ).fetchall()
        eids = [int(dict(e)["id"]) for e in ev]
        if eids:
            placeholders = ",".join(["?"] * len(eids))
            rows = db.execute(
                "SELECT author_handle, author_id, raw_data_json, comment_text FROM vkpi_comments "
                f"WHERE post_table IN ('evidence','vkpi_kol_video_evidence') AND post_id IN ({placeholders}) LIMIT ?",
                (*eids, int(limit)),
            ).fetchall()
    by_author: dict[str, dict[str, Any]] = {}
    comments_scanned = 0
    for r in rows:
        rec = dict(r)
        comments_scanned += 1
        handle = str(rec.get("author_handle") or "").strip()
        if not handle:
            # 救援链:部分抓取批次 author_handle 为空(TikTok 批次作者在 raw 的 uniqueId,
            # 或仅有 author_id)。逐级兜底,救不出才跳过 —— 治 no_commenters 假空。
            handle = str(rec.get("author_id") or "").strip()
        if not handle:
            try:
                import json as _rj

                _raw = rec.get("raw_data_json")
                _rd = _rj.loads(_raw) if isinstance(_raw, str) else (_raw or {})
                if isinstance(_rd, dict):
                    handle = str(
                        _rd.get("uniqueId") or _rd.get("username") or _rd.get("ownerUsername") or _rd.get("uid") or ""
                    ).strip()
            except Exception:
                handle = ""
        if not handle:
            continue
        author_key = handle.lower()
        entry = by_author.setdefault(
            author_key,
            {
                "platform": "",  # 由 refresh 按 pool 行平台补
                "author_key": author_key,
                "display_name": handle,
                "comment_text": "",
                "declared_country": "",
            },
        )
        _txt = str(rec.get("comment_text") or "").strip()
        # 同一评论者最多攒 3 条评论合喂(多条口吻/话题互证,提年龄判定率)。
        if _txt and str(entry["comment_text"]).count(" || ") < 2:
            entry["comment_text"] = (
                f"{entry['comment_text']} || {_txt[:160]}" if entry["comment_text"] else _txt[:200]
            )[:500]
    return {
        "status": "ok",
        "commenters": list(by_author.values()),
        "comments_scanned": comments_scanned,
        "source": "vkpi_comments",
    }


# ── 推断层 ──

def infer_commenter(profile: dict[str, Any]) -> dict[str, Any]:
    """单评论者三层推断:国家=自报 .9 > 人名词表 .4 > 评论语言推市场 .3;性别=人名表 .8(未知留空)。"""
    display_name = str(profile.get("display_name") or "")
    comment_text = str(profile.get("comment_text") or "")
    declared = str(profile.get("declared_country") or "").strip().upper()
    language = detect_lang(comment_text) if comment_text else "und"
    first = _first_name(display_name)

    country, country_source, country_conf = "", "", 0.0
    if declared:
        country, country_source, country_conf = declared, "declared", 0.9
    elif first and first in _NAME_COUNTRY:
        country, country_source, country_conf = _NAME_COUNTRY[first], "name", 0.4
    elif language != "und" and LANG_TO_MARKETS.get(language):
        country, country_source, country_conf = LANG_TO_MARKETS[language][0], "language", 0.3

    gender, gender_conf = "", 0.0
    if first and first in _MALE_NAMES and first not in _FEMALE_NAMES:
        gender, gender_conf = "male", 0.8
    elif first and first in _FEMALE_NAMES and first not in _MALE_NAMES:
        gender, gender_conf = "female", 0.8

    return {
        "platform": str(profile.get("platform") or ""),
        "author_key": str(profile.get("author_key") or ""),
        "display_name": display_name[:200],
        "country": country,
        "country_source": country_source,
        "country_conf": round(country_conf, 2),
        "gender": gender,
        "gender_conf": round(gender_conf, 2),
        "language": language if language != "und" else "",
        # v2 白捡字段直通(缺省 None/空,upsert 用 COALESCE 保旧值)
        "bio": str(profile.get("bio") or "")[:500],
        "channel_created_at": str(profile.get("channel_created_at") or ""),
        "subscriber_count": profile.get("subscriber_count"),
        "video_count": profile.get("video_count"),
        "age_bucket": "",
        "age_conf": None,
    }


def _load_cached_profiles(conn: Any, platform: str, author_keys: list[str]) -> dict[str, dict[str, Any]]:
    if not author_keys:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for index in range(0, len(author_keys), 200):
        chunk = author_keys[index : index + 200]
        placeholders = ",".join(["?"] * len(chunk))
        rows = conn.execute(
            "SELECT platform, author_key, display_name, country, country_source, country_conf, gender, gender_conf, language, "
            "age_bucket, age_conf, subscriber_count, video_count, channel_created_at, bio "
            f"FROM vkpi_commenter_profiles WHERE platform=? AND author_key IN ({placeholders})",
            (platform, *chunk),
        ).fetchall()
        for r in rows:
            rec = dict(r)
            out[str(rec.get("author_key") or "")] = rec
    return out


def _upsert_commenter_profiles(conn: Any, rows: list[dict[str, Any]]) -> int:
    now = _utcnow_iso()
    written = 0
    for rec in rows:
        if not rec.get("platform") or not rec.get("author_key"):
            continue
        subscriber_count = rec.get("subscriber_count")
        video_count = rec.get("video_count")
        age_conf = rec.get("age_conf")
        conn.execute(
            """
            INSERT INTO vkpi_commenter_profiles
                (platform, author_key, display_name, country, country_source, country_conf,
                 gender, gender_conf, language,
                 age_bucket, age_conf, subscriber_count, video_count, channel_created_at, bio,
                 updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(platform, author_key) DO UPDATE SET
                display_name=excluded.display_name,
                country=excluded.country,
                country_source=excluded.country_source,
                country_conf=excluded.country_conf,
                gender=excluded.gender,
                gender_conf=excluded.gender_conf,
                language=excluded.language,
                age_bucket=COALESCE(NULLIF(excluded.age_bucket,''), vkpi_commenter_profiles.age_bucket),
                age_conf=COALESCE(excluded.age_conf, vkpi_commenter_profiles.age_conf),
                subscriber_count=COALESCE(excluded.subscriber_count, vkpi_commenter_profiles.subscriber_count),
                video_count=COALESCE(excluded.video_count, vkpi_commenter_profiles.video_count),
                channel_created_at=COALESCE(NULLIF(excluded.channel_created_at,''), vkpi_commenter_profiles.channel_created_at),
                bio=COALESCE(NULLIF(excluded.bio,''), vkpi_commenter_profiles.bio),
                updated_at=excluded.updated_at
            """,
            (
                rec["platform"], rec["author_key"], rec.get("display_name") or "",
                rec.get("country") or "", rec.get("country_source") or "", float(rec.get("country_conf") or 0.0),
                rec.get("gender") or "", float(rec.get("gender_conf") or 0.0),
                rec.get("language") or "",
                rec.get("age_bucket") or "",
                float(age_conf) if age_conf is not None else None,
                int(subscriber_count) if subscriber_count is not None else None,
                int(video_count) if video_count is not None else None,
                rec.get("channel_created_at") or "", rec.get("bio") or "",
                now,
            ),
        )
        written += 1
    return written


# ── 年龄三路融合(v2)──

_AGE_ALIAS = {
    "0-18": "0-18", "under 18": "0-18", "13-18": "0-18", "<18": "0-18", "<=18": "0-18",
    "19-29": "19-29", "20-29": "19-29", "18-29": "19-29",
    "30-39": "30-39",
    "40+": "40+", "40-49": "40+", "50+": "40+", ">=40": "40+", ">40": "40+",
}


def _normalize_age_bucket(value: Any) -> str:
    return _AGE_ALIAS.get(str(value or "").strip().lower(), "")


def _age_from_channel_created(created_at: Any) -> tuple[str, float]:
    """C 路:注册年龄弱先验(conf .3)。YouTube 开户下限 13 岁 → 最小年龄 = 13 + 账号年龄。

    只有老账号有信息量(新账号不代表年轻人):账号不满 8 年不出信号;
    8 年以上按年龄下界落桶(如 2012 年注册 -> 下界约 27 -> '19-29'/'30-39' 权重上移)。
    """
    text = str(created_at or "").strip()
    if not text:
        return "", 0.0
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception:
        return "", 0.0
    years = (datetime.now(timezone.utc) - dt).days / 365.25
    if years < 8:
        return "", 0.0
    min_age = 13 + years
    if min_age >= 40:
        return "40+", 0.3
    if min_age >= 30:
        return "30-39", 0.3
    return "19-29", 0.3


_HANDLE_YEAR_RE = re.compile(r"(?<!\d)(19[5-9]\d|20[01]\d)(?!\d)")


def _age_from_handle(*texts: Any) -> tuple[str, float]:
    """D 路:用户名/显示名里的生日年启发(jake2008 → ~18)。conf 0.4 只作一票,
    数字可能不是生日(型号/纪念年),靠融合层与其它信号互证;不在 12-75 岁范围直接丢弃。"""
    for text in texts:
        m = _HANDLE_YEAR_RE.search(str(text or ""))
        if not m:
            continue
        age = datetime.now(timezone.utc).year - int(m.group(1))
        if not (12 <= age <= 75):
            continue
        if age <= 18:
            return "0-18", 0.4
        if age <= 29:
            return "19-29", 0.4
        if age <= 39:
            return "30-39", 0.4
        return "40+", 0.4
    return "", 0.0


def _fuse_age(signals: list[tuple[str, float]]) -> tuple[str, float]:
    """按 conf 加权投票融合多路年龄信号。

    赢家桶内多信号 noisy-or 合并(相互印证抬置信),再乘赢家得分份额(有分歧降置信)。
    单信号 -> 原 conf;无有效信号 -> ('', 0)。"""
    votes: dict[str, list[float]] = {}
    for bucket, conf in signals or []:
        normalized = _normalize_age_bucket(bucket)
        if normalized and float(conf or 0) > 0:
            votes.setdefault(normalized, []).append(min(0.95, float(conf)))
    if not votes:
        return "", 0.0
    scores = {b: sum(cs) for b, cs in votes.items()}
    winner = max(scores.items(), key=lambda kv: (kv[1], kv[0]))[0]
    total = sum(scores.values())
    disagreement_share = scores[winner] / total if total else 0.0
    agree_miss = 1.0
    for conf in votes[winner]:
        agree_miss *= 1 - conf
    fused = (1 - agree_miss) * disagreement_share
    return winner, round(min(0.9, fused), 2)


def _extract_json_array(text: str) -> list[dict[str, Any]]:
    """LLM 回复里抠 JSON 数组(容忍代码围栏/前后缀文本/被 max_tokens 截断的残缺数组)。

    截断救援:整体 parse 失败时,逐个抢救完整的 {...} 对象(thinking 型模型的思考 token
    会吃掉 maxOutputTokens,尾部截断是常态 —— 抢救到多少算多少,绝不编造)。
    抠不到返回 []。"""
    raw = str(text or "").strip()
    if "```" in raw:
        raw = raw.replace("```json", "```")
        parts = raw.split("```")
        raw = max(parts, key=len)
    start = raw.find("[")
    if start < 0:
        return []
    end = raw.rfind("]")
    if end > start:
        try:
            data = json.loads(raw[start : end + 1])
            if isinstance(data, list):
                return [item for item in data if isinstance(item, dict)]
        except Exception:
            pass
    # 救援:截断/夹杂散文时逐对象抠(对象内不嵌套,够用)。
    out: list[dict[str, Any]] = []
    for match in re.finditer(r"\{[^{}]*\}", raw[start:]):
        try:
            obj = json.loads(match.group(0))
            if isinstance(obj, dict):
                out.append(obj)
        except Exception:
            continue
    return out


def _age_llm_batches(
    commenters: list[dict[str, Any]],
    *,
    max_batches: int = AGE_LLM_MAX_BATCHES,
    batch_size: int = AGE_LLM_BATCH_SIZE,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """A 路:Gemini 批推年龄/性别(llm_gateway:预算记账 + 代理 + 结果落 ledger)。

    输入 显示名+bio+评论原文片段(2026-07-02:评论用语/话题/表情是比名字强得多的年龄信号,
    此前只喂名字+bio 导致判定率极低 → 1 人外推 100% 的笑话);头像视觉版留 P2。
    批 50 人/调用,单次刷新最多 max_batches 次。
    红线:rule_v0 兜底文本不当真(model=rule_v0 一律丢弃);失败/超闸跳过不阻断主流程。
    返回 (author_key -> {age_bucket, gender, conf}, stats)。
    """
    if max_batches <= 0 or not commenters:
        return {}, {"status": "skipped", "calls": 0, "batches_ok": 0, "people_in": 0, "people_out": 0}
    try:
        from app.platform import llm_gateway
    except Exception as exc:
        return {}, {"status": f"gateway_unavailable: {exc}"[:120], "calls": 0, "batches_ok": 0, "people_in": 0, "people_out": 0}
    out: dict[str, dict[str, Any]] = {}
    calls = 0
    batches_ok = 0
    people_in = 0
    batches = [commenters[i : i + batch_size] for i in range(0, len(commenters), batch_size)][:max_batches]
    for batch in batches:
        people_in += len(batch)
        lines = []
        for idx, c in enumerate(batch):
            name = str(c.get("display_name") or "").replace('"', "'")[:60]
            bio = str(c.get("bio") or "").replace("\n", " ").replace('"', "'")[:160]
            comment = str(c.get("comment_text") or "").replace("\n", " ").replace('"', "'")[:150]
            lines.append(f'{idx + 1}. name="{name}" bio="{bio}" comment="{comment}"')
        prompt = (
            "Task: AGGREGATE audience statistics for a marketing dashboard. Below are PUBLIC display names, "
            "public bios and one public comment of anonymous social media commenters. Results are only used "
            "as aggregate percentages (age buckets, gender split); nothing is attributed to any individual.\n"
            "For EACH numbered entry, classify from TEXT STYLE ONLY — name style, emoji, slang vs formal "
            "wording, topics referenced in the comment, stated roles like dad / retired / student / engineer. "
            "Comment language style (teen slang, professional jargon, dated phrasing) is the strongest cue:\n"
            '  "i": entry number, "age": "0-18"|"19-29"|"30-39"|"40+" or "" when no signal,\n'
            '  "gender": "male"|"female" or "" when no signal, "conf": 0.0-1.0.\n'
            "AGE: when there is ANY weak cue (slang vs formal tone, emoji habits, life-stage hints, topics, "
            "name style) give your best-supported bucket with a LOW conf (0.25-0.4) instead of empty — "
            "empty only when truly nothing. Adults discussing pro gear/work are usually 19-29 or 30-39, "
            "not 0-18. GENDER: stay conservative, empty beats a guess. Output STRICTLY one JSON array, "
            "no prose, no markdown fences. Your reply must start with the character [\n\n"
            + "\n".join(lines)
        )
        resp = llm_gateway.invoke(
            prompt,
            purpose="vkpi_audience_age_v1",
            preferred_provider="google",
            # thinking 型模型的思考 token 计入 maxOutputTokens,给足余量;残缺数组有救援解析兜底。
            max_output_tokens=4000,
            cost_tag="audience_stats",
        )
        calls += 1
        text = str(resp.get("text") or "")
        if str(resp.get("model") or "") == "rule_v0" or str(resp.get("status") or "") != "success" or not text.strip():
            logger.warning("audience age llm batch unusable: model=%s status=%s", resp.get("model"), resp.get("status"))
            continue
        parsed = _extract_json_array(text)
        if not parsed:
            # 诊断留痕:模型可能整段婉拒或输出散文;截前 200 字方便追查(不含个人数据以外内容)。
            logger.warning("audience age llm batch parse failed, preview=%r", text[:200])
            continue
        batches_ok += 1
        for item in parsed:
            try:
                idx = int(item.get("i")) - 1
            except (TypeError, ValueError):
                continue
            if not (0 <= idx < len(batch)):
                continue
            key = str(batch[idx].get("author_key") or "")
            bucket = _normalize_age_bucket(item.get("age"))
            gender = str(item.get("gender") or "").strip().lower()
            try:
                conf = max(0.0, min(1.0, float(item.get("conf") or 0.0)))
            except (TypeError, ValueError):
                conf = 0.0
            if key and (bucket or gender in ("male", "female")) and conf > 0:
                out[key] = {"age_bucket": bucket, "gender": gender if gender in ("male", "female") else "", "conf": round(conf, 2)}
    return out, {"status": "ok" if batches_ok else "failed", "calls": calls, "batches_ok": batches_ok,
                 "people_in": people_in, "people_out": len(out)}


def _m3_available() -> bool:
    try:
        import m3inference  # noqa: F401

        return True
    except Exception:
        return False


def _age_m3_batch(commenters: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], str]:
    """B 路:M3(可选依赖,文本模式)。未安装诚实返回 unavailable,绝不强装。

    安装法(重依赖含 torch,默认不装):.venv/bin/pip install m3inference
    """
    if not _m3_available():
        return {}, "unavailable"
    try:
        from m3inference import M3Inference  # type: ignore

        m3 = M3Inference(use_full_model=False, use_cuda=False, parallel=False)
        docs = [
            {
                "id": str(c.get("author_key") or ""),
                "name": str(c.get("display_name") or "")[:60],
                "screen_name": _first_name(str(c.get("display_name") or "")) or "user",
                "description": str(c.get("bio") or "")[:200],
                "lang": str(c.get("language") or "un") or "un",
            }
            for c in commenters
            if c.get("author_key")
        ]
        preds = m3.infer(docs) or {}
        bucket_map = {"<=18": "0-18", "19-29": "19-29", "30-39": "30-39", ">=40": "40+"}
        out: dict[str, dict[str, Any]] = {}
        for key, pred in preds.items():
            age = (pred or {}).get("age") or {}
            if not age:
                continue
            top_label, top_p = max(age.items(), key=lambda kv: kv[1])
            bucket = bucket_map.get(str(top_label), "")
            if bucket:
                out[str(key)] = {"age_bucket": bucket, "conf": round(float(top_p), 2)}
        return out, "ok"
    except Exception as exc:
        logger.warning("audience_stats m3 batch failed: %s", exc)
        return {}, f"error: {exc}"[:120]


def _update_age_cache(conn: Any, rows: list[dict[str, Any]]) -> int:
    """把融合后的 age/gender 写回身份缓存(行已由 upsert 保证存在)。"""
    now = _utcnow_iso()
    written = 0
    for rec in rows:
        if not rec.get("platform") or not rec.get("author_key"):
            continue
        age_conf = rec.get("age_conf")
        conn.execute(
            "UPDATE vkpi_commenter_profiles SET age_bucket=?, age_conf=?, gender=?, gender_conf=?, updated_at=? "
            "WHERE platform=? AND author_key=?",
            (
                rec.get("age_bucket") or "",
                float(age_conf) if age_conf is not None else None,
                rec.get("gender") or "",
                float(rec.get("gender_conf") or 0.0),
                now,
                rec["platform"], rec["author_key"],
            ),
        )
        written += 1
    return written


def _age_ensemble(
    conn: Any,
    platform: str,
    profiles: list[dict[str, Any]],
    *,
    llm_max_batches: int = AGE_LLM_MAX_BATCHES,
) -> dict[str, Any]:
    """ABC 三路年龄融合,就地更新 profiles 的 age_bucket/age_conf(必要时 gender),写回缓存。

    A=Gemini(只喂尚无缓存年龄、且有名字或 bio 的人;成本闸 llm_max_batches);
    B=M3(装了就用,没装 coverage 标 unavailable);C=频道注册年龄弱先验。
    gender:LLM 输出仅在新 conf 高于现有 gender_conf 时覆盖(人名表 .8 通常保留)。
    """
    # 评论文本也算可推断输入(2026-07-02):很多评论者无 bio,但评论用语本身就是年龄信号。
    need = [
        p for p in profiles
        if not p.get("age_bucket") and (p.get("display_name") or p.get("bio") or p.get("comment_text"))
    ]
    llm_pred, llm_stats = _age_llm_batches(need, max_batches=llm_max_batches)
    m3_pred, m3_status = _age_m3_batch(need)
    counts = {"cached": 0, "llm": 0, "m3": 0, "channel": 0, "handle_year": 0, "fused": 0}
    updates: list[dict[str, Any]] = []
    for p in profiles:
        if p.get("age_bucket"):
            counts["cached"] += 1
            continue
        key = str(p.get("author_key") or "")
        signals: list[tuple[str, float]] = []
        lp = llm_pred.get(key)
        if lp and lp.get("age_bucket"):
            signals.append((lp["age_bucket"], float(lp.get("conf") or 0.55)))
            counts["llm"] += 1
        mp = m3_pred.get(key)
        if mp and mp.get("age_bucket"):
            signals.append((mp["age_bucket"], float(mp.get("conf") or 0.5)))
            counts["m3"] += 1
        c_bucket, c_conf = _age_from_channel_created(p.get("channel_created_at"))
        if c_bucket:
            signals.append((c_bucket, c_conf))
            counts["channel"] += 1
        h_bucket, h_conf = _age_from_handle(p.get("author_key"), p.get("display_name"))
        if h_bucket:
            signals.append((h_bucket, h_conf))
            counts["handle_year"] += 1
        changed = False
        bucket, conf = _fuse_age(signals)
        if bucket:
            p["age_bucket"], p["age_conf"] = bucket, conf
            counts["fused"] += 1
            changed = True
        # gender:LLM 仅在新 conf 更高时覆盖(写缓存同一条 UPDATE 顺带)。
        if lp and lp.get("gender") in ("male", "female") and float(lp.get("conf") or 0) > float(p.get("gender_conf") or 0):
            p["gender"], p["gender_conf"] = lp["gender"], round(float(lp.get("conf") or 0), 2)
            changed = True
        if changed:
            updates.append(p)
    written = _update_age_cache(conn, updates) if updates else 0
    return {"llm": llm_stats, "m3": m3_status, "counts": counts, "cache_written": written}


# ── 聚合 + 经验贝叶斯收缩 ──

def _pct(numerator: int, denominator: int) -> float:
    return round(100.0 * numerator / denominator, 1) if denominator else 0.0


def _vertical_key(recommended_product_lines_json: Any) -> str:
    """recommended_product_lines_json 首项当垂类键(收缩 prior 的分组口径)。"""
    try:
        data = recommended_product_lines_json
        if isinstance(data, str):
            data = json.loads(data or "[]")
        if isinstance(data, list) and data:
            return str(data[0] or "").strip().lower()
    except Exception:
        pass
    return ""


def _vertical_prior(conn: Any, kol_pool_id: int, vertical: str) -> dict[str, Any] | None:
    """同垂类其它 KOL 已存 ensemble_v1 audience_estimated_json 的均值当 prior;没有返回 None。"""
    if not vertical:
        return None
    rows = conn.execute(
        "SELECT id, audience_estimated_json, recommended_product_lines_json FROM vkpi_kol_pool "
        "WHERE id<>? AND audience_estimated_json IS NOT NULL AND audience_estimated_json NOT IN ('','{}') "
        "AND recommended_product_lines_json IS NOT NULL LIMIT 500",
        (int(kol_pool_id),),
    ).fetchall()
    gender_acc: dict[str, list[float]] = {"male_pct": [], "female_pct": [], "unknown_pct": []}
    country_acc: dict[str, list[float]] = {}
    prior_n = 0
    for r in rows:
        rec = dict(r)
        try:
            est = json.loads(str(rec.get("audience_estimated_json") or "{}"))
        except Exception:
            continue
        if not isinstance(est, dict) or est.get("method") != METHOD:
            continue
        if _vertical_key(rec.get("recommended_product_lines_json")) != vertical:
            continue
        gender = est.get("gender") or {}
        for key in gender_acc:
            value = gender.get(key)
            if isinstance(value, (int, float)):
                gender_acc[key].append(float(value))
        for entry in est.get("top_countries") or []:
            if isinstance(entry, dict) and entry.get("code"):
                country_acc.setdefault(str(entry["code"]), []).append(float(entry.get("pct") or 0.0))
        prior_n += 1
    if prior_n == 0:
        return None
    return {
        "n": prior_n,
        "gender": {k: (sum(v) / len(v) if v else 0.0) for k, v in gender_acc.items()},
        "countries": {k: (sum(v) / max(prior_n, 1)) for k, v in country_acc.items()},
    }


def _apply_shrinkage(payload: dict[str, Any], prior: dict[str, Any] | None, n: int, tau: float = SHRINK_TAU) -> dict[str, Any]:
    """经验贝叶斯收缩:posterior = (n*sample + tau*prior) / (n+tau)。无 prior 原样返回(标注 skipped)。"""
    if not prior or n <= 0:
        payload["shrinkage"] = {"applied": False, "reason": "no_prior" if not prior else "empty_sample", "tau": tau}
        return payload
    weight = n / (n + tau)
    prior_gender = prior.get("gender") or {}
    gender = dict(payload.get("gender") or {})  # 拷贝:不就地改调用方的嵌套 dict
    for key in ("male_pct", "female_pct", "unknown_pct"):
        sample_value = float(gender.get(key) or 0.0)
        prior_value = float(prior_gender.get(key) or 0.0)
        gender[key] = round(weight * sample_value + (1 - weight) * prior_value, 1)
    payload["gender"] = gender
    prior_countries = dict(prior.get("countries") or {})
    merged: dict[str, float] = {}
    for entry in payload.get("top_countries") or []:
        code = str(entry.get("code") or "")
        if code:
            merged[code] = weight * float(entry.get("pct") or 0.0) + (1 - weight) * float(prior_countries.pop(code, 0.0))
    for code, prior_value in prior_countries.items():
        merged[code] = (1 - weight) * float(prior_value)
    payload["top_countries"] = [
        {"code": code, "pct": round(value, 1)}
        for code, value in sorted(merged.items(), key=lambda kv: kv[1], reverse=True)[:6]
        if value >= 0.5
    ]
    payload["shrinkage"] = {"applied": True, "tau": tau, "prior_n": int(prior.get("n") or 0), "weight": round(weight, 3)}
    return payload


def aggregate_audience(
    kol_pool_id: int,
    commenters: list[dict[str, Any]],
    *,
    conn: Any = None,
    platform: str = "",
) -> dict[str, Any]:
    """聚合已推断的评论者 -> 受众画像 payload(性别/国家/语言 + 样本量/覆盖率/置信度 + 收缩)。"""
    from app.db.connection import get_conn

    n = len(commenters)
    if n == 0:
        return {
            "method": METHOD, "platform": platform, "sample_size": 0, "confidence": 0.0,
            "gender": {"male_pct": 0.0, "female_pct": 0.0, "unknown_pct": 100.0},
            "top_countries": [], "languages": [],
            "coverage": {"declared_pct": 0.0, "name_pct": 0.0, "lang_pct": 0.0},
            "shrinkage": {"applied": False, "reason": "empty_sample", "tau": SHRINK_TAU},
            "note": "估算值(评论者画像),非平台官方粉丝数据", "beta": True,
        }
    male = sum(1 for c in commenters if c.get("gender") == "male")
    female = sum(1 for c in commenters if c.get("gender") == "female")
    country_counter: Counter = Counter(c.get("country") for c in commenters if c.get("country"))
    lang_counter: Counter = Counter(c.get("language") for c in commenters if c.get("language"))
    declared = sum(1 for c in commenters if c.get("country_source") == "declared")
    named = sum(1 for c in commenters if c.get("country_source") == "name")
    langed = sum(1 for c in commenters if c.get("country_source") == "language")
    # 置信度:样本量 + 自报占比(硬信号)+ 语言可判占比。IG/TT 无自报路,天然降档 —— 口径三平台一致。
    size_factor = min(1.0, n / 200.0)
    declared_ratio = declared / n
    lang_ratio = sum(lang_counter.values()) / n
    confidence = round(min(0.9, 0.15 + 0.45 * size_factor + 0.3 * declared_ratio + 0.1 * lang_ratio), 2)
    payload: dict[str, Any] = {
        "method": METHOD,
        "platform": platform,
        "sample_size": n,
        "gender": {
            "male_pct": _pct(male, n),
            "female_pct": _pct(female, n),
            "unknown_pct": _pct(n - male - female, n),
        },
        "top_countries": [{"code": code, "pct": _pct(count, n)} for code, count in country_counter.most_common(6)],
        "languages": [{"lang": lang, "pct": _pct(count, n)} for lang, count in lang_counter.most_common(8)],
        "coverage": {
            "declared_pct": _pct(declared, n),
            "name_pct": _pct(named, n),
            "lang_pct": _pct(langed, n),
        },
        "confidence": confidence,
        "note": "估算值(评论者画像),非平台官方粉丝数据",
        "beta": True,
    }
    # 性别归一(发布口径,v2):male/(male+female) 外推到 100;原始 coverage 与判定样本数照留。
    determined = male + female
    payload["gender_normalized"] = {
        "male_pct": _pct(male, determined),
        "female_pct": _pct(female, determined),
        "determined_n": determined,
        "determined_pct": _pct(determined, n),
    }
    # 年龄 4 桶(v2,BETA):ABC 融合后的 age_bucket 聚合;只在已判定人群内归一。
    age_counter: Counter = Counter(
        c.get("age_bucket") for c in commenters if c.get("age_bucket") in AGE_BUCKETS
    )
    age_known = sum(age_counter.values())
    # 最小判定门槛(2026-07-02):判定人数 < 5 不出分布 —— 1 人外推 100% 是误导不是估算。
    payload["age_bins"] = {
        "bins": (
            [{"bucket": b, "pct": _pct(age_counter.get(b, 0), age_known)} for b in AGE_BUCKETS]
            if age_known >= AGE_MIN_DETERMINED
            else []
        ),
        "determined_n": age_known,
        "determined_pct": _pct(age_known, n),
        "low_sample": bool(age_known and age_known < AGE_MIN_DETERMINED),
        "min_required": AGE_MIN_DETERMINED,
        "beta": True,
    }
    # 受众创作者浓度(v2):订阅数已知的评论者里,超过 CREATOR_DENSITY_MIN_SUBS 的占比。
    known_subs = [int(c.get("subscriber_count") or 0) for c in commenters if c.get("subscriber_count") is not None]
    payload["creator_density"] = {
        "pct": _pct(sum(1 for s in known_subs if s > CREATOR_DENSITY_MIN_SUBS), len(known_subs)) if known_subs else None,
        "known_n": len(known_subs),
        "min_subscribers": CREATOR_DENSITY_MIN_SUBS,
    }
    # 收缩 prior:同垂类均值(recommended_product_lines 首项当垂类键),tau=50;无先验跳过。
    try:
        db = conn or get_conn()
        row = db.execute(
            "SELECT recommended_product_lines_json FROM vkpi_kol_pool WHERE id=?", (int(kol_pool_id),)
        ).fetchone()
        vertical = _vertical_key(dict(row or {}).get("recommended_product_lines_json"))
        prior = _vertical_prior(db, int(kol_pool_id), vertical)
        payload = _apply_shrinkage(payload, prior, n)
        if vertical:
            payload["shrinkage"]["vertical"] = vertical
    except Exception as exc:
        logger.warning("audience_stats shrinkage failed kol=%s: %s", kol_pool_id, exc)
        payload.setdefault("shrinkage", {"applied": False, "reason": "error", "tau": SHRINK_TAU})
    return payload


# ── 编排:抽样 -> 推断(带缓存)-> 聚合 -> 落库 ──

def _youtube_channel_ref(row: dict[str, Any]) -> str:
    """从 pool 行找 YouTube channel 引用:raw 里的 channelId > profile_url /channel/UC > handle。"""
    handle = str(row.get("handle") or "").strip()
    if handle.startswith("UC") and len(handle) >= 20:
        return handle
    try:
        raw = row.get("raw_platform_data")
        data = json.loads(raw) if isinstance(raw, str) and raw.strip() else (raw if isinstance(raw, dict) else {})
        for key in ("channel_id", "channelId"):
            value = str((data or {}).get(key) or "").strip()
            if value.startswith("UC"):
                return value
        identity = (data or {}).get("identity") or {}
        value = str(identity.get("channel_id") or identity.get("channelId") or "").strip()
        if value.startswith("UC"):
            return value
    except Exception:
        pass
    profile_url = str(row.get("profile_url") or "")
    match = re.search(r"/channel/(UC[0-9A-Za-z_-]{10,})", profile_url)
    if match:
        return match.group(1)
    return handle or profile_url


def _infer_with_cache(conn: Any, platform: str, commenters: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """按 (platform, author_key) 走身份缓存:命中直接用;未命中推断后 upsert。"""
    for c in commenters:
        c["platform"] = platform
    keys = [str(c.get("author_key") or "") for c in commenters if c.get("author_key")]
    cached = _load_cached_profiles(conn, platform, keys)
    inferred: list[dict[str, Any]] = []
    fresh: list[dict[str, Any]] = []
    extras_keys = ("bio", "channel_created_at", "subscriber_count", "video_count")
    cache_hits = 0
    inferred_fresh = 0
    for c in commenters:
        key = str(c.get("author_key") or "")
        hit = cached.get(key)
        # 自报国家是最强信号:抽样带回了自报而缓存里不是 declared 口径时,重推断刷新缓存。
        if hit and not (c.get("declared_country") and hit.get("country_source") != "declared"):
            cache_hits += 1
            merged = dict(hit)
            # v2:抽样新带回的白捡字段(bio/订阅数/频道年龄)补进缓存命中行,并回写缓存。
            extras_changed = False
            for field in extras_keys:
                value = c.get(field)
                if value not in (None, "") and value != merged.get(field):
                    merged[field] = value
                    extras_changed = True
            if extras_changed:
                fresh.append(merged)
            inferred.append(merged)
            continue
        rec = infer_commenter(c)
        inferred_fresh += 1
        fresh.append(rec)
        inferred.append(rec)
    written = _upsert_commenter_profiles(conn, fresh) if fresh else 0
    return inferred, {"cache_hits": cache_hits, "inferred_fresh": inferred_fresh, "cache_written": written}


def refresh_audience_stats(
    kol_pool_id: int,
    *,
    max_comments: int = 400,
    enqueue_if_missing: bool = True,
    llm_max_batches: int = AGE_LLM_MAX_BATCHES,
) -> dict[str, Any]:
    """入口:抽样 -> 推断(含 ABC 年龄融合)-> 聚合归一 -> comment_intel/overlap -> 一次写库。

    - youtube:Data API 全频道评论抽样(免费额度;本地被墙时报 network_error + 代理提示)。
    - instagram / tiktok:复用 vkpi_comments 已抓评论;不足 MIN_LOCAL_COMMENTS 条则入队抓评论
      (enqueue_kol_pool_comments_job,幂等),返回 pending_comments,下次刷新即有数据。
    llm_max_batches=0 可整体关掉 A 路(Gemini)。异常诚实返回 {status, reason}。
    红线:只写 audience_estimated_json + updated_at,绝不触 viltrox_fit_score、不碰 rule_v0。
    """
    from app.db.connection import get_conn

    started = time.time()
    conn = get_conn()
    row = conn.execute(
        "SELECT id, platform, handle, profile_url, raw_platform_data FROM vkpi_kol_pool WHERE id=?",
        (int(kol_pool_id),),
    ).fetchone()
    if not row:
        raise LookupError("kol pool item not found")
    rec = dict(row)
    platform = str(rec.get("platform") or "").strip().lower()

    if platform == "youtube":
        ref = _youtube_channel_ref(rec)
        if not ref:
            return {"status": "skipped", "reason": "no_channel_reference", "kol_pool_id": int(kol_pool_id)}
        sample = sample_youtube_commenters(ref, max_comments=max_comments)
        if sample.get("status") != "ok":
            return {**sample, "kol_pool_id": int(kol_pool_id)}
    elif platform in ("instagram", "tiktok"):
        sample = sample_local_commenters(int(kol_pool_id), conn=conn)
        if int(sample.get("comments_scanned") or 0) < MIN_LOCAL_COMMENTS:
            # 无帖可采就别入队:evidence 为空时采集 job 会 1 秒空转"done",
            # 用户按"已入队稍后刷新"的提示等不到任何结果 —— 诚实返回 no_posts 让 UI 引导先跑账号分析。
            ev_n = 0
            try:
                ev_row = conn.execute(
                    "SELECT COUNT(*) AS n FROM vkpi_kol_video_evidence WHERE kol_pool_id=?",
                    (int(kol_pool_id),),
                ).fetchone()
                ev_n = int(dict(ev_row).get("n") or 0) if ev_row else 0
            except Exception:
                ev_n = 0
            if ev_n <= 0:
                return {
                    "status": "no_posts",
                    "kol_pool_id": int(kol_pool_id),
                    "platform": platform,
                    "comments_found": int(sample.get("comments_scanned") or 0),
                    "min_required": MIN_LOCAL_COMMENTS,
                    "enqueued": False,
                    "reason": "池内暂无该 KOL 的帖子记录,先对该 KOL 跑一次账号/视频分析再生成受众统计",
                }
            enqueued = False
            enqueue_status = ""
            if enqueue_if_missing:
                try:
                    from app.domains.comments.collector import enqueue_kol_pool_comments_job

                    result = enqueue_kol_pool_comments_job(int(kol_pool_id))
                    enqueue_status = str(result.get("status") or "")
                    enqueued = enqueue_status in ("queued", "already_queued")
                except Exception as exc:
                    enqueue_status = f"enqueue_failed: {exc}"[:200]
            return {
                "status": "pending_comments",
                "kol_pool_id": int(kol_pool_id),
                "platform": platform,
                "comments_found": int(sample.get("comments_scanned") or 0),
                "min_required": MIN_LOCAL_COMMENTS,
                "enqueued": enqueued,
                "enqueue_status": enqueue_status,
                "reason": "本地评论不足,已入队抓评论,稍后再刷新" if enqueued else "本地评论不足",
            }
    else:
        return {"status": "unsupported_platform", "platform": platform, "kol_pool_id": int(kol_pool_id),
                "reason": "P0 支持 youtube/instagram/tiktok"}

    commenters = list(sample.get("commenters") or [])
    if not commenters:
        return {"status": "no_commenters", "kol_pool_id": int(kol_pool_id), "platform": platform,
                "comments_scanned": int(sample.get("comments_scanned") or 0)}
    inferred, cache_stats = _infer_with_cache(conn, platform, commenters)
    # v2:年龄 ABC 三路融合(失败不阻断主流程,coverage 里诚实标注)。
    age_stats: dict[str, Any] = {"llm": {"status": "skipped", "calls": 0}, "m3": "unavailable", "counts": {}}
    try:
        age_stats = _age_ensemble(conn, platform, inferred, llm_max_batches=llm_max_batches)
    except Exception as exc:
        logger.warning("audience_stats age ensemble failed kol=%s: %s", kol_pool_id, exc)
        age_stats["error"] = str(exc)[:200]
    payload = aggregate_audience(int(kol_pool_id), inferred, conn=conn, platform=platform)
    payload["generated_at"] = _utcnow_iso()
    payload["comments_scanned"] = int(sample.get("comments_scanned") or 0)
    payload["cache"] = cache_stats
    payload["age_coverage"] = age_stats
    if platform == "youtube":
        payload["channel_id"] = str(sample.get("channel_id") or "")
    # v2:评论情报(纯词表/直方零成本)。YT 用本次 API 抽样带回的评论;IG/TT 读 vkpi_comments。
    try:
        from app.domains.kol import comment_intel as ci

        api_comments = list(sample.get("comments") or [])
        if api_comments:
            intel = ci.analyze_comments(api_comments)
            intel["source"] = "youtube_api_sample"
            reply_total = int(sample.get("reply_total") or 0)
            if reply_total and isinstance(intel.get("engagement"), dict):
                # API 只抓 top-level:回复占比按 thread 的 totalReplyCount 口径补算。
                top_n = int(intel.get("sample_size") or 0)
                intel["engagement"]["reply_pct"] = _pct(reply_total, top_n + reply_total)
                intel["engagement"]["reply_basis"] = "thread_total_reply_count"
        else:
            intel = ci.comment_intel_for_kol(int(kol_pool_id), conn=conn)
        payload["comment_intel"] = intel
    except Exception as exc:
        logger.warning("comment_intel failed kol=%s: %s", kol_pool_id, exc)
        payload["comment_intel"] = {"sample_size": 0, "error": str(exc)[:200]}
    # v2:共同粉丝(audience overlap)—— 矩阵投放去重用(重叠高的 KOL 不必都投)。
    try:
        from app.domains.kol.comment_intel import compute_audience_overlap

        payload["overlap"] = compute_audience_overlap(int(kol_pool_id), conn=conn)
    except Exception as exc:
        logger.warning("audience overlap failed kol=%s: %s", kol_pool_id, exc)
        payload["overlap"] = {"items": [], "error": str(exc)[:200]}
    conn.execute(
        "UPDATE vkpi_kol_pool SET audience_estimated_json=?, updated_at=? WHERE id=?",
        (json.dumps(payload, ensure_ascii=False), _utcnow_iso(), int(kol_pool_id)),
    )
    conn.commit()
    return {
        "status": "ok",
        "kol_pool_id": int(kol_pool_id),
        "platform": platform,
        "sample_size": payload.get("sample_size"),
        "confidence": payload.get("confidence"),
        "audience": payload,
        "elapsed_sec": round(time.time() - started, 2),
    }
