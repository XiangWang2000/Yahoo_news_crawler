# Yahoo_news_crawler
本專案使用 **Scrapy** 建立 Yahoo 台灣新聞爬蟲，用於收集「指定時間窗口內」的新聞資料（預設為近一小時）。

資料來源為 Yahoo News 的內部 ListService archive API，並進一步進入文章頁取得作者資訊。
## 功能特色
- 抓取 Yahoo 台灣新聞 archive feed
- 可自訂抓取時間窗口（預設近 1 小時）
- 自動分頁與停止判斷
- 去除重複新聞
- 進入文章頁抓取作者資訊
- 使用 buffer 機制避免 Yahoo 更新延遲造成缺漏
- 輸出 Excel 可直接開啟的 UTF-8 CSV
- 支援多種時間窗口定義模式

可透過 -a 傳入參數。

## 1.mode
決定「時間窗口的結束時間」。
| mode   | 說明                  |
| ------ | ------------------- |
| start  | 以程式啟動時間為窗口上界        |
| finish | 以程式結束時間為窗口上界        |
| newest | 以抓到的最新新聞時間為窗口上界（推薦） |

`scrapy crawl yahoo_news_last_hour -a mode=newest`

## 2.hours
要收集的時間範圍（單位：小時）。

## 3.buffer_minutes
Yahoo archive 列表可能出現：

- 更新延遲
- 排序混合
- 快照落差

因此爬蟲會先多抓一段時間，再於最後精準裁切

爬蟲會輸出 CSV 檔案
檔名包含：

- 抓取時間長度
- 本次窗口時間戳

# 已知限制
- 部分新聞沒有作者資訊
- 抓作者需進入文章頁，因此速度較慢
- 每小時新聞數量會隨時段波動

