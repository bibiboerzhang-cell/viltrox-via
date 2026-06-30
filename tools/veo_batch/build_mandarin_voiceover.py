#!/usr/bin/env python3
"""Build playful Mandarin voiceover and subtitles for the Viltrox Reborn film."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path


VOICEOVER_LINES = {
    "C01_S01": "凌晨三点，甲方又开始了。",
    "C01_S02": "灵魂一闪，硬盘都吓醒。",
    "C01_S03": "再睁眼，我成镜头了。",
    "C01_S04": "流水线正在开器材早会。",
    "C01_S05": "MTF 曲线，直接成龙。",
    "C01_S06": "三十五毫米，正式发光。",
    "C01_S07": "反派上线，画幅变窄。",
    "C01_S08": "玻璃命，开局。",
    "C02_S01": "上机第一天，相机先跑了。",
    "C02_S02": "三脚架也有自己的野心。",
    "C02_S03": "人类很认真，器材很失控。",
    "C02_S04": "劣质 UV 镜，坏气氛担当。",
    "C02_S05": "它说，电影感不许存在。",
    "C02_S06": "蓝色耀斑，开扫。",
    "C02_S07": "七镜圣图，掉出来了。",
    "C02_S08": "任务解锁，拉宽宇宙。",
    "C03_S01": "地下十八层，镜头开会。",
    "C03_S02": "二十五毫米：别裁我边边。",
    "C03_S03": "五十毫米，开始算账。",
    "C03_S04": "一粒灰，也有史诗。",
    "C03_S05": "七十五一来，全场变帅。",
    "C03_S06": "远处那两位，不太好惹。",
    "C03_S07": "横向像素，被偷走了。",
    "C03_S08": "城市太窄，冲！",
    "C04_S01": "广角一开，城市装不下。",
    "C04_S02": "路人追着自己的边缘跑。",
    "C04_S03": "遮光斗里，出租车漂移。",
    "C04_S04": "一个像素都不能丢。",
    "C04_S05": "猫咖，火锅，月亮，同框。",
    "C04_S06": "广角不是贪，是留证据。",
    "C04_S07": "第一枚镜魂，到账。",
    "C04_S08": "账本来了，麻烦也来了。",
    "C05_S01": "标准王国，连狗都三分法。",
    "C05_S02": "罪名：耀斑未经审批。",
    "C05_S03": "全世界，同时构图合规。",
    "C05_S04": "废片里，真心没跑焦。",
    "C05_S05": "账本着火，标准融化。",
    "C05_S06": "不标准，也能是电影。",
    "C05_S07": "第二枚镜魂，归位。",
    "C05_S08": "小灰尘，大事件。",
    "C06_S01": "欢迎来到灰尘宇宙。",
    "C06_S02": "死像素夜店，今晚营业。",
    "C06_S03": "F 十六黑帮，追上来了。",
    "C06_S04": "霉菌龙，喷雾登场。",
    "C06_S05": "核心竟是多年老污点。",
    "C06_S06": "微型气吹，风暴启动。",
    "C06_S07": "大场面，从灰开始。",
    "C06_S08": "美貌警报，七十五来了。",
    "C07_S01": "这焦外，连垃圾桶都深情。",
    "C07_S02": "背景虚化，主体升华。",
    "C07_S03": "主角也帅到有点离谱。",
    "C07_S04": "太好看，也会丢真实。",
    "C07_S05": "光斑神殿，浪漫超标。",
    "C07_S06": "人像，是看见自己。",
    "C07_S07": "泪珠焦外，镜魂交出。",
    "C07_S08": "远处红点，锁定灵魂。",
    "C08_S01": "一百毫米，楼顶埋伏。",
    "C08_S02": "每次对焦，都像拔刀。",
    "C08_S03": "空间压扁，真相变远。",
    "C08_S04": "靠近和旁观，都别装懂。",
    "C08_S05": "齿轮开转，决斗开始。",
    "C08_S06": "距离里，也有温柔。",
    "C08_S07": "第五枚镜魂，带回呼吸。",
    "C08_S08": "天空被裁，危险升级。",
    "C09_S01": "无眩光神庙，干净得吓人。",
    "C09_S02": "老祖一句：疯要有结构。",
    "C09_S03": "反派藏在导出窗口。",
    "C09_S04": "平台推荐，正在吃画面。",
    "C09_S05": "六枚镜魂，排成符号。",
    "C09_S06": "最后一枚，原来是我。",
    "C09_S07": "压缩率大帝，降临。",
    "C09_S08": "反挤压，启动。",
    "C10_S01": "最终战，在时间线银河。",
    "C10_S02": "它说，电影感没转化率。",
    "C10_S03": "广角、标准、微距，合流。",
    "C10_S04": "主体、距离、结局，归位。",
    "C10_S05": "三十五毫米，把故事连上。",
    "C10_S06": "被裁掉的，都回来了。",
    "C10_S07": "反派太宽，无法投放。",
    "C10_S08": "横屏观看，第二季见。",
}


def run(cmd: list[str]) -> None:
    print("+ " + " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def ffmpeg_path() -> str:
    for candidate in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "ffmpeg"):
        if shutil.which(candidate) or Path(candidate).exists():
            return candidate
    raise SystemExit("ffmpeg not found")


def ass_time(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centis = int(round((seconds - int(seconds)) * 100))
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def ass_escape(text: str) -> str:
    return text.replace("{", "").replace("}", "").replace("\n", "\\N")


def write_subtitles(manifest: dict, output_path: Path) -> None:
    events: list[str] = []
    cursor = 0.0
    for segment in manifest["segments"]:
        duration = float(segment["use_duration_seconds"])
        line = VOICEOVER_LINES.get(segment["id"], str(segment["description"])[:18])
        start = cursor + 0.25
        end = min(cursor + duration - 0.15, start + max(1.5, duration - 0.6))
        events.append(
            f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,{ass_escape(line)}"
        )
        cursor += duration

    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,PingFang SC,42,&H00FFFFFF,&H000000FF,&H99000000,&H66000000,1,0,0,0,100,100,0,0,1,4,1,2,90,90,54,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    output_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--voice", default="Tingting")
    parser.add_argument("--rate", default=205, type=int)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    output_dir = args.output_dir or args.manifest.parent / "voiceover"
    output_dir.mkdir(parents=True, exist_ok=True)
    audio_dir = output_dir / "segments"
    audio_dir.mkdir(exist_ok=True)
    ffmpeg = ffmpeg_path()

    concat_lines: list[str] = []
    timing_rows: list[dict[str, object]] = []
    cursor = 0.0

    for segment in manifest["segments"]:
        clip_id = segment["id"]
        duration = float(segment["use_duration_seconds"])
        line = VOICEOVER_LINES.get(clip_id, str(segment["description"])[:18])
        raw_aiff = audio_dir / f"{clip_id}.aiff"
        padded_m4a = audio_dir / f"{clip_id}_{int(duration)}s.m4a"
        run(["say", "-v", args.voice, "-r", str(args.rate), "-o", str(raw_aiff), "--", line])
        run(
            [
                ffmpeg,
                "-y",
                "-i",
                str(raw_aiff),
                "-af",
                f"apad,atrim=0:{duration}",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                str(padded_m4a),
            ]
        )
        concat_lines.append(f"file '{padded_m4a.resolve().as_posix()}'\n")
        timing_rows.append({"id": clip_id, "start": cursor, "end": cursor + duration, "text": line})
        cursor += duration

    concat_path = output_dir / "voiceover.concat.txt"
    concat_path.write_text("".join(concat_lines), encoding="utf-8")
    voiceover_path = output_dir / "viltrox_reborn_mandarin_voiceover.m4a"
    run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_path), "-c", "copy", str(voiceover_path)])

    write_subtitles(manifest, output_dir / "viltrox_reborn_mandarin_subtitles.ass")
    (output_dir / "voiceover_lines.json").write_text(json.dumps(timing_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(voiceover_path)


if __name__ == "__main__":
    main()
