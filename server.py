import asyncio
import json
import logging
import subprocess
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
import os
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
import sqlite3
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from heartbeat import router as hooter_router, init_hooter_db

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)
load_dotenv()  # Load variables from env.

BASE_DIR = Path(__file__).resolve().parent  # Server directory.
with (BASE_DIR / "config.json").open("r", encoding="utf-8") as f:
    CFG = json.load(f)

TRACKS = CFG["tracks"]
ZONES = CFG["zones"]
ICE = CFG["icecast"]
AUDIO_DIR = (BASE_DIR / CFG["audio_dir"]).resolve()
# FFMPEG_BIN = BASE_DIR / "ffmpeg-8.1" / "bin" / "ffmpeg.exe"
FFMPEG_BIN = BASE_DIR / os.getenv("FFMPEG_PATH","ffmpeg-8.1/bin/ffmpeg.exe")

# SAMPLE_RATE = 22050
SAMPLE_RATE = int(os.getenv("SAMPLE_RATE", 22050))
CHANNELS = 1
PCM_CHUNK_SIZE = 4096
SILENCE_INTERVAL = 0.1
# Keep the mount alive when idle.
SILENCE_CHUNK = b"\x00" * int(SAMPLE_RATE * CHANNELS * 2 * SILENCE_INTERVAL)

ffmpeg_procs = {}
ffmpeg_errors = {}
audio_queues = {}
audio_tasks = {}
silence_tasks = {}

DB_PATH = BASE_DIR / "hooter.db"

def icecast_url(mount: str) -> str:
    # icecast://source:password@127.0.0.1:8000/live
    return (f"icecast://source:{ICE['password']}"f"@{ICE['host']}:{ICE['port']}{mount}"
    )


def tail_errors(zone_name: str) -> str:
    return "\n".join(ffmpeg_errors.get(zone_name, ()))


def capture_ffmpeg_logs(zone_name: str, proc: subprocess.Popen) -> None:
    if proc.stderr is None:
        return

    for raw_line in proc.stderr:
        line = raw_line.decode("utf-8", errors="replace").rstrip()
        if not line:
            continue

        ffmpeg_errors[zone_name].append(line)
        if "error" in line.lower() or "failed" in line.lower():
            log.warning("FFmpeg[%s]: %s", zone_name, line)


def start_output_ffmpeg(zone_name: str, mount: str) -> subprocess.Popen:
    ffmpeg_errors[zone_name] = deque(maxlen=30)

    # Persistent encoder for one Icecast mount.
    cmd = [str(FFMPEG_BIN),"-hide_banner","-loglevel","warning","-f","s16le","-ar",str(SAMPLE_RATE),"-ac",str(CHANNELS),"-i","pipe:0","-vn","-c:a",
        "libmp3lame","-b:a","64k","-content_type","audio/mpeg","-f","mp3",icecast_url(mount),
    ]

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        bufsize=0,
    )

    ffmpeg_procs[zone_name] = proc
    asyncio.get_running_loop().run_in_executor(
        None,
        capture_ffmpeg_logs,
        zone_name,
        proc,
    )

    return proc


def ensure_output_running(zone_name: str) -> subprocess.Popen:
    proc = ffmpeg_procs[zone_name]
    if proc.poll() is not None:
        raise RuntimeError(
            f"FFmpeg output for {zone_name} stopped with code {proc.returncode}.\n"
            f"{tail_errors(zone_name) or 'No FFmpeg stderr captured.'}"
        )
    if proc.stdin is None:
        raise RuntimeError(f"FFmpeg stdin unavailable for {zone_name}")
    return proc


async def write_pcm(zone_name: str, payload: bytes) -> None:
    proc = ensure_output_running(zone_name)
    proc.stdin.write(payload)
    proc.stdin.flush()


async def silence_worker(zone_name: str) -> None:
    queue = audio_queues[zone_name]

    while True:
        try:
            # Send silence only when queue is empty.
            if queue.empty():
                await write_pcm(zone_name, SILENCE_CHUNK)
            await asyncio.sleep(SILENCE_INTERVAL)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("Silence worker stopped for %s: %s", zone_name, e)
            break


