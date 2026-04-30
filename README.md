# 隱藏攝影機偵測器

## 專案簡介
本專案使用 Raspberry Pi Zero 2W 搭配 NoIR Camera V2 與 IR LED，透過紅外線反射原理偵測隱藏攝影機鏡頭反光特徵。當系統判定可疑目標時，會透過 Telegram 發送通知與影像截圖。

## 硬體清單
- Raspberry Pi Zero 2W
- NoIR Camera V2
- IR LED x5
- S8050 電晶體
- 被動蜂鳴器
- 紅色 LED
- 12mm 金屬按鈕

## 系統架構
本系統以 OpenCV Hough Circle 偵測為核心，先進行灰階化前處理，再使用雙幀差分法（IR 關閉/開啟影像相減）降低環境干擾，最後對候選鏡頭反光點進行判定與告警觸發。

## 安裝方式
1. 複製範例設定檔：
   - 將 `.env.example` 複製為 `.env`
2. 編輯 `.env`，填入：
   - `TELEGRAM_TOKEN`
   - `TELEGRAM_CHAT_ID`

## 目前開發狀態
進行中，預計五月完成。
