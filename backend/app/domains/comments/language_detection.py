"""Best-effort comment language detection with bounded operational logging."""

from __future__ import annotations

from typing import Any


def detect_comment_language(text: str, *, logger: Any) -> str | None:
    """Prefer langdetect, then fall back to the deterministic local heuristic."""
    normalized = str(text or "").strip()
    if len(normalized) < 3:
        return None
    try:
        from langdetect import DetectorFactory, detect  # type: ignore
        from langdetect.lang_detect_exception import ErrorCode, LangDetectException  # type: ignore
    except ModuleNotFoundError as exc:
        # The package is optional. Missing transitive dependencies are not.
        if exc.name != "langdetect":
            logger.warning("langdetect 依赖导入异常,退下一级语言检测(best-effort)", exc_info=True)
    except ImportError:
        logger.warning("langdetect 接口导入异常,退下一级语言检测(best-effort)", exc_info=True)
    else:
        try:
            DetectorFactory.seed = 0
            code = str(detect(normalized) or "").strip().lower()
            if code and code != "unknown":
                return code[:10]
        except LangDetectException as exc:
            if exc.get_code() == ErrorCode.CantDetectError:
                logger.debug("langdetect 无足够特征,退下一级语言检测(best-effort)")
            else:
                logger.warning(
                    "langdetect 内部状态异常(code=%s),退下一级语言检测(best-effort)",
                    exc.get_code(),
                    exc_info=True,
                )
        except Exception:
            logger.warning("langdetect 非预期异常,退下一级语言检测(best-effort)", exc_info=True)
    try:
        from app.domains.kol.audience_language import detect_lang

        code = detect_lang(normalized)
        return code if code and code != "und" else None
    except Exception:
        logger.warning("评论语言启发式回退异常,返回未知语言", exc_info=True)
        return None
