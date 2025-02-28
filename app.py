import argparse
import asyncio
import json
import logging
import threading
import tkinter as tk
from tkinter import scrolledtext, ttk
from queue import Queue, Empty
import sounddevice as sd
import numpy as np
import mss
import pyaudio
from aiohttp import web
from aiortc import (
    RTCPeerConnection,
    RTCSessionDescription,
    VideoStreamTrack,
    MediaStreamTrack,
)
from av import VideoFrame, AudioFrame
from Audio2 import AudioTrack2


class ScreenVideoTrack(VideoStreamTrack):
    def __init__(self):
        super().__init__()
        self.sct = mss.mss()
        self.monitor = self.sct.monitors[1]
        self._codec = "h264"
        self._profile = "high"
        self._level = "4.2"  # 提升至Level 4.2
        self.frame_interval = 1 / 60
        self._packetization_mode = 1

    @property
    def codec(self):
        return self._codec

    @property
    def profile(self):
        return self._profile

    @property
    def level(self):
        return self._level

    @property
    def packetization_mode(self):
        return self._packetization_mode

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        img = self.sct.grab(self.monitor)
        frame = np.array(img)[:, :, :3]
        video_frame = VideoFrame.from_ndarray(frame, format="bgr24")
        video_frame.pts = pts
        video_frame.time_base = time_base
        return video_frame


class AudioTrack(MediaStreamTrack):
    """
    利用 sounddevice 捕捉音訊，並將其轉換為 AudioFrame 供 WebRTC 傳送。
    使用 WASAPI loopback（捕捉系統音效）的方式，
    並透過內部的 queue 傳送 callback 取得的音訊數據。
    """

    kind = "audio"

    def __init__(self):
        super().__init__()  # This was commented out, needed for proper inheritance
        self.samplerate = 44100  # 指定採樣率
        self.channels = 2  # 固定雙聲道以匹配後續的 stereo layout
        self.blocksize = 1024  # 和 AudioFrame samples 匹配
        self._queue = asyncio.Queue(maxsize=10)  # 限制隊列大小防止內存溢出
        
        # 找到正確的 loopback 設備
        devices = sd.query_devices()
        loopback_device = None
        for i, device in enumerate(devices):
            if 'WASAPI' in str(device) and device['max_input_channels'] > 0:
                print(f"找到可能的 loopback 設備: {i}: {device['name']}")
                loopback_device = i
                break
        
        if loopback_device is None:
            print("未找到 loopback 設備，使用默認輸入")
            loopback_device = sd.default.device[0] # DEVICE ID
            
        print(f"使用音頻設備: {loopback_device}")
        
        def callback(indata, frames, time, status):
            if status:
                print(f"音頻狀態錯誤: {status}")
            
            # 確保數據是 16 位整數（與 AudioFrame 格式匹配）
            audio_data = indata.astype(np.int16).tobytes()
            
            try:
                self._queue.put_nowait(audio_data)
            except asyncio.QueueFull:
                # 隊列滿時，移除最舊的項目然後添加新項目
                try:
                    self._queue.get_nowait()
                    self._queue.put_nowait(audio_data)
                except:
                    pass

        self.stream = sd.InputStream(
            channels=self.channels,
            samplerate=self.samplerate,
            blocksize=self.blocksize,
            dtype='int16',  # 直接用 int16 格式採集
            callback=callback,
            device=loopback_device,
            latency='low',
            loopback=True  # 啟用 loopback 模式捕獲系統聲音
        )
        self.stream.start()
        print(f"音頻流已啟動: {self.channels}聲道, {self.samplerate}Hz")

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        
        # 等待音頻數據
        try:
            data = await self._queue.get() # REMOVE AND RETURN
        except Exception as e:
            print(f"獲取音頻數據時出錯: {e}")
            # 如果出錯，生成靜音
            data = bytes(self.blocksize * self.channels * 2)  # 16位元 = 2 bytes
        
        # 創建音頻幀
        audio_frame = AudioFrame(format="s16", layout="stereo", samples=self.blocksize)
        audio_frame.pts = pts
        audio_frame.time_base = time_base
        print(data)
        audio_frame.planes[0].update(data)
        
        return audio_frame
    
    # 添加清理方法
    def stop(self):
        if hasattr(self, 'stream') and self.stream:
            self.stream.stop()
            self.stream.close()
