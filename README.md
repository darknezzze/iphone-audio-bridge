# iPhone Audio Bridge

**Stream your Windows PC's system audio to an iPhone over Wi-Fi and listen with AirPods or any Bluetooth headphones. Optionally use the AirPods microphone as a microphone on the PC.**

No app to install on the iPhone: everything runs in Safari. The PC side is a small Python server (WASAPI loopback + WebSocket).

Useful when your PC has no Bluetooth adapter, or when you want to use headphones that are paired with your iPhone to listen to PC audio.

## Features

- PC system audio -> iPhone -> Bluetooth headphones / AirPods
- iPhone (AirPods) microphone -> PC, as a virtual microphone via VB-Cable
- Works in Safari, no iOS app needed
- Keeps playing in the background and with the screen locked (via an `<audio>` element and Media Session; behavior depends on the iOS version)
- Low-latency tuning: short queues, drops old audio instead of lagging behind
- Sample rate and channels are detected automatically
- Self-signed HTTPS certificate is generated automatically (required by iOS for microphone access)

## How it works

```
PC audio (WASAPI loopback) -> Python server -> WebSocket -> Safari (Web Audio) -> AirPods
AirPods mic -> Safari (getUserMedia) -> WebSocket -> Python server -> VB-Cable -> any PC app
```

## Requirements

- Windows 10/11 (WASAPI loopback is Windows-only)
- Python 3.9+
- iPhone and PC on the same Wi-Fi network
- [VB-Cable](https://vb-audio.com/Cable/) (free), only for the microphone feature

## Installation

```bash
git clone https://github.com/darknezzze/iphone-audio-bridge.git
cd iphone-audio-bridge
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## Usage

1. Pair your Bluetooth headphones with the iPhone.
2. Run the server: `python server.py`
3. Allow Python through Windows Firewall for **private networks** (port `8443`) if prompted.
4. On the iPhone, open the `https://<PC-IP>:8443` address printed in the console.
5. Safari warns about the certificate (it is self-signed): tap **Show Details -> visit this website**.
6. Tap **Connect** and play something on the PC.

### Using the iPhone/AirPods microphone on the PC

1. Install VB-Cable and reboot.
2. Restart the server. The console should say `Microphone routed to: CABLE Input`.
3. On the iPhone, tap **Start microphone** and allow access.
4. In Discord, Zoom, a game, etc., choose **CABLE Output (VB-Audio Virtual Cable)** as the microphone.

Do not set `CABLE Input` as the default Windows playback device, or the bridge will capture your own microphone.

## Latency

| Part | Approx. |
|---|---|
| Server capture + Wi-Fi + WebSocket | 20-50 ms |
| Browser buffers | 30-100 ms |
| Bluetooth headphones (AAC/SBC) | 120-250 ms |

Total is usually **250-350 ms** with Bluetooth headphones. Fine for music and podcasts, noticeable for video and games. The Bluetooth codec is the main limit and cannot be changed from software.

Tuning options: `CHUNK` in `server.py`, and `BUFFER_SIZE`, `MAX_QUEUE`, `KEEP_QUEUE` in `web/index.html`. If you hear crackling, increase them.

## Limitations

- **AirPods quality drops while the microphone is on.** Bluetooth switches to the call profile (mono, low quality). This is an iOS/Bluetooth limitation.
- Background playback on iOS is not guaranteed and depends on the iOS version. If audio stops, keep Safari open with auto-lock disabled.
- iOS requires HTTPS for microphone access, hence the self-signed certificate.
- Windows only. Not suitable for latency-critical use (rhythm games, live monitoring).

## Troubleshooting

- **Page does not open on the iPhone:** check the firewall, that both devices are on the same network, and that you use `https://` and port `8443`.
- **Audio is pitched or slow:** restart the server and reload the page (sample rate is detected from the device).
- **Silence:** check the iPhone silent switch and that audio is actually playing on the Windows default output device.
- **Microphone button is disabled:** VB-Cable was not found. Install it, reboot, and restart the server.
- **WebSocket fails after accepting the certificate:** reload the page, or install `cert.pem` as a trusted profile on the iPhone.

## Tested on

Windows 11 / iOS 17 / AirPods (edit this line with your own setup)

## Related projects

[SoundPush](https://github.com/FlyToRakib/soundpush), [MicYou](https://github.com/OrientCOMPASS/MicYou), [pc-webrtc-audio-streamer](https://github.com/olpapchenko/pc-webrtc-audio-streamer), [AudioRelay](https://audiorelay.net/)

## License

MIT
