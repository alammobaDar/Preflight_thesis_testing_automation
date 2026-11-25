#!/usr/bin/env bash
# ONE-TIME media preparation (not run per trial). Produces fixed single-variant 1080p HLS + a manifest.
# Refuses if the source is shorter than --min-duration (default 100 s). Source video is user-supplied.
set -euo pipefail
usage() { echo "Usage: $0 --source FILE --outdir DIR [--bitrate-mbit 5] [--segment-s 10] [--min-duration 100] [--preset medium]"; }
SRC=""; OUT=""; BR=5; SEG=10; MIN=100; PRESET=medium
while [ $# -gt 0 ]; do case "$1" in
  --source) SRC="$2"; shift ;; --outdir) OUT="$2"; shift ;; --bitrate-mbit) BR="$2"; shift ;;
  --segment-s) SEG="$2"; shift ;; --min-duration) MIN="$2"; shift ;; --preset) PRESET="$2"; shift ;;
  -h|--help) usage; exit 0 ;; *) echo "unknown arg $1" >&2; usage; exit 2 ;; esac; shift; done
[ -n "$SRC" ] && [ -n "$OUT" ] || { usage; exit 2; }
[ -f "$SRC" ] || { echo "[!] source video not found: $SRC (source video is not available yet?)" >&2; exit 2; }
command -v ffmpeg >/dev/null && command -v ffprobe >/dev/null || { echo "[!] ffmpeg/ffprobe required" >&2; exit 3; }
[ -e "$OUT/index.m3u8" ] && { echo "[!] $OUT/index.m3u8 exists; refusing to overwrite. Choose a new --outdir." >&2; exit 4; }
DUR="$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$SRC")"
awk -v d="$DUR" -v m="$MIN" 'BEGIN{exit !(d+0 >= m+0)}' || { echo "[!] source is ${DUR}s; need >= ${MIN}s to cover the active window plus margin" >&2; exit 5; }
FR="$(ffprobe -v error -select_streams v:0 -show_entries stream=r_frame_rate -of csv=p=0 "$SRC")"
FPS="$(awk -F/ -v f="$FR" 'BEGIN{split(f,a,"/"); printf "%d", (a[2]>0? a[1]/a[2] : a[1]) + 0.5}')"
GOP=$((FPS * SEG))
mkdir -p "$OUT"
HAS_AUDIO="$(ffprobe -v error -select_streams a -show_entries stream=index -of csv=p=0 "$SRC" | head -n1)"
AUD=(-an); [ -n "$HAS_AUDIO" ] && AUD=(-c:a aac -b:a 128k -ac 2)
ffmpeg -nostdin -hide_banner -loglevel error -i "$SRC" \
  -vf "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1" \
  -c:v libx264 -preset "$PRESET" -profile:v high -pix_fmt yuv420p \
  -b:v "${BR}M" -maxrate "${BR}M" -bufsize "$((BR*2))M" -g "$GOP" -keyint_min "$GOP" -sc_threshold 0 \
  "${AUD[@]}" -f hls -hls_time "$SEG" -hls_playlist_type vod \
  -hls_segment_filename "$OUT/seg_%05d.ts" "$OUT/index.m3u8"
python3 - "$OUT" "$SRC" "$BR" "$SEG" <<'PY'
import hashlib, json, subprocess, sys, pathlib, datetime
out, src, br, seg = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3], sys.argv[4]
def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""): h.update(c)
    return h.hexdigest()
def probe(p, *args):
    return subprocess.run(["ffprobe", "-v", "error", *args, str(p)], capture_output=True, text=True).stdout.strip()
files = sorted(out.glob("*.ts")) + [out / "index.m3u8"]
v = json.loads(probe(out/"index.m3u8", "-select_streams", "v:0", "-show_entries", "stream=width,height,codec_name,r_frame_rate", "-of", "json"))
m = {"generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
     "source_file": src.name, "source_sha256": sha(src),
     "target_video_bitrate_mbit": float(br), "segment_s": float(seg), "adaptive": False, "variants": 1,
     "ffmpeg_version": subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True).stdout.splitlines()[0],
     "video_stream": v["streams"][0], "segment_count": len(files) - 1,
     "total_duration_s": float(probe(out/"index.m3u8", "-show_entries", "format=duration", "-of", "csv=p=0")),
     "files": {f.name: {"sha256": sha(f), "bytes": f.stat().st_size} for f in files}}
(out / "media.manifest.json").write_text(json.dumps(m, indent=2) + "\n")
print("segments:", m["segment_count"], "duration:", m["total_duration_s"], "s", "resolution:", v["streams"][0]["width"], "x", v["streams"][0]["height"])
PY
echo "[+] HLS ready in $OUT. Next: copy to the EC2 HLS root and verify playback over HTTPS (preflight does not prove playback)."
