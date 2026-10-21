"""把 notepad 的待辦文字拆成一筆一筆事項。

支援的寫法（都可以混用）：

    CAL todo                      ← 「XXX todo / 待辦」整行 = 切換客戶
    10/20                         ← 日期標題，底下的項目都是這天
    - 6400 reimage upgrade        ← 項目（- * • 1. 或 - [ ] 開頭）
       A > set interface ...      ← 縮排的行 = 上一個項目的細節（放進備註）
    10/xx                         ← 只知道月份 → 不排日期，備註寫「10 月內」
    10/28(3950 & 9200 online)     ← 括號內容 = 當天的里程碑
    10/30 交報告                  ← 日期後面直接寫事情 = 當天的一個項目
    找時間 staging 9800            ← 空行之後、沒有日期也沒有 - 的一行 = 未排日期的事項
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date

TITLE_MAX = 200

CLIENT_RE = re.compile(r"^\s*(?P<name>.+?)\s*(?:[-–:：]\s*)?(?:to\s*-?\s*do|待辦(?:事項)?)\s*[:：]?\s*$", re.I)
DATE_RE = re.compile(
    r"^\s*(?:(?P<year>\d{4})[/-])?(?P<month>\d{1,2})[/-](?P<day>\d{1,2}|xx|XX|x|X|\?\?|？？)"
    r"(?![\d/])\s*(?:[(（](?P<label>[^)）]*)[)）])?\s*(?P<rest>.*)$"
)
BULLET_RE = re.compile(r"^(?P<indent>\s*)(?:[-*•・‧+]|\d{1,2}[.)、])\s*(?:\[[ xX✓]?\]\s*)?(?P<text>.+)$")
WEEKDAY_RE = re.compile(r"^(?:週|周|星期|禮拜)?[一二三四五六日天]$|^(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\.?$", re.I)


@dataclass
class ParsedItem:
    title: str
    due_date: date | None = None
    client: str = ""
    notes: str = ""
    is_milestone: bool = False
    indent: int = field(default=0, repr=False)

    def add_note(self, line: str) -> None:
        line = line.strip()
        if line:
            self.notes = f"{self.notes}\n{line}" if self.notes else line


@dataclass
class ParseResult:
    items: list[ParsedItem]
    clients: list[str]


def _indent_width(s: str) -> int:
    return len(s.expandtabs(4)) - len(s.expandtabs(4).lstrip())


def _clean_title(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text.rstrip("。.;；,，").strip() or text


def _resolve_date(year: int | None, month: int, day: int, today: date) -> date | None:
    if not 1 <= month <= 12:
        return None
    y = year or today.year
    if not 1 <= day <= calendar.monthrange(y, month)[1]:
        return None
    d = date(y, month, day)
    # 12 月貼 1/5 → 明年；超過半年前的日期視為明年
    if year is None and (d - today).days < -183:
        try:
            d = date(y + 1, month, day)
        except ValueError:
            return None
    return d


def parse(text: str, default_client: str = "", today: date | None = None) -> ParseResult:
    today = today or date.today()
    items: list[ParsedItem] = []
    clients: list[str] = []

    client = default_client.strip()
    cur_date: date | None = None
    cur_hint = ""            # 「10 月內」這種只有月份的提示
    last: ParsedItem | None = None
    blank_before = True

    def new_item(title: str, *, indent: int = 0, milestone: bool = False, note: str = "") -> ParsedItem:
        title = _clean_title(title)
        it = ParsedItem(title=title, due_date=cur_date, client=client, is_milestone=milestone, indent=indent)
        if len(title) > TITLE_MAX:
            it.title = title[: TITLE_MAX - 1] + "…"
            it.add_note(title)
        if cur_hint:
            it.add_note(cur_hint)
        if note:
            it.add_note(note)
        items.append(it)
        return it

    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.rstrip()
        if not line.strip():
            blank_before = True
            continue

        indent = _indent_width(line)
        stripped = line.strip()

        # 1) 客戶切換：「CAL todo」「ABC 待辦」
        m = CLIENT_RE.match(stripped)
        if m and not DATE_RE.match(stripped) and not BULLET_RE.match(line) and len(m["name"]) <= 40:
            client = m["name"].strip()
            if client not in clients:
                clients.append(client)
            cur_date, cur_hint, last = None, "", None
            blank_before = False
            continue

        # 2) 日期標題
        m = DATE_RE.match(stripped)
        if m and indent <= 1:
            month = int(m["month"])
            day_raw = m["day"]
            year = int(m["year"]) if m["year"] else None
            if day_raw.isdigit():
                cur_date = _resolve_date(year, month, int(day_raw), today)
                cur_hint = "" if cur_date else f"日期待確認：{stripped.split()[0]}"
            else:
                cur_date = None
                cur_hint = f"日期未定：{month} 月內"
            last = None
            label = (m["label"] or "").strip()
            if label and not WEEKDAY_RE.match(label):
                last = new_item(label, milestone=True)
            rest = (m["rest"] or "").strip()
            if rest:
                rb = BULLET_RE.match(rest)
                last = new_item(rb["text"] if rb else rest)
            blank_before = False
            continue

        # 3) 項目
        m = BULLET_RE.match(line)
        if m:
            text_ = m["text"]
            # 比上一個項目縮排更深的子項目 → 併進上一個項目的備註
            if last is not None and not last.is_milestone and indent > last.indent:
                last.add_note(text_)
            else:
                last = new_item(text_, indent=indent)
            blank_before = False
            continue

        # 4) 一般文字行
        if last is not None and not blank_before and not last.is_milestone:
            if indent > last.indent:
                last.add_note(stripped)          # 縮排 → 細節
            else:
                last.title = _clean_title(f"{last.title} {stripped}")  # notepad 自動換行的續行
        elif not blank_before:
            # 緊接在日期標題（或里程碑）下面、沒加 - 的行 → 當天的項目
            last = new_item(stripped, indent=indent)
        else:
            # 空行之後自成一行、沒有日期 → 未排日期的事項，並結束上一個日期區塊
            cur_date, cur_hint = None, ""
            last = new_item(stripped, indent=indent)
        blank_before = False

    if default_client and default_client not in clients:
        clients.insert(0, default_client)
    return ParseResult(items=items, clients=clients)
