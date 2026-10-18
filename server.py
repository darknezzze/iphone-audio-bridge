import asyncio
import datetime
import ipaddress
import queue
import socket
import ssl
import threading
from pathlib import Path

import pyaudiowpatch as pyaudio
from aiohttp import web


HOST = "0.0.0.0"
PORT = 8443  # HTTPS is required for microphone access on iOS

CHANNELS = 2
CHUNK = 960

BASE_DIR = Path(__file__).parent
WEB_DIR = BASE_DIR / "web"

clients = set()
audio_queue = asyncio.Queue(maxsize=5)

loop = None
audio_stream = None
pa = None

stream_rate = 48000
stream_channels = 2

# iPhone microphone -> virtual cable on the PC.
mic_queue = queue.Queue(maxsize=10)
mic_stream = None
mic_info = {"available": False, "rate": 48000, "channels": 2}


def get_local_ip():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except Exception:
        return "127.0.0.1"


def ensure_cert(ip):
    cert_path = BASE_DIR / "cert.pem"
    key_path = BASE_DIR / "key.pem"

    if cert_path.exists() and key_path.exists():
        return cert_path, key_path

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "audio-bridge")])
    now = datetime.datetime.now(datetime.timezone.utc)

    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=825))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.IPAddress(ipaddress.ip_address(ip)),
                    x509.DNSName("localhost"),
                ]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    print("Certificate created:", cert_path)
    return cert_path, key_path


def find_loopback_device():
    global pa

    pa = pyaudio.PyAudio()

    default_output = pa.get_default_output_device_info()

    print()
    print("Windows output device:")
    print(f"  Name: {default_output['name']}")
    print(f"  Index: {default_output['index']}")
    print()

    output_name = default_output["name"]

    print("Searching for a WASAPI loopback device...")

    candidates = []

    for device in pa.get_loopback_device_info_generator():
        candidates.append(device)
        print(f"  [{device['index']}] {device['name']}")

        if output_name.lower() in device["name"].lower():
            return device

    output_parts = output_name.lower().split()

    for device in candidates:
        name = device["name"].lower()

        if any(part in name for part in output_parts if len(part) > 3):
            return device

    if not candidates:
        raise RuntimeError("No WASAPI loopback devices found.")

    print()
    print("No exact loopback match found.")
    print("Using the first available one.")

    return candidates[0]


def audio_callback(in_data, frame_count, time_info, status):
    if status:
        print("Audio:", status)

    try:
        loop.call_soon_threadsafe(put_audio, in_data)
    except Exception as e:
        print("Audio callback error:", e)

    return (None, pyaudio.paContinue)


def put_audio(data):
    try:
        audio_queue.put_nowait(data)

    except asyncio.QueueFull:
        try:
            audio_queue.get_nowait()
        except asyncio.QueueEmpty:
            pass

        try:
            audio_queue.put_nowait(data)
        except asyncio.QueueFull:
            pass


def start_audio_capture():
    global audio_stream, stream_rate, stream_channels

    device = find_loopback_device()

    channels = min(CHANNELS, int(device["maxInputChannels"]))

    if channels < 1:
        raise RuntimeError("Loopback device has no input channels.")

    sample_rate = int(device.get("defaultSampleRate", 48000))

    stream_rate = sample_rate
    stream_channels = channels

    print()
    print("Using loopback:")
    print(f"  Name: {device['name']}")
    print(f"  Index: {device['index']}")
    print(f"  Channels: {channels}")
    print(f"  Sample rate: {sample_rate}")
    print()

    audio_stream = pa.open(
        format=pyaudio.paInt16,
        channels=channels,
        rate=sample_rate,
        input=True,
        input_device_index=device["index"],
        frames_per_buffer=CHUNK,
        stream_callback=audio_callback,
    )

    audio_stream.start_stream()

    print("✓ System audio capture started.")
    print()


