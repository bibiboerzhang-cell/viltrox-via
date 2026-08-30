from __future__ import annotations

import asyncio
import ast
import base64
from copy import deepcopy
from functools import lru_cache
import hashlib
from pathlib import Path
from typing import Any, Callable
import zlib

from fastapi import HTTPException
import pytest

from app.api.routers import vkpi_kol_pool_search as route
from scripts.vkpi_engineering_health_collect import collect_complexity


ROOT = Path(__file__).resolve().parents[1]
ROUTE_PATH = ROOT / "backend/app/api/routers/vkpi_kol_pool_search.py"
HELPER_PATH = ROOT / "backend/app/api/routers/vkpi_kol_pool_smart_search_helpers.py"
SCENARIO_COUNT = 1024
LEGACY_SHA256 = "2035c7a8af23643fc946eae2faa4305e31fffe298a2efd9e60641784f8220971"
LEGACY_SOURCE_B85 = """
c-pNy{c{sXvcL0J%oFvhB%UmsaLIdRO0S9w&K-
45$bq?6DJr$KmPYo*>s@7c<&Wa1GM_fa#wL7Y8!#ck2ZwDA9vIn%|K*}tNk84c@Oox;SF^jbvP_<f!tzW{cTaauPj^pCwI_@MD&i
8dM^wvEhD}|uv1%2D()$qn44B1<9#qr{4$7n7!yoBfE|<x&IonZ7rTkG`#zxUjTlj~HW?{uq2hyq0;j)&>qSTnBIXIPp1sT-!-
waO=TboLyP8nKB!$nRQX-
2u?Aiz{MHN(m6V^Bvbj@ebz?6T?<hOmvws_npvQz!at<e11A!t^s5Gp*s0ZjK?<D5A2iY6f!fxI;n8mRZtt%&=Lwpz3-
K9dqbM(<<7?v?yN3QXS1S=!P5&oFFa%_+{HL4q%2p!H^qx6k80LV^$bTbf-
*eWz*D=4QMrE5W&XuqMh@%%6|V)!PbPr7|R1GD@|pa8^lgJ#WtVH06VBP$l#@e3`umRV(EM+mCED*y;e?V7+tM|4AT+Fa}6+wsG5
z@AN%{i=^ZcNG8ICa3ZY^v1+$3rzw7SKf^j&irsp}6NVAcr>0*lG9|6eNj_9DrjtWX_v6KTnVXMU&TTS@`v7h0vU^jF#@QZ7Vequ
X5`;nY88Y30kK?4|35?KTUvYtU?DVJQL+~h6Q0OjX77XgoXe*mD<!3blRnTDd9=CG~k+AvlCF5QxMCWEjJ-
1^%IZJi=v#9Mo7G2k!Y7H3VgU|_iq=JC~<<F;;BbZTgdRWT?(N)h9-
Qn1u9JspCiKWAbi^yk0<s?V%+j*}FODh_rwKEyo+`#(MjPDllwfzcAS6z|~X`>hI2hAmhaj;!Jx)LnpWvtku+KE-?-
^LG54r@~7z5kWc?%aBMY#mx<bz{CpzFN3Q9Uf6J&D<Bi+h98X{&&K1^o=7Lk6v*SVHJ0XD&wD3?Q^_Y2<KUDW-
F4UwWYjq(faf115L{-
y$OA}z>?4yhjzF=aS`jo%O`D2u032spYGfkj@G_;0t&(YtP$xtWqzI|&BXOV@ei>_{n(kQUxKh%<G{BR?K{mLbFGlv+4l;uI?7o)
MV#k?lGv+a)o?Y-CA$LxH_M;Q{dklB-h^q4)9xRwfNr&r|-
aaO5Ty+2koFE5VHn=^2!ib#~3ZM#@iWN&8(6N8nQPQ5=7^E@bvCVGTT(A(2mw_B?9}ILDQ%}P#L4Il#xU&l;T$K|!keaEFVh;?Qv
q7(0xZ=~1W7ZN)SboBt!cau-y@Cm%rx#^wSxZn{h!7GubVGI`m%(lVP^Dy<Bb=YK$Xq5a0TF$8R}u1|xzHv8kcg#*fDAm=Vp-
{E$sWOdAOS`+I&x{+ojq53cZtm2CiCl!r{`+h_Z#cWS#o)foV#D!+N$l`A`h$el}R%Hce424*U9tb%3S^Vdj0iN_u9|(<;ljzh5F
4^0jH;nF10uco(gw;Zw??by-2pIWZ@c_-vFd!X5}z7K5U`d<_q`AT;uI^vNr49dQF2pgPjqs9H1wlZhW}coSG%`&wygB`jp%_SAY
0(WBoFz-
Z<EJdXY@uBh$;|>D9*C3Yq>9{ReWF{!W%Q5R2s8+f(#UwvB9K{jK}*eC_>H0efw84z}HR`U{!Aj{5q(`gQUg9vQ&JB0V<l^ldr@W
)1##SY%5xG)Gf)-
$i<{^<UA~ra_}9`b)gz6{;MCxkz>yqg43HOJ)7*ja`13a1;<B2Kd!6RfF&sVgd~WHw3tfWvTydB?A6xJBJOsVg;lXuoNZfn($$?V
jd_t3V<50HNoGtAXt77MZmY2aHF?;V?Nn&2!L>L=2?`b_z`8CVICZWeKH^P;@DW&AmN(D$VyqA&`q_NKQoLWc^Xyq3PvC-
1b=3|KOx#>WFS2nvC|o<YM4d;Mng(SC>(Pb8>ziSP?;nP0|C=8P*g%XY8SxUnX?%$7oqyt7u6$W7SM#lAX$KT;D98&6qVg0VK8pi
J?KjnV&=1P36~`6V^A#KJ`-LosH-(WgN49LcYHuvrqEhzU@I4KM@kU1lq?)E<MtO`J}lUb=n|-
jhS~OvmB*ug4#*M%1LGoOM7nB0u3cbRZX{8_BV}ho3DN~goI8S@A(QHj#$d%t0WNXXQFU`r8LX&QQ5HoqtA-
V)zmtWel>%Jjbova}x@mDx8hwsx7}7)bx5WD4K2rw`({{9iw;nPttw;@rg#j?u&Ct-
89T!`JGF)L90#tZFvI27ZLCsKgMTpgUP<s!Q!le>KY8r$<p;f6=G)!!x6-9L%m3m@ka~?s@{Nu-s8IX8z<6L=pgGlB#c-
an|QirLO)L`4>P)wM+B~iP0ej!Kd*uaA{sl5c_$&v{F@hKDYjqLyp(_uUJV^rkr45E!9tG0~LiW)1Rd3!w!!z^S0?-
4|e$VZ6AR2xz1SSpUaAEi(%3kzu6v*(OV9vnpCWWlyfY6ZX+Eg~i!zsPy@@-(T#<(d=fbfH8cmbC>5L;I}6D^q?<O0pl-Uo18!ue
evY8$Vrwh_*5HjLfXMH{X-BowQmmA1s<9kbKz(f#5-pe_Fx<qPjYsD^Cak<bLF?u8;>;A%J%07aD78*;=*g-
rA_&eA!%l<4!L?e2h;1xetAD{M6U|h^*ZwPpXG$Sp4K6I@FB-nrysuXRrJ^Ia}NMskZgjU792t^S@5c)$d<yu2e~NUL?ktJaB*$b
+4_uw_gC<fdhO?Y4^rlZD)yGzgw&R!@Y8qTzwC-<j2{@>(%DeJXx4_A8psGA8MPE@S%BOhcYs=DjX*uvHZSPy-aRA1EA*0JBZln$
h)hMqj(=6<yg2#p4=xJ*Ma>kxwP86uu85ybRWHKd|33{=IK;RWU22EBnb7bdjWgl90c+X;I8@jVr_fFy}M2pX5AM*x1^ROA1*alw
(7rZk?qM^^=fVF4SD~per=tm7H_XdaBa@O2Af-
V8*5L<rB~@syFdNBtNXvYKK(q?L)PE9Gk3_7Irq^fSzaJ(4{DoF+~pOrao=5fR{O992@}YxzFlqJd;8DHoAlrHM;>3dHfo#Kc&gL
5^g$HZ(zE8|0(o%<&^LD$(x6GQ@Ek@VkpeX~&;P{5O*QfOL49#1KvLhj;Xb$s9FzImR1?!1AWslDS==FOZ$J#ScUKzgbHF6|`3{-
?iM+oEvtiOSFZ0!!$<iba5KUbMV!(0pZ!bZ;qJ6vTHvz!=y!g8Q>fzykUSFxd+Ny6o1jacinR&$R3D}4Hkj%|A-
oF!5+_hP%^yPaX=4|7_j(h$h{kQlb&@W(sZPm7J*S4;?|5$0Pzixb(qC73Hfc8LRT<~1v<lP&tljh`o0HgLb{m8v`1xN!}V`Ww}P
fxRRuiSHwVO4Fj3PwRw=&2ppF!0}~KBp&#gH3Le=`Hu-eC@+BP0B$u^LMjk?w0$%SGhdd&>gb#lH8aR4T%}A7thn=ljS>^_;k+3j
+a}eMT^h!>t~l0LjEW?nBN~i`mJ*0RR3}1%VT~0y(dm8f9m}<hR}0vUiB)AS_l-lfCk|@+Dn`sV}dSf{TaRog1v-
?+udgoZ=O$b^WjyFYQZIvlpJ$Mqv+a*=0G||?-u!9j`n_e<Wyh3(tqT{AA0*`83QN8f?>mZe4?m=VGkz=#nDD^u59753iqsT7S!-
lp#MW9kdFvNNLnJ=%SV(MLd7e3`A97>izLF7h%w1vV29C7pH78AQr`({S%I>s_#u*fbr>kLo*^C{gGIJ~r1mAm(UIUHEDb;Uw1+E
^I)pG2<agn<twe~~Og<h^07X>~g6iDKua2DPSNe{BaimZA;>g!Wj~zYI->dW;`|4PK#Ps)$FKUcIJqHZ^hl=T_d0PJ!O1R=2-
=Hksl7M~kStgoR^9)i-Cp?)<NCp?DME9+fO)$@ih`PinPhYlKa-upL(Z;Do$uNjh9tMcqAonz4SoWh+HnzG(HCv-
)d`#nYqkz3w#YQSE{Hx$!KvO!Z7@A|}&=KSX!Vy#@T^-
~vr8G$>R_NiQZ{oB#`KRN3lo5!+txDNpxdTMm5aSM1m<8+$zq!F;S@~`h>>qSEhPA;VCtuP{Rn$;uHpR+fZ6<BTC99mWg3q3e^e?
?%^dCQ={7>((Km4&jB2$_;Cb1~YMH!4t{U-
MMzx}4S4RZsgX*&uw=+jLxAJm5hK0*+1X|wWh1Qi}H=#?T?3PYNKZ5+iDnNC3BJExLasMvYgc?v;9?Uy@S`c`L~hMG@Gw!&ZuUNB
T|lNetocaoov)20wS7(JMgS&xqaRi}>^v9~{7>hh1cyi&vQh9D)+m9*?>L*nL8I_bo9S$6$o<n_?#s&77y*f*Wf3cX`U-dp#h-
f`Bc<Gnqyej7M3zWeGL1o;+w(12yCMN~G+6<QK<CeVOd7zVxR5p3Y$=wY+rAlan}q@s%G6nV8{Oml|Jio=_80!m@O1!iH6DL#?Bq
|o9&t|}ds$NXLyKX6cdvapcGH)&aEE);6Wv=a|d%z^KDU;zm!E&SqfaLt-A7|=_7oSt|X7;;m-Imeku22On^58J%@X2-
#NQq+paaiQXbqt;0L!I_M4Ev?G)U@jl}O9m(ji*o*=){NbD@A3y}?wb5-!b=B&7&tBI^^tDG^Kh|8=Kn8_c)-
3pJJAR~k$foW<dwB7eVaNRlrojz0Jio6HlJ8sYrj9~Jq(ldG<oHAZc}`<-
_{dBdOBJGr3{6KVk=~i33Qq0OJa+pj|pt|;feUBJAXph<@q4ga+KY-=LZAnV}V%0Jn213a)w8+;UIMYY>-
L@!wzj;wl+t~IviwVY2e!V&E)UaHYNQnkk{%e@8YEEbv&pRCJ-%zc6kjh)FW6bEs~ooaw`m}#-
MON>_^9GA1Is#%3qg(2Tez#*AzO*T38FZ2F%(%p8=Z`<rrK7$R1M50zU68FO>E39x~4W*?T~wrIUGg(J(y<%IGIaEr1ex_bKK4)g
ngv0G(j&`%(S%Q!;hkow-GJUV0VN%X9AR`5v(pN9%#}8;#e$kfl}L72++}yxP35!)u;odV{>bQ>$JMDr@znZE|HUUEf-+ZO-
u$Tt+NhNvp5<cy%aPAauX*7y0(x76y-YL(c~Q6bXu@V9}-|A4mWY-vRh#wF@CBn23ixg-e(Tn}KQQ5WOpQ85bB0AH6pozO5-
#Bp{@A2L|GSAL{lQjmIUekWrwS6eZGgNUdK0^|L(777d>nya#1Ks7tqcsGFL5=QzFOfj<X_5FhGJwRy2@84#<2z}CYBh44$0SFKC
xz!IiE`7DV}axYmpU+AnC>?8WYI&Mw#0OJL^HuCgg6i6uJUGS~$GfzYNB4MxeMD)nstt+^g-
0NpBfKV?_0Oqx!$@@_|eh?3?QF&waZq3yn-E7cmz>qqrq!-
hKms4)AJ<(Ip@%onK^vEo{4W7Ufdcr0nUvgGH+_u>~Zp*RDLfQ=zZdc%zCTYLn6RCvWdDh`hlR~(-7-
U<`q`o%?c;l2v&KqRp(vb;18tUjuKn-
R;Xwi6UbQc~H+piOw5xi!wGi@wfasvTAN#~(=`{ejO71_dcBAu2?NLEzh;*8wlyx1*#F?vmB5?M!@djPJdBm0v(u+UjDc6C*4-
yEWz+0M&ODh>SjYx940b${AZ+q%?Pds4r->Q29P@4hG7^Q`CKZ-
?vK)%w!8TJ;GMF0$I@74q&C#DnD05~<!wdyTV?pR;xo)`<IK^X57F6wJUfZ5d>KAm4#k;O`c@`Yth*le8xZ-}3Ofh-
6>$$i}r7v4VCTOC5JBMZV1;hiUr_j^Fa_jgH!o;w?yCx2FFs0?iJpv|Y4Hdl;1}FicH>by4y!9TdHHf`dT=63BZHQa`I=(B4^^T6
xzz?+lMhVNV@Nv2LW8HY4zRZA(gONlM2Lz38|DPX*p44#~e$mekb5T@GenjzfQ;J;%M41;|yK4u0Gh>1enHi!&KSJCP_Jkw~9u4p
+(_OXYVRQaQmZe(lJB0b8S5fht8E1&lQPPp5=yMHr{wEsQsIODTuAkI)q10+<+kx;Y5xX$}V>2pdV14^#ps!NR?-
eEqx4+solY#M(JPQZ43m&nKUE%j>Tatwd`zfr>usK9r3-@$qk0(sx03VX6N5vAZ-
!W>(z$=iNJRSog^~+!6oUo+UdsAhGA)<e+^$kDt4brW$J-w8Ld>)}3DX*Y;v#{cU}9jx1bjPR%veZ$aKKY*J9j{{dUbO2P
"""


