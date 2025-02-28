# Screen Share Server

這是一個基於 WebRTC 的螢幕分享伺服器，使用 Python 編寫。此專案僅為興趣而做，在我測試時不能輸出聲音。

## 功能

- 捕捉螢幕並通過 WebRTC 傳輸
- 捕捉系統音效並通過 WebRTC 傳輸（目前無法正常工作）

## 依賴

請確保已安裝以下 Python 套件：

- argparse
- asyncio
- json
- logging
- threading
- tkinter
- sounddevice
- numpy
- mss
- pyaudio
- aiohttp
- aiortc
- av

可以使用以下命令安裝所需的套件(請自行配置venv如有需要)：


```sh
pip install argparse asyncio json logging threading tkinter sounddevice numpy mss pyaudio aiohttp aiortc av
```

使用方法
克隆此專案到本地：

```sh
git clone https://github.com/xva0001/python_webrtc_network_projection_no_sound.git
```

進入專案目錄：

```sh
cd ur-dir
```


運行伺服器 ：

```sh
python app.py
```

開啟瀏覽器並訪問 http://your-server-ip:8080 以查看螢幕分享

注意事項
此專案僅為興趣而做，並不保證所有功能正常運作。
在我測試時，音效捕捉功能無法正常工作。
貢獻
歡迎提交問題和請求合併。請確保在提交前已閱讀並遵守貢獻指南。
授權
此專案採用 MIT 授權條款。詳情請參閱 LICENSE 文件。

希望這對你有幫助！
希望這對你有幫助！
