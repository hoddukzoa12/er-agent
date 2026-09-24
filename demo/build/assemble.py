"""Build the demo video: slides + recorded UI frames + Magpie narration + real call audio."""
import json, subprocess, wave
from pathlib import Path

V = Path(__file__).parent
FPS, W, H = 30, 1600, 1000
LEAD, TAIL = 0.35, 0.6
scenes = json.load(open(V / "scenes.json"))
narr = json.load(open(V / "narr/durations.json"))
SEG = V / "seg"; SEG.mkdir(exist_ok=True)


def run(args):
    r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(r.stderr[-2000:])


def wav_len(p):
    with wave.open(str(p)) as w:
        return w.getnframes() / w.getframerate()


def frames_list(dirs, out):
    """ffconcat list honouring the real capture timestamps."""
    lines, total = ["ffconcat version 1.0"], 0.0
    for d in dirs:
        fr = json.load(open(V / "frames" / d / "frames.json"))
        for a, b in zip(fr, fr[1:] + [None]):
            dur = ((b["t"] - a["t"]) / 1000) if b else 0.4
            lines += [f"file '{a['p']}'", f"duration {dur:.3f}"]
            total += dur
        last = fr[-1]["p"]
    lines.append(f"file '{last}'")  # concat demuxer needs the last file repeated
    Path(out).write_text("\n".join(lines))
    return total


def call_clip():
    """Real call audio of the accepted call: AI opening → hospital reply → AI confirmation."""
    parts = sorted((V / "callaudio").glob("*.wav"))
    out = V / "seg/call_clip.wav"
    inputs = sum([["-i", str(p)] for p in parts], [])
    pads = "".join(f"[{i}:a]apad=pad_dur=0.35[a{i}];" for i in range(len(parts)))
    run([*inputs, "-filter_complex", pads + "".join(f"[a{i}]" for i in range(len(parts))) +
         f"concat=n={len(parts)}:v=0:a=1[out]", "-map", "[out]", "-ar", "48000", "-ac", "1", str(out)])
    return out, wav_len(out)


def audio_track(sid, extra=None, total=None):
    """Narration (optionally preceded by the call clip), padded to the segment length."""
    out = SEG / f"{sid}.wav"
    n = V / f"narr/{sid}.wav"
    if extra:
        run(["-i", str(extra), "-i", str(n), "-filter_complex",
             f"[0:a]apad=pad_dur=0.5[c];[1:a]aresample=48000[n];[c][n]concat=n=2:v=0:a=1,adelay={int(LEAD*1000)},apad=whole_dur={total}[o]",
             "-map", "[o]", "-ar", "48000", "-ac", "1", str(out)])
    else:
        run(["-i", str(n), "-af", f"aresample=48000,adelay={int(LEAD*1000)},apad=whole_dur={total}",
             "-ar", "48000", "-ac", "1", str(out)])
    return out


segments = []
clip, clip_len = call_clip()
for s in scenes:
    sid, nl = s["id"], narr[s["id"]]
    target = LEAD + nl + TAIL
    video = SEG / f"{sid}_v.mp4"
    if s["kind"] == "slide":
        run(["-loop", "1", "-t", f"{target:.2f}", "-i", str(V / f"slides/{sid}.png"),
             "-vf", f"scale={W}:{H},fps={FPS},format=yuv420p,fade=in:d=0.3,fade=out:st={target-0.35:.2f}:d=0.35",
             "-c:v", "libx264", "-preset", "medium", "-crf", "20", str(video)])
        aud = audio_track(sid, total=target)
    else:
        dirs = {"s3_plan": ["s3_plan", "s3_plan_x"]}.get(sid, [sid])
        lst = SEG / f"{sid}.ffconcat"
        real = frames_list(dirs, lst)
        if s.get("call_audio"):
            # frames play while the real call audio runs, then hold on the result during narration
            target = LEAD + clip_len + 0.5 + nl + TAIL
            play = clip_len + 1.5
        elif sid == "s6_event":
            target = max(target, real / 3.0)  # long agent run: at most 3x speed so steps stay readable
            play = target - 1.0
        else:
            play = target - 1.0
        speed = play / real
        vf = (f"setpts={speed:.4f}*PTS,fps={FPS},scale={W}:{H},"
              f"tpad=stop_mode=clone:stop_duration={max(0.0, target - play) + 1:.2f},trim=duration={target:.2f},setpts=PTS-STARTPTS")
        run(["-f", "concat", "-safe", "0", "-i", str(lst), "-i", str(V / f"caps/{sid}.png"),
             "-filter_complex",
             f"[0:v]{vf}[b];[b][1:v]overlay=0:{H-84}[c];[c]format=yuv420p,fade=in:d=0.3,fade=out:st={target-0.35:.2f}:d=0.35[v]",
             "-map", "[v]", "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-r", str(FPS), str(video)])
        aud = audio_track(sid, extra=clip if s.get("call_audio") else None, total=target)
    seg = SEG / f"{sid}.mp4"
    run(["-i", str(video), "-i", str(aud), "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest", str(seg)])
    segments.append(seg)
    print(f"{sid:14s} {s['kind']:5s} {target:5.1f}s")

(SEG / "all.txt").write_text("\n".join(f"file '{p}'" for p in segments))
final = V / "er-agent-demo.mp4"
run(["-f", "concat", "-safe", "0", "-i", str(SEG / "all.txt"), "-c", "copy", "-movflags", "+faststart", str(final)])
print("->", final)
