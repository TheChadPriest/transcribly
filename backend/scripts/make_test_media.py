"""Generate the test media in tests/media with known spoken content.

Speech is synthesized locally (Windows SAPI voices via pyttsx3), so the files are free to use
and redistribute. Run on Windows: python scripts/make_test_media.py
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import wave
from pathlib import Path

import imageio_ffmpeg
import numpy as np
import pyttsx3

OUT = Path(__file__).resolve().parent.parent / "tests" / "media"
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

CLEAN = ("Photosynthesis is the process plants use to turn sunlight into chemical energy. "
         "It takes place inside the chloroplast. The light reactions produce oxygen, "
         "and the Calvin cycle uses carbon dioxide to build sugar.")

LECTURE = [
    "Welcome to this short lecture on the water cycle. Today we will look at how water moves between "
    "the oceans, the atmosphere and the land, and why this matters for climate and for people.",
    "The first stage is evaporation. Evaporation is the process in which liquid water turns into water vapor "
    "when it is heated by the sun. Most evaporation happens over the oceans, which cover about seventy one "
    "percent of the surface of the Earth. Plants also release water vapor through their leaves, and this is "
    "called transpiration.",
    "The second stage is condensation. Condensation is when water vapor cools and becomes tiny droplets of "
    "liquid water. These droplets gather around small particles of dust and form clouds. Warm air rises, "
    "expands, and cools, and that cooling is what makes condensation possible.",
    "The third stage is precipitation. Precipitation refers to any water that falls from clouds to the ground, "
    "such as rain, snow, sleet or hail. It happens when droplets in a cloud combine and become too heavy to "
    "stay in the air.",
    "The fourth stage is collection. Water collects in oceans, lakes and rivers, and some of it soaks into the "
    "ground to become groundwater. Groundwater can stay underground for thousands of years before it returns "
    "to the surface.",
    "Human activity is changing the water cycle. A warmer climate increases evaporation, which can lead to "
    "heavier rainfall in some regions and longer droughts in others. Cutting down forests also reduces "
    "transpiration and can make local weather drier.",
    "In conclusion, the water cycle is a continuous loop of evaporation, condensation, precipitation and "
    "collection. The key point to remember is that the total amount of water on Earth stays almost the same, "
    "but where it is stored keeps changing.",
]

NOISY = ("The mitochondria is often described as the powerhouse of the cell. "
         "It produces energy for the cell through a process called cellular respiration.")


def say(text: str, path: Path, voice: int = 0, rate: int = 165) -> np.ndarray:
    engine = pyttsx3.init()
    engine.setProperty("voice", engine.getProperty("voices")[voice].id)
    engine.setProperty("rate", rate)
    engine.save_to_file(text, str(path))
    engine.runAndWait()
    engine.stop()
    return load(path)


def load(path: Path) -> np.ndarray:
    tmp = path.with_suffix(".16k.wav")
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(path), "-ac", "1", "-ar", "16000", str(tmp)], check=True)
    with wave.open(str(tmp)) as w:
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    tmp.unlink()
    return data


def write_wav(path: Path, x: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(16000 * seconds), dtype=np.float32)


def encode(src: Path, dst: Path, *args: str) -> None:
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-i", str(src), *args, str(dst)], check=True)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        clean = say(CLEAN, tmp / "clean_raw.wav")
        write_wav(OUT / "clean.wav", np.concatenate([silence(0.5), clean, silence(0.5)]))

        parts = []
        for i, para in enumerate(LECTURE):
            parts += [say(para, tmp / f"p{i}.wav", voice=i % 3), silence(1.5)]
        lecture = np.concatenate(parts)
        write_wav(tmp / "lecture.wav", lecture)
        encode(tmp / "lecture.wav", OUT / "lecture.m4a", "-c:a", "aac", "-b:a", "48k")

        multi = np.concatenate([parts[0], silence(2), parts[2], silence(2), parts[4]])
        write_wav(tmp / "multi.wav", multi)
        encode(tmp / "multi.wav", OUT / "multi_segment.mp3", "-c:a", "libmp3lame", "-b:a", "48k")

        write_wav(OUT / "silence_gaps.wav", np.concatenate([silence(8), clean, silence(12), clean, silence(6)]))

        speech = say(NOISY, tmp / "noisy_raw.wav")
        rng = np.random.default_rng(7)
        noise = rng.normal(0, 1, len(speech)).astype(np.float32)
        snr_db = 10
        noise *= np.sqrt((speech ** 2).mean() / (noise ** 2).mean() / 10 ** (snr_db / 10))
        write_wav(OUT / "noisy.wav", speech + noise)

        # video with audio track, and a video with no audio track
        encode(OUT / "clean.wav", OUT / "with_video.mp4", "-f", "lavfi", "-i", "color=c=gray:s=320x240:r=10",
               "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "48k")
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=320x240:r=10:d=3",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(OUT / "no_audio.mp4")], check=True)

    (OUT / "corrupt.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + np.random.default_rng(1).bytes(40000))
    (OUT / "expected.json").write_text(json.dumps({"clean": CLEAN, "lecture": LECTURE, "noisy": NOISY}, indent=2))
    print("written:", sorted(p.name for p in OUT.iterdir()))


if __name__ == "__main__":
    main()
