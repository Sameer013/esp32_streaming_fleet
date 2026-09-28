# IP Hooter – Streaming Backend Server

The main server program for the IP Hooter system. It streams audio to an **Icecast** server, manages the streaming zones, and monitors the health of the **ESP32** units connected on the same network.

## Features

- **FastAPI** REST API that triggers audio playback on demand
- **FFmpeg-based streaming** of audio files to Icecast mount points
- **Config-driven mapping** (`config.json`) of API endpoints to audio files and zones
- **Heartbeat monitoring** (`heartbeat.py`) of ESP32 units, with status stored in **SQLite**
- **24x7 silent stream** so ESP32 units never lose their connection
- Fully configurable ports and settings
- Low latency: typically **under 5 seconds** from API call to audio playing on the ESP32 (on a good WiFi connection)

## How It Works

```
 API request ──► FastAPI ──► config.json lookup ──► FFmpeg ──► Icecast ──► ESP32 units
                                  │                               ▲
                            audio/ directory                      │
                                                          silent.mp3 (24x7 loop)

 ESP32 units ──► heartbeat.py ──► SQLite DB (status & running condition)
```

1. A request hits a FastAPI endpoint.
2. The server looks up the matching audio file (and zone) in `config.json`.
3. FastAPI executes an FFmpeg command that streams that file from the `audio/` directory to the Icecast server.
4. ESP32 units listening to the Icecast mount play the audio.
5. Meanwhile, `heartbeat.py` records each ESP32's heartbeat and running status in a SQLite database file.

### Silent stream

The `audio/` folder contains `silent.mp3`, which streams continuously (24x7) whenever no announcement is playing. This keeps the connection to the ESP32 units alive so they don't have to reconnect each time audio is triggered.

## Project Structure

```
.
├── server.py          # FastAPI entry point
├── heartbeat.py       # ESP32 heartbeat & status tracking
├── config.json        # Maps API requests -> audio files / zones
├── audio/
│   └── silent.mp3     # Idle stream (plays 24x7)
└── *.db               # SQLite database (heartbeat / unit status)
```

> Adjust the tree above to match your actual repository layout.

## Requirements

- Python 3.12+
- [FFmpeg](https://ffmpeg.org/) installed and available on `PATH` or Just give the path of the binary in the env file,
- A running [Icecast](https://icecast.org/) server
- ESP32 units on the same network as the server

Python dependencies (adjust to your `requirements.txt`):

```bash
pip install fastapi uvicorn
```

## Configuration

Ports, Icecast settings, and audio mappings are all configurable via `config.json`.

Example (edit to match your real schema):

```json
{
  "tracks": {
    "1": {
      "zone": "zone1", // assign them zones
      "audio": "track1.mp3"
    },

    "2": {
      "zone": "zone1",
      "audio": "track2.mp3"
    },
    // So on you can add n numbers of track in here 

    "10": {
      "zone": "zone2", //assign them zones
      "audio": "track3.mp3"
    },

    "11": {
      "zone": "all", // you can also select global zones too
      "audio": "track4.mp3"
    }
  },

  "zones": { // you can define n numbers of zones in here
    "zone1": "/live1",
    "zone2": "/live2"
  },

  "icecast": // Icecast server details
  {
    "host": "127.0.0.1",
    "port": 8000,
    "password": "password"
  },

  "audio_dir": "./audio",
  "silence_file": "./audio/silence.mp3"
}
```

## Running the Server

Start the server by running `server.py` with the desired FastAPI/Uvicorn arguments:

```bash
python server.py --host 0.0.0.0 --port 8080
```

Or directly with Uvicorn:

```bash
uvicorn server:app --host 0.0.0.0 --port 5000 --reload 
```

Once running, interactive API docs are available at `http://<server-ip>:8080/docs`.

## API Docs
 
Replace `<ip_where_server_is_running>` with the IP of the machine running the server.
 
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `http://<ip_where_server_is_running>:5000/dashboard` | Dashboard UI |
| GET | `http://<ip_where_server_is_running>:5000/docs` | Interactive FastAPI docs (Swagger UI) with the full list of endpoints |
 
> **Note:** The `/docs` page loads its CSS/JS from a CDN, so the machine you open it on needs an internet connection. Without internet the page won't render properly.



## Heartbeat & Monitoring

`heartbeat.py` tracks the heartbeat and running condition of every ESP32 unit on the network. All information is stored in a SQLite database file, so it can be inspected easily:

```bash
sqlite3 hooter.db "SELECT * FROM <table>;"
```
There is also `seed.py` where it will do the inital seeding of the database read that file I have mentioned all the arguments in that file.

## ESP32 Firmware

The ESP32 Arduino code will be added to this repository soon.

## Notes

- Latency from hitting the API to hearing audio on an ESP32 is consistently well under 5 seconds with a good WiFi connection.
- Keep `silent.mp3` in the `audio/` folder; the idle stream depends on it.

## License

This project is licensed under the [MIT License](LICENSE).


---

## Tech Stack

- **Framework:** FastApi (Python)
- **UI:** Vanilla (javascript)
- **DATABASE:** SQLite3
- **Binaries**: FFmpeg, Icecast


##  Author

**Sameer**

- GitHub: [@Sameer013](https://github.com/Sameer013)
- Mail: clarno06@gmail.com  