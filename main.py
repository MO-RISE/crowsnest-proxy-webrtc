import asyncio
import atexit
import json
import logging
import warnings
import ssl

from environs import Env

from asyncio_mqtt import Client, MqttError

from paho.mqtt.client import MQTTv5, MQTTMessage
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties

from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.contrib.media import MediaPlayer, MediaRelay

env = Env()

MQTT_BROKER_HOST = env("MQTT_BROKER_HOST")
MQTT_BROKER_PORT = env.int("MQTT_BROKER_PORT", 1883)
MQTT_CLIENT_ID = env("MQTT_CLIENT_ID", None)
MQTT_TRANSPORT = env("MQTT_TRANSPORT", "tcp")
MQTT_TLS = env.bool("MQTT_TLS", False)
MQTT_USER = env("MQTT_USER", None)
MQTT_PASSWORD = env("MQTT_PASSWORD", None)
MQTT_BASE_TOPIC = env("MQTT_BASE_TOPIC", "/test/test")

MEDIA_SOURCE = env("MEDIA_SOURCE")
TARGET_BITRATE = env.int("TARGET_BITRATE", None)

LOG_LEVEL = env.log_level("LOG_LEVEL", logging.WARNING)

# Setup logger
logging.basicConfig(level=LOG_LEVEL)
logging.captureWarnings(True)
warnings.filterwarnings("once")
LOGGER = logging.getLogger("crowsnest-webrtc-producer")

# mq = Client(client_id=MQTT_CLIENT_ID, transport=MQTT_TRANSPORT, protocol=MQTTv5)

# Globals...
pcs = set()
source = None


async def offer(sdp: str, type: str):
    logging.info("Do we get here?")
    offer = RTCSessionDescription(sdp=sdp, type=type)

    pc = RTCPeerConnection()
    pcs.add(pc)

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        print("Connection state is %s" % pc.connectionState)
        if pc.connectionState == "failed":
            await pc.close()
            pcs.discard(pc)

    await pc.setRemoteDescription(offer)
    for t in pc.getTransceivers():
        if t.kind == "audio" and source.audio:
            pc.addTrack(MediaRelay().subscribe(source.audio))
        elif t.kind == "video" and source.video:
            pc.addTrack(MediaRelay().subscribe(source.video))

    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}


async def clean_up():
    # close peer connections
    coros = [pc.close() for pc in pcs]
    asyncio.gather(*coros)
    pcs.clear()


async def main():

    client = Client(
        MQTT_BROKER_HOST,
        MQTT_BROKER_PORT,
        username=MQTT_USER,
        password=MQTT_PASSWORD,
        protocol=MQTTv5,
        transport=MQTT_TRANSPORT,
    )

    client._client.tls_set()
    await client.connect()

    async with client.unfiltered_messages() as messages:
        await client.subscribe(f"{MQTT_BASE_TOPIC}/offer")

        message: MQTTMessage
        async for message in messages:
            logging.info("Received request for a new peer connection")

            payload = json.loads(message.payload)
            response_topic = message.properties.ResponseTopic
            correlation_data = message.properties.CorrelationData

            answer = await offer(payload["sdp"], payload["type"])

            properties = Properties(PacketTypes.PUBLISH)
            properties.CorrelationData = correlation_data

            await client.publish(
                response_topic, json.dumps(answer), properties=properties
            )

    await client.disconnect()


if __name__ == "__main__":

    if TARGET_BITRATE:
        # Workaround for setting a target bitrate for the video feed, see: https://github.com/aiortc/aiortc/issues/402
        import aiortc

        aiortc.codecs.vpx.MIN_BITRATE = (
            aiortc.codecs.vpx.DEFAULT_BITRATE
        ) = aiortc.codecs.vpx.MAX_BITRATE = TARGET_BITRATE

    # Open media source
    source = MediaPlayer(MEDIA_SOURCE)

    loop = asyncio.get_event_loop()

    try:
        loop.run_until_complete(main())
    except MqttError as exc:
        print(exc)
    finally:
        loop.run_until_complete(clean_up())
