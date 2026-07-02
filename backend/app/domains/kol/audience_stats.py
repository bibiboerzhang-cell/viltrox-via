"""受众画像 ensemble_v1(P0)—— 三平台评论者抽样 -> 推断 -> 聚合 -> audience_estimated_json。

像 Modash / NanoInfluencer 的 Audience Stats:性别环 + Top countries + 语言分布,
带样本量/覆盖率/置信度,前端标 BETA。数据链路:
  - YouTube:Data API(免费额度)抽 commentThreads 评论者 -> channels.list 批量拿自报 country。
  - Instagram / TikTok:复用库里已抓的 vkpi_comments(不新写抓取);评论不足则入队抓评论。
推断三层信号(逐层降置信):自报国家 .9 > 人名词表国籍猜 .4 > 评论语言推市场 .3;
性别用内置人名表(仅高辨识度常见名,conf .8,未知诚实留空,不猜)。
聚合后做经验贝叶斯收缩:prior=同垂类已有 audience_estimated 的均值,tau=50,无先验跳过。
身份推断结果落 vkpi_commenter_profiles 缓存(迁移 205),同评论者跨 KOL 复用。

红线:绝不写 viltrox_fit_score、不碰 rule_v0;全部估算值明示 method/置信度,不冒充官方数据。
网络:本地网络到 googleapis 需代理 —— 读 env HTTPS_PROXY(runtime_env.sh 会从 YTDLP_PROXY 导出),
没配则在错误里提示。
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
    """YouTube 免费 Data API 抽评论者:commentThreads(全频道) -> channels.list 批量拿自报 country。

    返回 {status, channel_id, commenters:[{platform,author_key,display_name,comment_text,declared_country}],
    comments_scanned, api_calls}。网络/配置失败诚实返回 {status:..., reason:...}(不 raise)。
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
    comments_scanned = 0
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
                snippet = ((thread or {}).get("snippet") or {}).get("topLevelComment", {}).get("snippet", {})
                if not isinstance(snippet, dict):
                    continue
                comments_scanned += 1
                author_id = str(((snippet.get("authorChannelId") or {}).get("value")) or "").strip()
                if not author_id:
                    continue
                entry = by_author.setdefault(
                    author_id,
                    {
                        "platform": "youtube",
                        "author_key": author_id,
                        "display_name": str(snippet.get("authorDisplayName") or "").strip(),
                        "comment_text": "",
                        "declared_country": "",
                    },
                )
                if not entry["comment_text"]:
                    entry["comment_text"] = str(snippet.get("textOriginal") or snippet.get("textDisplay") or "")[:500]
            page_token = str(payload.get("nextPageToken") or "")
            if not page_token or not items:
                break
    except RuntimeError as exc:
        if not by_author:
            return {"status": "network_error", "reason": str(exc)[:400]}
        logger.warning("audience_stats yt comment paging partial: %s", exc)

    # 批量 channels.list(part=snippet, 50/批)拿评论者自报 country(强信号,conf .9)。
    author_ids = list(by_author.keys())
    declared_hits = 0
    for index in range(0, len(author_ids), 50):
        chunk = author_ids[index : index + 50]
        try:
            payload = _yt_get("channels", {"part": "snippet", "id": ",".join(chunk), "maxResults": 50})
            api_calls += 1
        except RuntimeError as exc:
            logger.warning("audience_stats yt channels.list batch failed: %s", exc)
            continue
        for item in payload.get("items") or []:
            if not isinstance(item, dict):
                continue
            cid = str(item.get("id") or "")
            country = str(((item.get("snippet") or {}).get("country")) or "").strip().upper()
            if cid in by_author and country:
                by_author[cid]["declared_country"] = country
                declared_hits += 1
    return {
        "status": "ok",
        "channel_id": channel_id,
        "commenters": list(by_author.values()),
        "comments_scanned": comments_scanned,
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
        "SELECT author_handle, comment_text FROM vkpi_comments WHERE account_id=? LIMIT ?",
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
                "SELECT author_handle, comment_text FROM vkpi_comments "
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
        if not entry["comment_text"]:
            entry["comment_text"] = str(rec.get("comment_text") or "")[:500]
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
    }


def _load_cached_profiles(conn: Any, platform: str, author_keys: list[str]) -> dict[str, dict[str, Any]]:
    if not author_keys:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for index in range(0, len(author_keys), 200):
        chunk = author_keys[index : index + 200]
        placeholders = ",".join(["?"] * len(chunk))
        rows = conn.execute(
            "SELECT platform, author_key, display_name, country, country_source, country_conf, gender, gender_conf, language "
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
        conn.execute(
            """
            INSERT INTO vkpi_commenter_profiles
                (platform, author_key, display_name, country, country_source, country_conf,
                 gender, gender_conf, language, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(platform, author_key) DO UPDATE SET
                display_name=excluded.display_name,
                country=excluded.country,
                country_source=excluded.country_source,
                country_conf=excluded.country_conf,
                gender=excluded.gender,
                gender_conf=excluded.gender_conf,
                language=excluded.language,
                updated_at=excluded.updated_at
            """,
            (
                rec["platform"], rec["author_key"], rec.get("display_name") or "",
                rec.get("country") or "", rec.get("country_source") or "", float(rec.get("country_conf") or 0.0),
                rec.get("gender") or "", float(rec.get("gender_conf") or 0.0),
                rec.get("language") or "", now,
            ),
        )
        written += 1
    return written


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
    for c in commenters:
        key = str(c.get("author_key") or "")
        hit = cached.get(key)
        # 自报国家是最强信号:抽样带回了自报而缓存里不是 declared 口径时,重推断刷新缓存。
        if hit and not (c.get("declared_country") and hit.get("country_source") != "declared"):
            inferred.append(dict(hit))
            continue
        rec = infer_commenter(c)
        fresh.append(rec)
        inferred.append(rec)
    written = _upsert_commenter_profiles(conn, fresh) if fresh else 0
    return inferred, {"cache_hits": len(inferred) - len(fresh), "inferred_fresh": len(fresh), "cache_written": written}


def refresh_audience_stats(
    kol_pool_id: int,
    *,
    max_comments: int = 400,
    enqueue_if_missing: bool = True,
) -> dict[str, Any]:
    """入口:按平台分流抽样 -> 推断 -> 聚合 -> 写 audience_estimated_json。异常诚实返回 {status, reason}。

    - youtube:Data API 全频道评论抽样(免费额度;本地被墙时报 network_error + 代理提示)。
    - instagram / tiktok:复用 vkpi_comments 已抓评论;不足 MIN_LOCAL_COMMENTS 条则入队抓评论
      (enqueue_kol_pool_comments_job,幂等),返回 pending_comments,下次刷新即有数据。
    红线:只写 audience_estimated_json + updated_at,绝不触 viltrox_fit_score。
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
    payload = aggregate_audience(int(kol_pool_id), inferred, conn=conn, platform=platform)
    payload["generated_at"] = _utcnow_iso()
    payload["comments_scanned"] = int(sample.get("comments_scanned") or 0)
    payload["cache"] = cache_stats
    if platform == "youtube":
        payload["channel_id"] = str(sample.get("channel_id") or "")
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