def find_cable_input():
    wasapi = pa.get_host_api_info_by_type(pyaudio.paWASAPI)["index"]

    for i in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(i)

        if (
            info["hostApi"] == wasapi
            and info["maxOutputChannels"] > 0
            and "cable input" in info["name"].lower()
        ):
            return info

    return None


def mic_writer():
    while True:
        data = mic_queue.get()

        try:
            mic_stream.write(data)
        except Exception as e:
            print("Microphone output error:", e)


def start_mic_output():
    global mic_stream

    info = find_cable_input()

    if info is None:
        print("! VB-Cable not found - microphone disabled.")
        print("  Install VB-Cable (vb-audio.com/Cable) and reboot.")
        print()
        return

    channels = min(2, int(info["maxOutputChannels"]))
    rate = int(info["defaultSampleRate"])

    mic_stream = pa.open(
        format=pyaudio.paInt16,
        channels=channels,
        rate=rate,
        output=True,
        output_device_index=info["index"],
    )

    mic_info.update(available=True, rate=rate, channels=channels)

    threading.Thread(target=mic_writer, daemon=True).start()

    print(f"✓ Microphone routed to: {info['name']} ({rate} Hz)")
    print()


async def websocket_handler(request):
    ws = web.WebSocketResponse()

    await ws.prepare(request)

    clients.add(ws)

    print(f"[+] iPhone connected (clients: {len(clients)})")

    try:
        async for message in ws:
            pass

    except Exception as e:
        print("WebSocket error:", e)

    finally:
        clients.discard(ws)

        print(f"[-] iPhone disconnected (clients: {len(clients)})")

    return ws


async def mic_handler(request):
    ws = web.WebSocketResponse()

    await ws.prepare(request)

    print("[+] iPhone microphone on")

    try:
        async for message in ws:
            if message.type != web.WSMsgType.BINARY or not mic_info["available"]:
                continue

            try:
                mic_queue.put_nowait(message.data)
            except queue.Full:
                try:
                    mic_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    mic_queue.put_nowait(message.data)
                except queue.Full:
                    pass

    finally:
        print("[-] iPhone microphone off")

    return ws


async def audio_broadcast_loop():
    while True:
        data = await audio_queue.get()

        if not clients:
            continue

        disconnected = []

        for client in clients.copy():
            try:
                await client.send_bytes(data)
            except Exception:
                disconnected.append(client)

        for client in disconnected:
            clients.discard(client)


async def index_handler(request):
    return web.FileResponse(
        WEB_DIR / "index.html",
        headers={"Cache-Control": "no-store"},
    )


async def config_handler(request):
    return web.json_response(
        {
            "sampleRate": stream_rate,
            "channels": stream_channels,
            "micAvailable": mic_info["available"],
            "micRate": mic_info["rate"],
            "micChannels": mic_info["channels"],
        }
    )


async def main():
    global loop

    loop = asyncio.get_running_loop()

    start_audio_capture()
    start_mic_output()

    app = web.Application()

    app.router.add_get("/", index_handler)
    app.router.add_get("/config", config_handler)
    app.router.add_get("/audio", websocket_handler)
    app.router.add_get("/mic", mic_handler)

    runner = web.AppRunner(app)

    await runner.setup()

    ip = get_local_ip()
    cert_path, key_path = ensure_cert(ip)

    ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ssl_context.load_cert_chain(cert_path, key_path)

    site = web.TCPSite(runner, HOST, PORT, ssl_context=ssl_context)

    await site.start()

    print("=" * 60)
    print(" iPhone Audio Bridge")
    print("=" * 60)
    print()
    print("Open on your iPhone:")
    print()
    print(f"    https://{ip}:{PORT}")
    print()
    print("Safari will warn about the certificate:")
    print("Show Details -> visit this website.")
    print()
    print("=" * 60)

    asyncio.create_task(audio_broadcast_loop())

    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        print("\nShutting down...")

        if audio_stream:
            audio_stream.stop_stream()
            audio_stream.close()

        if pa:
            pa.terminate()
