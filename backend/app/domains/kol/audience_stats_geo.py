"""受众画像 · 身份推断 + 地理/性别聚合(从 audience_stats 拆出;地理口径 2026-08 去假)。

人名词表(性别 .8 / 国籍 .4)+ 自报国家 .9 -> vkpi_commenter_profiles 缓存;聚合出性别环 /
国家分层 / 语言分布 + 经验贝叶斯收缩(同垂类 prior,tau=50)。
地理红线:只在硬信号(自报 / 人名)样本 >= GEO_MIN_SAMPLE 时出国家分层,否则
method=insufficient_sample、top_countries=[];评论语言只进 languages,不再被当国家。
红线:绝不写 viltrox_fit_score、不碰 rule_v0。
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any

from app.core.logging import get_logger
from app.domains.kol.audience_language import detect_lang
from app.domains.kol.audience_stats_age import (
    AGE_BUCKETS,
    AGE_MIN_DETERMINED,
    _first_name,
    _utcnow_iso,
)

logger = get_logger(__name__)

METHOD = "ensemble_v1"
SHRINK_TAU = 50.0
CREATOR_DENSITY_MIN_SUBS = 1000  # 受众创作者浓度口径:订阅数超过 1000
GEO_MIN_SAMPLE = 30  # 硬信号(自报/人名)判定的评论者数低于此不出国家分层
GEO_HARD_SOURCES = frozenset({"declared", "name"})
GEO_METHOD = "commenter_country_v1"

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



# ── 推断层 ──

def infer_commenter(profile: dict[str, Any]) -> dict[str, Any]:
    """单评论者推断:国家=自报 .9 > 人名词表 .4(评论语言不再推国家);性别=人名表 .8(未知留空)。"""
    display_name = str(profile.get("display_name") or "")
    comment_text = str(profile.get("comment_text") or "")
    declared = str(profile.get("declared_country") or "").strip().upper()
    language = detect_lang(comment_text) if comment_text else "und"
    first = _first_name(display_name)

    # 国家只认两层硬信号:自报 .9 > 人名词表 .4。评论语言 -> 代表市场(en -> US)不再写成
    # country(2026-08 地理去假:那是市场猜测不是国家推断,曾把英语评论者全算成 US 79%);
    # 语言留在 language 列,市场倾向由 geo_ensemble 的语言信号独立降权表达。
    country, country_source, country_conf = "", "", 0.0
    if declared:
        country, country_source, country_conf = declared, "declared", 0.9
    elif first and first in _NAME_COUNTRY:
        country, country_source, country_conf = _NAME_COUNTRY[first], "name", 0.4

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
        # 瞬态直通(不入缓存表):A 路年龄批要喂评论原文,丢了等于文本信号归零。
        "comment_text": comment_text[:500],
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
        logger.debug("recommended_product_lines_json 解析失败,垂类键留空(best-effort)", exc_info=True)
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
        # 国家 prior 只吃新口径(geo.method=commenter_country_v1)的存量;旧 JSON 的 top_countries
        # 是「语言 -> 市场」假地理,混进 prior 会把假 US 再传染给新 KOL。性别 prior 不受影响。
        geo = est.get("geo") if isinstance(est.get("geo"), dict) else {}
        if str(geo.get("method") or "") == GEO_METHOD:
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


def _apply_shrinkage(
    payload: dict[str, Any],
    prior: dict[str, Any] | None,
    n: int,
    tau: float = SHRINK_TAU,
    *,
    shrink_countries: bool = True,
) -> dict[str, Any]:
    """经验贝叶斯收缩:posterior = (n*sample + tau*prior) / (n+tau)。无 prior 原样返回(标注 skipped)。
    shrink_countries=False(地理样本不足)时只收缩性别,top_countries 保持空 —— prior 不能无中生有。"""
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
    if not shrink_countries:
        payload["shrinkage"] = {
            "applied": True, "tau": tau, "prior_n": int(prior.get("n") or 0), "weight": round(weight, 3),
            "countries": "skipped_insufficient_geo_sample",
        }
        return payload
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


# ── 地理分层(2026-08 去假)──

def geo_breakdown(commenters: list[dict[str, Any]], *, min_sample: int = GEO_MIN_SAMPLE) -> dict[str, Any]:
    """受众国家分层:只数 country_source 属硬信号(自报 declared / 人名词表 name)的评论者。

    旧口径把「评论语言 -> 代表市场」(en -> US)当国家,英语评论一律算 US,再被收缩 prior 放大成
    US 79% 的假地理;更早的前端还拿创作者国别@100% 冒充受众。现在:硬信号样本 >= min_sample 才出
    分层(pct 按已判定样本归一),否则 method=insufficient_sample、confidence=0、top_countries=[]。
    """
    n = len(commenters)
    hard = [c for c in commenters if c.get("country") and c.get("country_source") in GEO_HARD_SOURCES]
    k = len(hard)
    sources: Counter = Counter(str(c.get("country_source")) for c in hard)
    base = {
        "sample_n": n,
        "determined_n": k,
        "determined_pct": _pct(k, n),
        "min_required": int(min_sample),
        "source_breakdown": dict(sources),
        "hard_sources": sorted(GEO_HARD_SOURCES),
    }
    if k < int(min_sample):
        return {
            **base,
            "method": "insufficient_sample",
            "confidence": 0.0,
            "top_countries": [],
            "note": f"有国家硬信号的评论者 {k} 人,不足 {int(min_sample)} 人,不出地理分层(不以语言市场或创作者国别冒充)",
        }
    counter: Counter = Counter(str(c.get("country")).upper() for c in hard)
    declared_ratio = sources.get("declared", 0) / k
    size_factor = min(1.0, k / 200.0)
    confidence = round(min(0.9, 0.2 + 0.4 * size_factor + 0.3 * declared_ratio), 2)
    return {
        **base,
        "method": GEO_METHOD,
        "confidence": confidence,
        "top_countries": [{"code": code, "pct": _pct(count, k)} for code, count in counter.most_common(6)],
        "note": "估算值:按有国家硬信号(自报/人名)的评论者归一,非平台官方粉丝数据",
    }


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
            "geo": geo_breakdown([]),
            "coverage": {"declared_pct": 0.0, "name_pct": 0.0, "lang_pct": 0.0},
            "shrinkage": {"applied": False, "reason": "empty_sample", "tau": SHRINK_TAU},
            "note": "估算值(评论者画像),非平台官方粉丝数据", "beta": True,
        }
    male = sum(1 for c in commenters if c.get("gender") == "male")
    female = sum(1 for c in commenters if c.get("gender") == "female")
    lang_counter: Counter = Counter(c.get("language") for c in commenters if c.get("language"))
    declared = sum(1 for c in commenters if c.get("country_source") == "declared")
    named = sum(1 for c in commenters if c.get("country_source") == "name")
    langed = sum(lang_counter.values())
    geo = geo_breakdown(commenters)
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
        # 地理(2026-08 去假):只在硬信号样本 >= GEO_MIN_SAMPLE 时出分层,pct 按已判定样本归一;
        # 不足则 [] + geo.method=insufficient_sample,绝不拿语言市场或创作者国别冒充。
        "top_countries": list(geo.get("top_countries") or []),
        "geo": geo,
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
        payload = _apply_shrinkage(payload, prior, n, shrink_countries=geo.get("method") == GEO_METHOD)
        if vertical:
            payload["shrinkage"]["vertical"] = vertical
    except Exception as exc:
        logger.warning("audience_stats shrinkage failed kol=%s: %s", kol_pool_id, exc)
        payload.setdefault("shrinkage", {"applied": False, "reason": "error", "tau": SHRINK_TAU})
    return payload