def pcm_stream_from_file(audio_path: Path):
    # Convert source audio to the stream PCM format.
    cmd = [str(FFMPEG_BIN),"-hide_banner","-loglevel","error","-i",str(audio_path),"-vn","-f","s16le","-acodec","pcm_s16le","-ac",str(CHANNELS),"-ar",str(SAMPLE_RATE),"pipe:1",]
    proc = subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,bufsize=0,)
    return proc


async def audio_worker(zone_name: str) -> None:
    queue = audio_queues[zone_name]

    while True:
        audio_path = Path(await queue.get())

        try:
            if not audio_path.is_file():
                raise FileNotFoundError(f"Missing file: {audio_path}")

            ensure_output_running(zone_name)
            log.info("Playing %s on %s", audio_path.name, zone_name)

            decoder = pcm_stream_from_file(audio_path)
            if decoder.stdout is None:
                raise RuntimeError("Decoder stdout unavailable")

            # Push decoded audio from the *.mp3 file into the live stream.
            while True:
                chunk = await asyncio.to_thread(decoder.stdout.read, PCM_CHUNK_SIZE)
                if not chunk:
                    break
                await write_pcm(zone_name, chunk)

            stderr = b""
            if decoder.stderr is not None:
                stderr = await asyncio.to_thread(decoder.stderr.read)

            rc = await asyncio.to_thread(decoder.wait)
            if rc != 0:
                raise RuntimeError(
                    stderr.decode("utf-8", errors="replace") or
                    f"Decoder failed with code {rc}"
                )

            log.info("Finished %s on %s", audio_path.name, zone_name)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.error("Audio worker error on %s: %s", zone_name, e)
        finally:
            queue.task_done()


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        for zone_name, mount in ZONES.items():
            audio_queues[zone_name] = asyncio.Queue()
            start_output_ffmpeg(zone_name, mount)

        await asyncio.sleep(0.5)

        for zone_name, proc in ffmpeg_procs.items():
            # Stop startup if a mount could not connect.
            if proc.poll() is not None:
                raise RuntimeError(
                    f"FFmpeg exited for {zone_name} during startup.\n"
                    f"{tail_errors(zone_name) or 'No FFmpeg stderr captured.'}"
                )
        init_hooter_db()
        for zone_name in ZONES:
            audio_tasks[zone_name] = asyncio.create_task(audio_worker(zone_name))
            silence_tasks[zone_name] = asyncio.create_task(silence_worker(zone_name))
            log.info("Persistent FFmpeg started for %s", zone_name)

        yield
    finally:
        for task in list(audio_tasks.values()) + list(silence_tasks.values()):
            task.cancel()

        for task in list(audio_tasks.values()) + list(silence_tasks.values()):
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        for proc in ffmpeg_procs.values():
            try:
                proc.terminate()
            except Exception:
                pass


app = FastAPI(lifespan=lifespan)
app.include_router(hooter_router)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

@app.post("/alert/{track_id}")
async def alert(track_id: str):
    if track_id not in TRACKS:
        raise HTTPException(404, "Unknown track")

    track = TRACKS[track_id]
    zone = track["zone"]
    audio_path = AUDIO_DIR / track["audio"]

    if not audio_path.is_file():
        raise HTTPException(500, f"Missing file: {audio_path}")

    if zone == "all":
        # Queue the same file for all zones.
        for z in ZONES:
            await audio_queues[z].put(str(audio_path))
    else:
        if zone not in ZONES:
            raise HTTPException(500, f"Unknown zone in config: {zone}")
        await audio_queues[zone].put(str(audio_path))

    return {
        "status": "queued",
        "track": track_id,
        "zone": zone,
    }


@app.get("/status")
def status():
    return {
        zone: {
            "stream": "running" if proc.poll() is None else "stopped",
            "queue_size": audio_queues[zone].qsize() if zone in audio_queues else 0,
            "last_errors": list(ffmpeg_errors.get(zone, ())),
        }
        for zone, proc in ffmpeg_procs.items()
    }


# @app.get("/status/1")
# def test_status():
#     conn = sqlite3.connect(DB_PATH)
#     conn.row_factory = sqlite3.Row

#     cursor = conn.cursor()
#     cursor.execute("SELECT * FROM devices")
#     devices = [dict(row) for row in cursor.fetchall()]
#     conn.close()
#     return {
#         "status": "ok",
#         "devices": devices,
        
#     }



