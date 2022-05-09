"""Main entrypoint for this application"""
import asyncio
import json
import logging
import warnings
from weakref import WeakValueDictionary

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
MQTT_BASE_TOPIC = env("MQTT_BASE_TOPIC")

MEDIA_SOURCE = env("MEDIA_SOURCE")
TARGET_BITRATE = env.int("TARGET_BITRATE", None)

LOG_LEVEL = env.log_level("LOG_LEVEL", logging.WARNING)

# Setup logger
logging.basicConfig(level=LOG_LEVEL)
logging.captureWarnings(True)
warnings.filterwarnings("once")
LOGGER = logging.getLogger("crowsnest-webrtc-producer")

# Globals
sources = WeakValueDictionary()
pcs = set()


async def create_peer_connection(sdp: str, sdp_type: str, source: MediaPlayer):
    """Negotiating WebRTC connection

    Args:
        sdp (str): Session Description Protocol string
        type (str): Type of sdp, i.e. offer or answer
        source (MediaPlayer): the source to be used for the connection

    Returns:
        str: Session Description Protocol (SDP) answer
    """
    remote_description = RTCSessionDescription(sdp=sdp, type=sdp_type)

    pc = RTCPeerConnection()  # pylint: disable=invalid-name

    await pc.setRemoteDescription(remote_description)

    # Add to global
    pcs.add(pc)

    # Attach strong reference to source
    pc.source = source

    # Attach callback
    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        LOGGER.info("Connection state is %s", pc.connectionState)
        if pc.connectionState == "failed":
            await pc.close()
            pcs.discard(pc)

    for transceiver in pc.getTransceivers():
        if transceiver.kind == "audio" and source.audio:
            pc.addTrack(MediaRelay().subscribe(source.audio))
        elif transceiver.kind == "video" and source.video:
            pc.addTrack(MediaRelay().subscribe(source.video))

    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}


async def clean_up():
    """Clean up by closing all open peer connections"""
    coros = [pc.close() for pc in pcs]
    asyncio.gather(*coros)
    pcs.clear()


async def main():
    """Main "loop" """

    client = Client(
        MQTT_BROKER_HOST,
        MQTT_BROKER_PORT,
        username=MQTT_USER,
        password=MQTT_PASSWORD,
        protocol=MQTTv5,
        transport=MQTT_TRANSPORT,
    )

    if MQTT_TLS:
        client._client.tls_set()  # pylint: disable=protected-access

    await client.connect()

    async with client.unfiltered_messages() as messages:
        await client.subscribe(f"{MQTT_BASE_TOPIC}/#")

        message: MQTTMessage
        async for message in messages:

            topic: str = message.topic
            path: str = topic.replace(MQTT_BASE_TOPIC, "", 1)

            logging.info(
                "Received request for a new peer connection on topic: %s, i.e. path: %s",
                topic,
                path,
            )

            # Get the payload, i.e. SDP
            payload = json.loads(message.payload)

            # Extract MQTTv5 messaging protocol specifics
            response_topic = message.properties.ResponseTopic
            correlation_data = message.properties.CorrelationData

            # Create return properties struct
            properties = Properties(PacketTypes.PUBLISH)
            properties.CorrelationData = correlation_data

            # Try to get an existing source
            source = sources.get(path)

            # If fail, create a new one
            if source is None:
                try:
                    source = sources[path] = MediaPlayer(f"{MEDIA_SOURCE}{path}")
                except Exception as exc:  # pylint: disable=broad-except
                    await client.publish(
                        response_topic,
                        json.dumps(str(exc)),
                        properties=properties,
                    )
                    return

            # We have a working source, lets get the connection set up
            answer = await create_peer_connection(
                payload["sdp"], payload["type"], source
            )

            await client.publish(
                response_topic, json.dumps(answer), properties=properties
            )

    await client.disconnect()


if __name__ == "__main__":

    if TARGET_BITRATE:
        # Workaround for setting a target bitrate for the video feed,
        # see: https://github.com/aiortc/aiortc/issues/402
        import aiortc

        aiortc.codecs.vpx.MIN_BITRATE = (
            aiortc.codecs.vpx.DEFAULT_BITRATE
        ) = aiortc.codecs.vpx.MAX_BITRATE = TARGET_BITRATE

    loop = asyncio.get_event_loop()

    try:
        loop.run_until_complete(main())
    except MqttError:
        LOGGER.exception(
            "MQTT connection failed, automatic reconnect has not yet been fixed..."
        )
    finally:
        loop.run_until_complete(clean_up())
