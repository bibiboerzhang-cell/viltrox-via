#!/usr/bin/env python3
"""Prepare a Veo batch job from the Viltrox Reborn prompt-pack text.

This script is intentionally offline-only. It reads extracted PDF text, parses
the 10 chapter / 80 segment plan, and writes prompts plus a manifest that the
runner can execute later.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path


MASTER_SKILL = """Create a cinematic surreal comedy video clip for a 10-minute AI short film.

Main character consistency:
A photorealistic Viltrox EPIC 35mm T2.0 1.33X full-frame anamorphic cine lens as the hero. The lens has a moon-white / silver metal barrel, a large black convex front glass element, silver knurled focus and iris gear rings, a PL-style rear mount, clear big "35" focal length marking, small VILTROX logo, "T2.0 1.33X" marking, professional cinema lens proportions. It is not a DSLR zoom lens, not a black plastic lens, not a camera body.

Visual style:
Surreal absurd Chinese comedy mixed with high-end cinematic commercial look. Photorealistic objects, controlled chaos, anamorphic widescreen feeling, 2.39:1 letterboxed composition inside a 16:9 frame, blue horizontal anamorphic flares when bright lights appear, oval bokeh, clean modern anamorphic image, retro pure color, high contrast but not oversharpened, minimal focus breathing, practical lighting, dramatic shadows.

Camera language:
Use precise shot framing and camera movement. Fast comedic timing, absurd props, dynamic push-ins, whip pans, low-angle hero shots, macro shots when needed, rack focus, handheld panic when characters lose control.

Post-production audio mode:
Create a video-only clip for later Mandarin voiceover. Do not generate spoken dialogue, narration, music, lyrics, or prominent sound effects. If the scene description contains shouting or quoted dialogue, express it visually through object motion, aperture movement, reflections, blue flare pulses, and character blocking only. The final playful Mandarin voiceover and subtitles will be added in post-production.