def _legacy_source() -> str:
    source = zlib.decompress(base64.b85decode("".join(LEGACY_SOURCE_B85.split()))).decode()
    assert hashlib.sha256(source.encode()).hexdigest() == LEGACY_SHA256
    return source


def _legacy_function() -> Callable[..., Any]:
    namespace = dict(vars(route))
    exec(_legacy_code(), namespace)
    return namespace["smart_kol_search"]


@lru_cache(maxsize=1)
def _legacy_code() -> Any:
    return compile(_legacy_source(), "<smart_kol_search_pre_refactor>", "exec")


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return ("dict", tuple((_freeze(key), _freeze(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return (type(value).__name__, tuple(_freeze(item) for item in value))
    if isinstance(value, (set, frozenset)):
        return (type(value).__name__, tuple(sorted((_freeze(item) for item in value), key=repr)))
    if callable(value):
        return ("callable", getattr(value, "__name__", type(value).__name__))
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    return (type(value).__name__, repr(value))


def _exception_signature(exc: Exception | None) -> Any:
    if exc is None:
        return None
    return (
        type(exc).__module__,
        type(exc).__qualname__,
        str(exc),
        getattr(exc, "status_code", None),
        _freeze(getattr(exc, "detail", None)),
        _exception_signature(exc.__cause__),
    )


async def _capture(function: Callable[..., Any], body: dict) -> tuple[Any, Any]:
    payload = deepcopy(body)
    try:
        result = await function(payload, staff={"id": 42, "email": "offline@example.test"})
    except Exception as exc:  # noqa: BLE001 - exception parity is the contract under test
        outcome = ("exception", _exception_signature(exc))
    else:
        outcome = ("return", _freeze(result))
    return outcome, _freeze(payload)


def _fault(name: str, kind: str, seed: int) -> Exception:
    message = f"offline-fault:{name}:{seed}"
    if kind == "value":
        return ValueError(message)
    if kind == "lookup":
        return LookupError(message)
    if kind == "runtime":
        return RuntimeError(message)
    if kind == "http":
        return HTTPException(status_code=409, detail={"fault": message})
    return KeyError(message)


def _install_world(
    monkeypatch: pytest.MonkeyPatch,
    *,
    seed: int,
    fault_site: str | None,
    fault_kind: str,
    trace: list[Any],
) -> None:
    original_body_bool = route._body_bool
    original_int_or_none = route._int_or_none
    original_looks_like_url = route._looks_like_url
    original_service_unavailable = route._service_unavailable

    def stub(
        name: str,
        implementation: Callable[..., Any],
        *,
        faultable: bool = True,
    ) -> Callable[..., Any]:
        def call(*args: Any, **kwargs: Any) -> Any:
            trace.append((name, _freeze(args), _freeze(kwargs)))
            if faultable and fault_site == name:
                raise _fault(name, fault_kind, seed)
            return implementation(*args, **kwargs)

        call.__name__ = name
        return call

    async def run_threadpool(function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        trace.append(("run_in_threadpool", _freeze((function, *args)), _freeze(kwargs)))
        return function(*args, **kwargs)

    class TraceLogger:
        def exception(self, *args: Any, **kwargs: Any) -> None:
            trace.append(("logger.exception", _freeze(args), _freeze(kwargs)))

    def ensure_session(**_kwargs: Any) -> dict:
        return {"id": 1000 + seed, "status": "planned"}

    def plan_query(_query: str, *, body: dict) -> dict:
        if body.get("_clarify"):
            return {"status": "needs_clarification", "reason": f"unknown-{seed}"}
        return {
            "status": "ready",
            "search_query": f"planned query {seed}",
            "creator_quota": 10 + seed % 4,
            "reviewer_quota": 12 + seed % 3,
            "product_focus": f"focus-{seed % 5}",
            "target_persona": f"persona-{seed % 7}",
        }

    def auto_relax(_body: dict, _plan: dict, **_kwargs: Any) -> tuple[dict, dict]:
        return (
            {"platforms": ["youtube"], "market": f"market-{seed % 3}"},
            {"enabled": False, "seed": seed},
        )

    def prepare_local(**kwargs: Any) -> dict:
        return {
            "recall_filters": deepcopy(kwargs["recall_filters"]),
            "resolved_product": {"sku": f"SKU-{seed % 11}"},
            "objective": "existing_evidence" if seed % 2 else "prospective_fit",
            "local_qualification_policy": {"version": "offline-v1", "seed": seed},
        }

    def execute_local(**kwargs: Any) -> dict:
        recall_kwargs = kwargs["recall_kwargs"]
        return {
            "method": "offline_characterization",
            "items": [{"id": seed, "platform": "youtube"}],
            "buckets": {"creator": [{"id": seed}], "reviewer": []},
            "diagnostics": {"candidate_count": 1, "returned_count": 1},
            "query": {"effective": recall_kwargs["query_text"]},
        }

    def filter_platforms(result: dict, platforms: Any) -> dict:
        projected = deepcopy(result)
        projected["platform_filter"] = deepcopy(platforms)
        return projected

    def filter_market(result: dict, market: Any) -> dict:
        projected = deepcopy(result)
        projected["market_filter"] = deepcopy(market)
        return projected

    def project_local(result: dict) -> dict:
        projected = deepcopy(result)
        projected["local_projection"] = "smart_local_v1"
        return projected

    def attach_session(*, body: dict, result: dict, query_text: str, staff: dict) -> dict:
        projected = deepcopy(result)
        projected["search_session"] = {
            "id": body.get("session_id") or 2000 + seed,
            "query_text": query_text,
            "staff_id": staff.get("id"),
        }
        return projected

    def compact_result(result: dict) -> dict:
        projected = deepcopy(result)
        projected["response_projection"] = "smart_local_compact_v1"
        return projected

    def enqueue_discovery(**_kwargs: Any) -> dict:
        if seed % 2:
            return {"status": "already_queued", "job": {"id": 3000 + seed}}
        return {"status": "queued", "job_id": 3000 + seed}

    def discovery_plan(**kwargs: Any) -> dict:
        return {
            "status": "planned",
            "query_text": kwargs["query_text"],
            "platforms": deepcopy(kwargs["platforms"]),
            "limit": kwargs["limit"],
        }

    monkeypatch.setattr(route, "run_in_threadpool", run_threadpool)
    monkeypatch.setattr(route, "logger", TraceLogger())
    monkeypatch.setattr(
        route,
        "_body_bool",
        stub("body_bool", lambda body, key, **kwargs: original_body_bool(body, key, **kwargs)),
    )
    monkeypatch.setattr(route, "_int_or_none", stub("int_or_none", original_int_or_none))
    monkeypatch.setattr(route, "_looks_like_url", stub("looks_like_url", original_looks_like_url))
    monkeypatch.setattr(
        route,
        "_service_unavailable",
        stub("service_unavailable", original_service_unavailable, faultable=False),
    )
    monkeypatch.setattr(
        route,
        "_run_url_deep_crawl",
        stub(
            "url_deep_crawl",
            lambda *_args, **_kwargs: {
                "status": "queued",
                "url_type": "video" if seed % 2 else "profile",
                "search_session": {"id": seed + 1},
                "provider_calls_performed": bool(seed % 3 == 0),
                "llm_calls_performed": bool(seed % 5 == 0),
                "viltrox_fit_score_untouched": True,
            },
        ),
    )
    monkeypatch.setattr(route, "_url_response_status", stub("url_status", lambda _result: "queued"))
    monkeypatch.setattr(
        route,
        "_smart_query_type",
        stub("smart_query_type", lambda **kwargs: f"url_{kwargs['result']['url_type']}"),
    )
    monkeypatch.setattr(
        route.kol_profile_discovery,
        "resolve_market_constraint",
        stub("resolve_market", lambda _query, market: str(market or f"US-{seed % 3}")),
    )
    monkeypatch.setattr(
        route.kol_profile_discovery,
        "explicit_platforms_from_query",
        stub("discovery_query_platforms", lambda _query: ["youtube"] if seed % 4 == 0 else []),
    )
    monkeypatch.setattr(
        route.kol_search_sessions,
        "ensure_session_for_result",
        stub("ensure_session", ensure_session),
    )
    monkeypatch.setattr(
        route.kol_smart_query_planner,
        "plan_text_query_provider_free",
        stub("planner", plan_query),
    )
    monkeypatch.setattr(
        route.kol_profile_recall,
        "explicit_platforms_from_query",
        stub("profile_query_platforms", lambda _query: ["instagram"] if seed % 3 == 0 else []),
    )
    monkeypatch.setattr(
        route.kol_search_auto_relax,
        "run_auto_relax",
        stub("auto_relax", auto_relax),
    )
    monkeypatch.setattr(
        route.kol_targeted_search_runtime,
        "prepare_local_search",
        stub("prepare_local", prepare_local),
    )
    monkeypatch.setattr(
        route.kol_targeted_search_runtime,
        "execute_local_search",
        stub("execute_local", execute_local),
    )
    monkeypatch.setattr(
        route.kol_profile_discovery,
        "filter_recall_result_platforms",
        stub("filter_platforms", filter_platforms),
    )
    monkeypatch.setattr(
        route.kol_profile_discovery,
        "filter_recall_result_market",
        stub("filter_market", filter_market),
    )
    monkeypatch.setattr(
        route.kol_profile_recall_qualification,
        "project_smart_local_result",
        stub("project_local", project_local),
    )
    monkeypatch.setattr(route, "_attach_smart_recall_session", stub("attach_session", attach_session))
    monkeypatch.setattr(
        route.kol_profile_recall_response,
        "compact_smart_local_api_result",
        stub("compact_result", compact_result),
    )
    monkeypatch.setattr(
        route.kol_profile_discovery,
        "enqueue_smart_search_profile_advance",
        stub("enqueue_discovery", enqueue_discovery),
    )
    monkeypatch.setattr(
        route.kol_profile_discovery,
        "discovery_plan",
        stub("discovery_plan", discovery_plan),
    )
    monkeypatch.setattr(
        route.kol_search_sessions_online,
        "queued_online_qualification",
        stub("queued_online", lambda status: {"status": status, "target": 30}),
    )
    monkeypatch.setattr(
        route,
        "_text_response_status",
        stub(
            "text_status",
            lambda _result, discovery: str((discovery or {}).get("status") or "ready"),
        ),
    )
    monkeypatch.setattr(
        route.kol_profile_recall,
        "recall_kol_profiles",
        stub("recall_kol_profiles", lambda **_kwargs: {}),
    )


def _scenario(seed: int) -> tuple[dict, list[str]]:
    lane = seed % 6
    if lane == 0:
        return ({}, []) if seed % 12 == 0 else ({"input": "camera", "mode": "invalid"}, [])
    if lane == 1:
        mode = "auto" if seed % 4 else "url"
        sites = ["url_deep_crawl", "url_status", "smart_query_type"]
        if mode == "auto":
            sites.insert(0, "looks_like_url")
        return {"input": f"https://example.test/video/{seed}", "mode": mode}, sites

    mode = ("recall", "text", "auto")[(seed // 6) % 3]
    body: dict[str, Any] = {
        "input": f"creator workflow {seed}",
        "query_text": f"operator query {seed}" if seed % 5 == 0 else "",
        "mode": mode,
        "create_session": (True, False, "0", "yes")[(seed // 3) % 4],
        "session_id": (None, 0, str(seed + 1), "bad")[(seed // 5) % 4],
        "source": f"offline-{seed % 4}",
        "market": "US" if seed % 2 else "",
        "country": "CA" if seed % 7 == 0 else "",
        "platforms": ["youtube", "instagram"] if seed % 3 else [],
        "product_sku": f"SKU-{seed % 11}",
        "creator_quota": 8 + seed % 5,
        "reviewer_quota": 9 + seed % 4,
        "vector_weight": None if seed % 4 else 0.4,
        "type_weight": None if seed % 5 else 0.2,
        "bucket_policy": {"creator": 0.5} if seed % 2 else None,
        "result_limit": 20 + seed % 10,
        "response_projection": "smart_local_compact_v1" if seed % 4 == 0 else "",
        "api_token": "must-not-leak",
    }
    prefix = ["resolve_market", "discovery_query_platforms", "body_bool", "int_or_none"]
    if mode == "auto":
        prefix.insert(0, "looks_like_url")
    if route._body_bool(body, "create_session", default=True):
        prefix.insert(prefix.index("int_or_none"), "ensure_session")
    prefix.extend(["planner", "attach_session"])
    if lane == 2:
        body["_clarify"] = True
        return body, prefix

    local_sites = [
        "profile_query_platforms", "auto_relax", "prepare_local", "execute_local",
        "filter_platforms", "filter_market", "project_local", "attach_session",
    ]
    if body["response_projection"]:
        local_sites.append("compact_result")
    local_sites.append("text_status")
    sites = prefix[:-1] + local_sites
    if lane == 4:
        body["include_new_discovery"] = True
        sites.insert(-1, "discovery_plan")
    elif lane == 5:
        body["include_discovery"] = True
        body["execute_new_discovery"] = True
        body["online_qualification_spec"] = {
            "version": "online_net_new_30_v1",
            "target_count": "30",
        }
        sites.insert(-1, "enqueue_discovery")
        sites.insert(-1, "queued_online")
    return body, sites


def test_pre_refactor_and_split_route_match_across_1024_offline_scenarios() -> None:
    async def exercise() -> None:
        for seed in range(SCENARIO_COUNT):
            body, fault_sites = _scenario(seed)
            fault_site = None if not fault_sites or seed % 7 == 0 else fault_sites[(seed // 7) % len(fault_sites)]
            fault_kind = ("value", "lookup", "runtime", "http", "key")[(seed // 6) % 5]
            with pytest.MonkeyPatch.context() as old_patch:
                old_trace: list[Any] = []
                _install_world(
                    old_patch,
                    seed=seed,
                    fault_site=fault_site,
                    fault_kind=fault_kind,
                    trace=old_trace,
                )
                old_outcome = await _capture(_legacy_function(), body)
            with pytest.MonkeyPatch.context() as new_patch:
                new_trace: list[Any] = []
                _install_world(
                    new_patch,
                    seed=seed,
                    fault_site=fault_site,
                    fault_kind=fault_kind,
                    trace=new_trace,
                )
                new_outcome = await _capture(route.smart_kol_search, body)
            assert new_outcome == old_outcome, f"outcome drift at scenario {seed}"
            assert new_trace == old_trace, f"call-trace drift at scenario {seed}"

    asyncio.run(exercise())


def test_smart_search_split_complexity_size_and_dependency_direction_are_bounded() -> None:
    route_source = ROUTE_PATH.read_text(encoding="utf-8")
    helper_source = HELPER_PATH.read_text(encoding="utf-8")
    rows = []
    for path, source in ((ROUTE_PATH, route_source), (HELPER_PATH, helper_source)):
        rows.extend(collect_complexity({str(path): ast.parse(source)}))
    facade = next(row for row in rows if row.qualified_name == "smart_kol_search")
    smart_family = [row for row in rows if "smart" in row.qualified_name]

    assert facade.cc <= 20
    assert facade.loc <= 40
    assert max(row.cc for row in smart_family) <= 20
    assert len(route_source.splitlines()) <= 997
    assert len(helper_source.splitlines()) < 800
    assert "vkpi_kol_pool_search" not in helper_source
    assert not any(isinstance(node, (ast.Await, ast.Yield, ast.YieldFrom)) for node in ast.walk(ast.parse(helper_source)))
