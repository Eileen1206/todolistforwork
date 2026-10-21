# 客戶待辦

把 notepad 的待辦貼進來 → 拆成一筆一筆 → 依客戶分組 → 有日期的自動加到 Google Calendar 的專用日曆；每月固定要做的事設一次就會一直出現。

架構跟打卡系統一樣：Django + gunicorn + Railway Postgres，設定全部走環境變數。**請開一個新的 Railway 專案**，不要跟送貨系統共用資料庫。

## 功能

| 頁面 | 做什麼 |
|---|---|
| 待辦 | 上方客戶標籤一鍵篩選；依「逾期／今天／7 天內／之後／未排日期」分區；勾選即完成；頂端一行快速新增 |
| 貼上 | 整段 notepad 貼上 → 預覽表可改標題、客戶、日期、里程碑、取消勾選 → 建立 |
| 每月循環 | 每月 1–28 號或月底；網站自動放這個月和下個月那一次，日曆上是一個每月重複事件 |
| 客戶 | 設每個客戶的日曆顏色（網站標籤也用同一色） |

### 貼上時看得懂的寫法

```
CAL todo                     ← 「XXX todo / 待辦」= 客戶（一次可貼多個客戶）
10/20                        ← 日期標題，底下的項目都是這天
- 6400 reimage upgrade
- 補貼3950設定於
    A > set interface ...    ← 縮排 = 這件事的備註
10/xx                        ← 只知道月份 → 未排日期，備註「10 月內」
10/28(3950 & 9200 online)    ← 括號 = 當天的里程碑 ★（(三) 這種星期會略過）
10/30 交報告                 ← 日期後面直接寫 = 當天一件事
                             ← 空一行
找時間 staging 9800          ← 沒日期、沒 - 的獨立一行 = 未排日期
```

日期沒寫年份時用今年；超過半年前的日期當作明年（12 月貼 `1/5` 會變明年 1/5）。

## 部署到 Railway

1. 把這個資料夾推到一個新的 GitHub repo（`.gitignore` 已排除金鑰和 db）。
2. Railway → New Project → Deploy from GitHub repo → 選這個 repo。
3. 同一個專案裡 **+ New → Database → PostgreSQL**。
4. Web 服務 → **Variables**：

   | 變數 | 值 |
   |---|---|
   | `DATABASE_URL` | `${{Postgres.DATABASE_URL}}` |
   | `SECRET_KEY` | 一串長亂數（`python -c "import secrets;print(secrets.token_urlsafe(50))"`） |
   | `DJANGO_ADMIN_USER` | 登入帳號 |
   | `DJANGO_ADMIN_PASSWORD` | 至少 10 字 |
   | `GOOGLE_CALENDAR_ID` / `GOOGLE_SERVICE_ACCOUNT_JSON` | 見下一節（可以晚點再設） |
   | `SITE_URL` | 部署後的網址，日曆事件描述裡會附回網站的連結（選填） |

5. Settings → Networking → **Generate Domain**。
6. 啟動指令在 `Procfile`：每次部署會自動 migrate、collectstatic、用上面的帳密建立登入帳號（已存在就不動）。

> 自訂網域時，把網域加進 `ALLOWED_HOSTS` 和 `CSRF_TRUSTED_ORIGINS`（逗號分隔，後者要含 `https://`）。預設已信任 `*.up.railway.app`。

## 接 Google Calendar（service account，免 OAuth 登入流程）

1. [Google Cloud Console](https://console.cloud.google.com/) 開一個專案 → **API 和服務 → 程式庫** → 啟用 **Google Calendar API**。
2. **IAM 與管理 → 服務帳戶** → 建立服務帳戶（不用給任何角色）→ 進去 **金鑰 → 新增金鑰 → JSON**，下載。
3. Google Calendar → 左側「其他日曆」**＋ 建立新日曆**，例如「客戶工作」——這就是「分類」。
4. 這個日曆的 **設定 → 與特定使用者共用** → 加入服務帳戶的 email（`xxx@xxx.iam.gserviceaccount.com`），權限選 **變更活動**。
5. 同一頁 **整合日曆** 底下複製 **日曆 ID** → 填到 `GOOGLE_CALENDAR_ID`。
6. 把下載的 JSON 整個內容貼到 `GOOGLE_SERVICE_ACCOUNT_JSON`。

設定好之後，儀表板上方會顯示還沒同步的筆數，按 **重新同步** 會把之前建的一次補上。

- 用個人 Gmail 建 Cloud 專案。公司 Workspace 帳號常常被組織政策禁止建立服務帳戶金鑰。
- 金鑰等於這個日曆的寫入權限：只放在 Railway Variables，不要 commit。

### 日曆上長什麼樣

- 全天事件，不佔用「忙碌」時段；標題 `[CAL] 事項`，里程碑 `★ [CAL] …`，完成後變 `✔ [CAL] …`
- 顏色 = 客戶設定的顏色；備註寫在事件描述
- 改日期／改內容會同步更新；清掉日期或刪除待辦 → 日曆事件一起刪
- 每月循環 = 一個 `↻ [CAL] …` 每月重複事件；網站裡勾掉某一次不會影響日曆
- 單向同步：在 Google Calendar 上改事件不會回寫網站
- Google 掛掉或逾時（10 秒）不會卡住網站，失敗的會標「日曆未同步」，之後按重新同步

## 本機跑

```bash
pip install -r requirements.txt
export DEBUG=1
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
python manage.py test tasks     # 19 個測試，含你那份 CAL todo 的解析
```

## 檔案

```
config/settings.py        環境變數設定、Railway CSRF、HTTPS cookie
tasks/parser.py           notepad 文字 → 事項（規則在檔案開頭）
tasks/gcal.py             Google Calendar：新增/更新/刪除、batch、逾時、失敗記錄
tasks/recurring.py        每月循環：產生這個月＋下個月那一次（不需要 cron）
tasks/views.py            頁面
tasks/tests.py            測試（Google 用假的 service，不連網）
Procfile                  Railway 啟動指令
```