Continuity:
The hero lens must remain visually consistent across all clips. Do not change the lens into a human. It may express emotions through aperture blades, reflections in glass, blue flares, tiny mechanical vibrations, and surreal floating subtitles."""


NEGATIVE_CONSTRAINTS = """Negative constraints:
No generic black DSLR lens, no Canon red ring, no Sony GM lens, no incorrect camera brand, no melted unreadable lens body, no wrong focal length on the hero, no fisheye action-camera look, no cartoon style unless explicitly stated, no gore, no horror realism, no random extra limbs, no fake UI text covering the whole image, no messy misspelled giant logos, no human face replacing the lens glass, no product deformation, no toy lens, no telescope, no coffee mug lens, no generated speech audio, no audible dialogue."""


REALITY_LOCK = """Real-world product and optical checks:
- Hero: moon-white / silver Viltrox EPIC 35mm T2.0 1.33X PL full-frame anamorphic cine lens, large black convex front element, silver knurled focus and iris gear rings, PL-style rear mount, visible 35 / T2.0 / 1.33X markings.
- Maintain a de-squeezed 2.39:1 widescreen feeling inside the 16:9 frame, with blue horizontal anamorphic flare and oval bokeh when motivated by light sources.
- Keep all real props physically coherent: lens barrel remains rigid metal and glass, mounts do not melt, markings stay small and plausible, no impossible body deformation."""


CHAPTER_LENS_LOCKS = {
    "C01": "35mm T2.0 / 1.33X / full-frame / Blue Flare hero origin. Keep the hero lens consistent.",
    "C02": "35mm T2.0 / 1.33X / full-frame / Blue Flare hero in a living film-set world.",
    "C03": "Seven-lens council: 25, 35, 50, 65 Macro, 75, 100, 135mm Viltrox EPIC 1.33X lenses.",
    "C04": "25mm T2.0 / 1.33X / full-frame wide lens. Wide perspective, not fisheye, not GoPro.",
    "C05": "50mm T2.0 / 1.33X / full-frame standard lens. Stable middle perspective and symmetric blocking.",
    "C06": "65mm T2.8 Macro / 1.33X / 1:4 macro lens. Do not label it T2.0.",
    "C07": "75mm T2.0 / 1.33X / full-frame portrait lens. Shallow depth, oval bokeh, real skin detail.",
    "C08": "100mm T2.0 / 1.33X / full-frame telephoto lens. Distance compression and rooftop duel logic.",
    "C09": "135mm T2.4 / 1.33X / full-frame telephoto lens. Do not label it T2.0; clean low-flare temple.",
    "C10": "Seven-lens finale. Keep all focal lengths correct: 25, 35, 50, 65, 75, 100, 135.",
}


def clean_pdf_text(text: str) -> str:
    replacements = {
        "重生之我变成了 Viltrox 镜头 - AI 视频投喂 PDF": "",
        "重生之我变成了 Viltrox 镜头": "",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def clean_segment_description(desc: str) -> str:
    desc = " ".join(desc.split())
    # PDF extraction sometimes inserts spaces inside Chinese words at line
    # wraps. Keep English technical terms spaced, but repair Chinese runs.
    desc = re.sub(r"(?<=[\u4e00-\u9fff，。：！？“”《》、])\s+(?=[\u4e00-\u9fff，。：！？“”《》、])", "", desc)
    return desc.strip()


def parse_segments(text: str) -> tuple[dict[str, str], list[dict[str, object]]]:
    text = clean_pdf_text(text)
    section_matches = list(
        re.finditer(r"3\. 80 段分镜生成表：10 分钟总计(?P<body>.*?)(?:\n4\. 真实镜头联网对照规则|\Z)", text, re.S)
    )
    if not section_matches:
        raise ValueError("Could not locate section 3 segment table in source text.")

    # The PDF includes a table of contents with the same section title. The
    # final match is the real body that contains C01-C10.
    body = section_matches[-1].group("body")
    chapter_matches = list(re.finditer(r"(?m)^(C\d{2})：(.+)$", body))
    if not chapter_matches:
        raise ValueError("Could not locate chapter headers such as C01：...")

    chapters: dict[str, str] = {}
    segments: list[dict[str, object]] = []
    for index, chapter_match in enumerate(chapter_matches):
        chapter_id = chapter_match.group(1)
        chapter_title = chapter_match.group(2).strip()
        chapters[chapter_id] = chapter_title
        start = chapter_match.end()
        end = chapter_matches[index + 1].start() if index + 1 < len(chapter_matches) else len(body)
        chapter_body = body[start:end]

        segment_matches = list(
            re.finditer(
                r"(?s)(S\d{2}) - (?P<meta>[^：]+)：(?P<desc>.*?)(?=\nS\d{2} - |\n文件名示例：|\Z)",
                chapter_body,
            )
        )
        if len(segment_matches) != 8:
            raise ValueError(f"{chapter_id} expected 8 segments, found {len(segment_matches)}")

        for segment_match in segment_matches:
            segment_id = segment_match.group(1)
            meta = " ".join(segment_match.group("meta").split())
            desc = clean_segment_description(segment_match.group("desc"))
            generated_duration = 4 if segment_id == "S08" else 8
            use_duration = 4 if segment_id == "S08" else 8
            segments.append(
                {
                    "id": f"{chapter_id}_{segment_id}",
                    "chapter_id": chapter_id,
                    "chapter_title": chapter_title,
                    "segment_id": segment_id,
                    "source_meta": meta,
                    "description": desc,
                    "generated_duration_seconds": generated_duration,
                    "use_duration_seconds": use_duration,
                    "default_output_name": f"{chapter_id}_{segment_id}_{use_duration}s.mp4",
                    "lens_lock": CHAPTER_LENS_LOCKS.get(chapter_id, CHAPTER_LENS_LOCKS["C10"]),
                }
            )

    if len(segments) != 80:
        raise ValueError(f"Expected 80 total segments, found {len(segments)}")
    return chapters, segments


def build_prompt(segment: dict[str, object]) -> str:
    generated_duration = segment["generated_duration_seconds"]
    visual_action = str(segment["description"])
    visual_action = re.sub(r"：“[^”]+”", "，以夸张动作、光线脉冲和镜身震动表达情绪", visual_action)
    visual_action = (
        visual_action.replace("尖叫", "剧烈震动")
        .replace("大喊", "快速冲入画面")
        .replace("怒吼", "强烈压迫登场")
        .replace("低声说", "通过慢速光圈动作表达")
        .replace("说：", "以姿态表达：")
        .replace("说", "以姿态表达")
    )
    return "\n\n".join(
        [
            MASTER_SKILL,
            "Clip instruction:",
            (
                f"{segment['id']}, {generated_duration} seconds.\n"
                f"Chapter: {segment['chapter_id']} - {segment['chapter_title']}.\n"
                f"Action: {visual_action}"
            ),
            "Dialogue handling:\nTreat any quoted Chinese dialogue in the action text as a visual beat only. Do not generate audible speech for it.",
            "Lens-specific lock:\n" + str(segment["lens_lock"]),
            REALITY_LOCK,
            NEGATIVE_CONSTRAINTS,
        ]
    )


def write_job(source_text: Path, output_dir: Path) -> Path:
    text = source_text.read_text(encoding="utf-8")
    chapters, segments = parse_segments(text)
    output_dir.mkdir(parents=True, exist_ok=True)
    prompts_dir = output_dir / "prompts"
    prompts_dir.mkdir(exist_ok=True)

    for segment in segments:
        chapter_prompt_dir = prompts_dir / str(segment["chapter_id"])
        chapter_prompt_dir.mkdir(exist_ok=True)
        prompt = build_prompt(segment)
        prompt_path = chapter_prompt_dir / f"{segment['id']}.txt"
        prompt_path.write_text(prompt, encoding="utf-8")
        segment["prompt_path"] = str(prompt_path.relative_to(output_dir))

    manifest = {
        "project": "viltrox_reborn_gemini_veo",
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
        "source_text": str(source_text),
        "default_model": "veo-3.1-fast-generate-preview",
        "default_aspect_ratio": "16:9",
        "default_resolution": "720p",
        "default_generate_audio": False,
        "default_s8_mode": "direct4",
        "chapters": chapters,
        "segments": segments,
        "estimated_generated_seconds_direct4": sum(int(s["generated_duration_seconds"]) for s in segments),
        "estimated_final_seconds": sum(int(s["use_duration_seconds"]) for s in segments),
        "cost_notes": {
            "veo_3_1_fast_720p_usd_per_second": 0.10,
            "direct4_fast_720p_estimate_usd": round(sum(int(s["generated_duration_seconds"]) for s in segments) * 0.10, 2),
            "trim8_fast_720p_estimate_usd": 64.0,
            "retries_increase_cost": True,
        },
    }

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    readme = f"""# Viltrox Reborn Veo Batch Job

