# crowsnest-proxy-webrtc

A proxy microservice within the crowsnest ecosystem which acts as a gateway between the local rtsp server ([part of the core crowsnest setup](https://github.com/MO-RISE/crowsnest/blob/main/docker-compose.base.yml#L57-L64)) and external consumers of the video and audio streams.

The proxy is built using WebRTC to minimize latency and enable live streams from remote setups without static addresses. Signalling ([some more details here](https://developer.mozilla.org/en-US/docs/Web/API/WebRTC_API/Signaling_and_video_calling)) utilizes MQTTv5 with response topics and correlation data using the ordinary crowsnest base setup for MQTT transport. 

### How it works

The proxy:
1. Connects to a `MEDIA_SOURCE`, i.e. the rtsp server address.
2. Listens on a `MQTT_BASE_TOPIC` (including all subtopics, i.e. `MQTT_BASETOPIC/#` for WebRTC "offers" 
3. Uses the remainder of the topic path, i.e. everything caught by the `#`, as the `rtsp path`
4. Tries to connect to `MEDIA_SOURCE` using `rtsp path` and fetch the livestream
5. Reply with a WebRTC "answer"

### Typical setup (docker-compose)

```yaml
version: '3'
services:

  webrtc-proxy:
    image: ghcr.io/mo-rise/crowsnest-proxy-webrtc:latest
    restart: unless-stopped
    network_mode: "host"
    environment:
      - MQTT_BROKER_HOST=localhost
      - MQTT_BROKER_PORT=1883
      - MQTT_BASE_TOPIC=CROWSNEST/<platform>/WEBRTC
      - MEDIA_SOURCE=rtsp://localhost:8554
```



## Development setup
To setup the development environment:

    python3 -m venv venv
    source ven/bin/activate

Install everything thats needed for development:

    pip install -r requirements_dev.txt

To run the linters:

    black main.py tests
    pylint main.py

To run the tests:

    no automatic tests yet...

    For local, manual testing, see examples/

## License
Apache 2.0, see [LICENSE](./LICENSE)