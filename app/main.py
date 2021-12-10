"""Crow's Nest Auth microservice"""
import logging
import warnings
import asyncio
from pathlib import Path
from environs import Env

from fastapi import FastAPI, Depends, Request, Response, HTTPException
from fastapi.responses import HTMLResponse

from av import VideoFrame
import numpy as np

from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiortc.contrib.media import MediaPlayer, MediaRelay

# Reading config from environment variables
env = Env()

BASE_URL = env("BASE_URL", "")
LOG_LEVEL = env.log_level("LOG_LEVEL", logging.WARNING)
VIDEO_STREAMS = env.dict("VIDEO_STREAMS")

ROOT = Path(__file__).parent

# Setup logger
logging.basicConfig(level=LOG_LEVEL)
logging.captureWarnings(True)
warnings.filterwarnings("once")
LOGGER = logging.getLogger("crowsnest-server-webrtc")

# Setting up app
app = FastAPI(root_path=BASE_URL)

# Keeper of peer connections
pcs = set()


@app.on_event("shutdown")
async def shutdown():
    """Run during shutdown of this application"""
    coros = [pc.close() for pc in pcs]
    await asyncio.gather(*coros)
    pcs.clear()


### Track creators (for different type of stream sources)


def create_track_from_shared_memory(path: str):
    from pycluon import SharedMemory

    class _(VideoStreamTrack):
        """
        A video track reading from shared memory
        """

        def __init__(self, shared_memory: SharedMemory):
            super().__init__()
            self._shared_memory = shared_memory

        async def recv(self):
            # await lock on shared memory
            loop = asyncio.get_running_loop()
            LOGGER.info("Awaiting shared memory...")
            await loop.run_in_executor(None, self._shared_memory.wait)

            # Read data from shared memory
            LOGGER.info("Data available! Reading...")
            self._shared_memory.lock()
            img = np.frombuffer(self._shared_memory.data, np.uint8).reshape(480, 640, 4)
            self._shared_memory.unlock()

            # convert to frame and return
            LOGGER.info("Converting data to frame and returning...")
            return VideoFrame.from_ndarray(img, format="rgb24")

    return None, _(SharedMemory(path))


def create_track_from_uri(uri: str):
    player = MediaPlayer(uri)
    return player.audio, player.video


### Routes


@app.get("/", response_class=HTMLResponse)
async def _():
    return (ROOT / "static" / "index.html").read_text()


@app.get("/client.js")
async def _():
    content = (ROOT / "static" / "client.js").read_text()
    return Response(content=content, media_type="application/javascript")


@app.get("/streams")
async def _() -> list:
    return list(VIDEO_STREAMS.keys())


@app.get("/streams/{stream_id}")
async def _(stream_id: str) -> str:
    return VIDEO_STREAMS[stream_id]


@app.post("/streams/{stream_id}/offer")
async def _(stream_id: str, configuration: dict) -> dict:
    offer = RTCSessionDescription(sdp=configuration["sdp"], type=configuration["type"])

    pc = RTCPeerConnection()
    pcs.add(pc)

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        print("Connection state is %s" % pc.connectionState)
        if pc.connectionState == "failed":
            await pc.close()
            pcs.discard(pc)

    # open media source
    source = VIDEO_STREAMS[stream_id]
    audio, video = create_track_from_shared_memory(source)

    await pc.setRemoteDescription(offer)
    for t in pc.getTransceivers():
        if t.kind == "audio" and audio:
            pc.addTrack(audio)
        elif t.kind == "video" and video:
            pc.addTrack(video)

    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}