Source text: `{source_text}`

Prepared:
- Chapters: {len(chapters)}
- Segments: {len(segments)}
- Final duration: {manifest['estimated_final_seconds']} seconds
- Generated seconds with direct 4s S08: {manifest['estimated_generated_seconds_direct4']} seconds
- Default model: `{manifest['default_model']}`
- Default output: 720p / 16:9 / post-production Mandarin voiceover

Cost checkpoint:
- Veo 3.1 Fast 720p at $0.10/s: about $60.00 for direct 4-second S08 generation.
- If S08 is generated as 8 seconds and trimmed to 4 seconds: about $64.00.
- Failed generations, rejected clips, or retries add cost.

Dry-run:
```bash
python3 tools/veo_batch/run_veo_manifest.py --manifest "{manifest_path}" --chapters C01
```

Execute one chapter:
```bash
python3 tools/veo_batch/run_veo_manifest.py --manifest "{manifest_path}" --chapters C01 --execute
```

Execute all chapters:
```bash
python3 tools/veo_batch/run_veo_manifest.py --manifest "{manifest_path}" --execute
```

If direct network access fails, retry with your local proxy environment:
```bash
HTTPS_PROXY=http://127.0.0.1:30001 HTTP_PROXY=http://127.0.0.1:30001 \\
python3 tools/veo_batch/run_veo_manifest.py --manifest "{manifest_path}" --chapters C01 --execute
```
"""
    (output_dir / "README.md").write_text(readme, encoding="utf-8")
    return manifest_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-text", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    manifest_path = write_job(args.source_text, args.output_dir)
    print(manifest_path)


if __name__ == "__main__":
    main()
