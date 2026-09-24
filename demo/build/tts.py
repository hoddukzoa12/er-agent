import json, re, wave, sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[2] / "src"))
from er_agent import voice
V = "Magpie-Multilingual.KO-KR.Louise"
GAP = b"\x00\x00" * int(voice.RATE * 0.28)
out = {}
for sc in json.load(open("scenes.json")):
    pcm = b""
    for sent in [x for x in re.split(r"(?<=[.!?])\s+", sc["narr"]) if x.strip()]:
        pcm += voice.synthesize(sent, voice=V) + GAP
    voice.save_wav(__import__("pathlib").Path(f"narr/{sc['id']}.wav"), pcm)
    out[sc["id"]] = round(len(pcm) / 2 / voice.RATE, 2)
    print(sc["id"], out[sc["id"]], "s")
json.dump(out, open("narr/durations.json", "w"))
print("total", round(sum(out.values()), 1), "s")