class ScreenShareServer:
    def __init__(self, host="0.0.0.0", port=8080, log_callback=None):
        self.host = host
        self.port = port
        self.pcs = set()
        self.app = web.Application()
        self.app.router.add_post("/offer", self.offer)
        self.app.router.add_get("/", self.index)
        self.app.on_shutdown.append(self.on_shutdown)
        self.runner = None
        self.log_callback = log_callback
        self.loop = None

    async def offer(self, request):
        params = await request.json()
        offer = RTCSessionDescription(sdp=params["sdp"], type=params["type"])
        pc = RTCPeerConnection()
        self.pcs.add(pc)

        self.log(f"New connection from {request.remote}")

        @pc.on("connectionstatechange")
        async def on_connectionstatechange():
            self.log(f"Connection state: {pc.connectionState}")
            if pc.connectionState in ["failed", "closed"]:
                await pc.close()
                self.pcs.discard(pc)

        pc.addTrack(ScreenVideoTrack())
        pc.addTrack(AudioTrack2())
        await pc.setRemoteDescription(offer)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        return web.Response(
            content_type="application/json",
            text=json.dumps(
                {"sdp": pc.localDescription.sdp, "type": pc.localDescription.type}
            ),
        )

    async def index(self, request):
        return web.Response(
            content_type="text/html",
            text="""
        <!DOCTYPE html>
        <html>
        <head><title>WebRTC Screen Share</title></head>
        <body>
        <video id="video" autoplay playsinline controls></video>
        <audio id="audio" autoplay playsinline></audio>
        <script>
        const pc = new RTCPeerConnection();
        pc.ontrack = (event) => {
            if (event.track.kind === "video") {
                const videoElem = document.getElementById('video');
                if (!videoElem.srcObject) {
                    videoElem.srcObject = new MediaStream();
                }
                videoElem.srcObject.addTrack(event.track);
            } else if (event.track.kind === "audio") {
                const audioElem = document.getElementById('audio');
                if (!audioElem.srcObject) {
                    audioElem.srcObject = new MediaStream();
                }
                audioElem.srcObject.addTrack(event.track);
                audioElem.volume = 1.0;  // 確保音量最大
                console.log("收到音頻軌道", event.track);
            }
        };
        async function start() {
            pc.addTransceiver("video", {"direction": "recvonly"});
            pc.addTransceiver("audio", {"direction": "recvonly"});
            const offer = await pc.createOffer();
            await pc.setLocalDescription(offer);
            const response = await fetch("/offer", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify({sdp: pc.localDescription.sdp, type: pc.localDescription.type})
            });
            const answer = await response.json();
            await pc.setRemoteDescription(answer);
        }
        start();
        </script>
        </body>
        </html>
        """,
        )

    async def on_shutdown(self, app):
        for pc in self.pcs:
            await pc.close()
        self.pcs.clear()

    def log(self, message):
        if self.log_callback:
            self.log_callback(message)

    async def run_async(self):
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, self.host, self.port)
        await site.start()
        self.log(f"Server started at http://{self.host}:{self.port}")
        while True:
            await asyncio.sleep(1)

    def stop(self):
        async def shutdown():
            await self.runner.shutdown()
            await self.runner.cleanup()

        if self.loop:
            self.loop.create_task(shutdown())


class ServerThread(threading.Thread):
    def __init__(self, server, loop):
        super().__init__()
        self.server = server
        self.loop = loop
        self._stop_event = threading.Event()

    def run(self):
        asyncio.set_event_loop(self.loop)
        self.server.loop = self.loop
        try:
            self.loop.run_until_complete(self.server.run_async())
        except asyncio.CancelledError:
            pass
        finally:
            self.loop.close()

    def stop(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.join()


class ServerUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Screen Share Server")
        self.geometry("600x400")

        # Server controls
        self.control_frame = ttk.Frame(self)
        self.control_frame.pack(pady=10)

        self.start_btn = ttk.Button(
            self.control_frame, text="Start Server", command=self.start_server
        )
        self.start_btn.pack(side=tk.LEFT, padx=5)

        self.stop_btn = ttk.Button(
            self.control_frame,
            text="Stop Server",
            command=self.stop_server,
            state=tk.DISABLED,
        )
        self.stop_btn.pack(side=tk.LEFT, padx=5)

        # Log display
        self.log_area = scrolledtext.ScrolledText(self, wrap=tk.WORD)
        self.log_area.pack(padx=10, pady=5, fill=tk.BOTH, expand=True)

        # Server instance
        self.server = ScreenShareServer(log_callback=self.log)
        self.server_thread = None
        self.log_queue = Queue()
        self.after(100, self.process_log_queue)

    def log(self, message):
        self.log_queue.put(message)

    def process_log_queue(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log_area.insert(tk.END, msg + "\n")
                self.log_area.see(tk.END)
        except Empty:
            pass
        self.after(100, self.process_log_queue)

    def start_server(self):
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        loop = asyncio.new_event_loop()
        self.server_thread = ServerThread(self.server, loop)
        self.server_thread.start()

    def stop_server(self):
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        self.server.stop()
        if self.server_thread:
            self.server_thread.stop()
            self.server_thread = None
        self.log("Server stopped")


if __name__ == "__main__":
    p = pyaudio.PyAudio()
    for i in range(p.get_device_count()):
        dev = p.get_device_info_by_index(i)
        print(
            f"Device {i}: {dev['name']}  (Max Channels: {dev['maxInputChannels']}) dsr: {dev['defaultSampleRate']} "
        )
    print(sd.query_devices())
    # print("debug!, exit")
    # exit(0)
    ui = ServerUI()
    ui.mainloop()
