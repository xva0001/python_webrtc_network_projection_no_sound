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

class AudioTrack2(MediaStreamTrack):
    """
    利用 sounddevice 捕捉音訊，並將其轉換為 AudioFrame 供 WebRTC 傳送。
    捕捉系統音效並透過內部的 queue 傳送 callback 取得的音訊數據。
    """

    kind = "audio"

    def __init__(self):
        super().__init__()
        self.samplerate = 44100  # 指定採樣率
        self.channels = 2  # 固定雙聲道以匹配後續的 stereo layout
        self.blocksize = 1024  # 和 AudioFrame samples 匹配
        self._queue = asyncio.Queue(maxsize=10)
        
        # 使用 PyAudio 來獲取系統聲音
        self.audio = pyaudio.PyAudio()
        
        # 尋找一個合適的音頻輸入設備（優先找 WASAPI 或系統聲音相關設備）
        wasapi_loopback_device = None
        default_device = 1
        device_index = 1        
        # for i in range(self.audio.get_device_count()):
        #     dev = self.audio.get_device_info_by_index(i)
        #     dev_name = dev['name'].lower()
        #     print(f"檢查設備 {i}: {dev['name']}")
            
        #     # 尋找帶有 loopback, stereo mix 或其他系統聲音相關字詞的設備
        #     if dev['maxInputChannels'] > 0:
        #         if default_device is None:
        #             default_device = i
                
        #         if any(keyword in dev_name for keyword in ['wasapi', 'loopback', 'stereo mix', 'what u hear', 'system']):
        #             wasapi_loopback_device = i
        #             print(f"找到可能的系統聲音設備: {i}: {dev['name']}")
        #             break
        
        # 選擇最佳設備
        # device_index = wasapi_loopback_device if wasapi_loopback_device is not None else default_device
        
        # if device_index is None:
        #     # print("警告: 未找到合適的輸入設備，可能無法捕獲音頻")
        #     device_index = 1
        #     return
            
        dev_info = self.audio.get_device_info_by_index(device_index)
        print(f"使用音頻設備: {device_index}: {dev_info['name']}")
        
        # 設置設備參數
        self.stream = self.audio.open(
            format=pyaudio.paInt16,
            channels=min(2, dev_info['maxInputChannels']),  # 確保不超過設備支援的最大聲道
            rate=int(dev_info['defaultSampleRate']),
            input=True,
            frames_per_buffer=self.blocksize,
            input_device_index=device_index,
            stream_callback=self._audio_callback
        )
        
        print(f"音頻流已啟動: {min(2, dev_info['maxInputChannels'])}聲道, {int(dev_info['defaultSampleRate'])}Hz")
        
    def _audio_callback(self, in_data, frame_count, time_info, status):
        if status:
            print(f"音頻狀態錯誤: {status}")
            
        try:
            if not self._queue.full():
                self._queue.put_nowait(in_data)
        except Exception as e:
            print(f"處理音頻時出錯: {e}")
            
        return None, pyaudio.paContinue

    async def recv(self):
        pts, time_base = await self.next_timestamp()

        try:
            # 等待隊列中的音頻數據，設置超時
            data = await asyncio.wait_for(self._queue.get(), timeout=0.5)

            # 確保有足夠的數據填充音頻幀
            if len(data) < self.blocksize * self.channels * 2:  # 16-bit = 2 bytes per sample
                # 如果數據不足，用零填充
                padding = bytes(self.blocksize * self.channels * 2 - len(data))
                data = data + padding
                print(f"數據大小不足，進行填充. 原始大小: {len(data) - len(padding)}")

            # 檢查數據有效性
            samples = np.frombuffer(data, dtype=np.int16)
            if np.max(np.abs(samples)) > 0:
              print(f"有效音頻數據: 最大振幅 = {np.max(np.abs(samples))}")
        except asyncio.TimeoutError:
            print("等待音頻數據超時，生成靜音")
            data = bytes(self.blocksize * self.channels * 2)
        except Exception as e:
            print(f"獲取音頻數據時出錯: {e}")
            data = bytes(self.blocksize * self.channels * 2)

        # 創建不同格式音頻幀嘗試解決兼容性問題
        try:
            # 創建音頻幀並設置重要屬性
            layout = "stereo" if self.channels == 2 else "mono"
            audio_frame = AudioFrame(format="s16", layout=layout, samples=self.blocksize)
            audio_frame.pts = pts
            audio_frame.time_base = time_base
            audio_frame.sample_rate = self.samplerate

            # 更新音頻數據
            audio_frame.planes[0].update(data)

            return audio_frame
        except Exception as e:
            print(f"創建音頻幀時出錯: {e}")
        # 創建一個基本的空白幀作為後備
            audio_frame = AudioFrame(format="s16", layout="stereo", samples=self.blocksize)
            audio_frame.pts = pts
            audio_frame.time_base = time_base
            return audio_frame
        
    # 添加清理方法
    def stop(self):
        if hasattr(self, 'stream') and self.stream:
            self.stream.stop_stream()
            self.stream.close()
        
        if hasattr(self, 'audio') and self.audio:
            self.audio.terminate()