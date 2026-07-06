# -*- coding: utf-8 -*-
"""
포레스쿨 무역창고 QR 재고관리
- v1.5: 창고 페이지 분리, 한국포레스쿨창고 판매 차감, 100개 미만 알림 기능 추가
- 스마트스토어/쿠팡 판매재고가 아니라 창고 부자재/포장재/무역 자재 관리용입니다.
- Python 표준 라이브러리 + qrcode/Pillow만 사용합니다.
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import os
import re
import shutil
import socket
import sqlite3
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
import uuid
import webbrowser
import zipfile
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import qrcode
except Exception:  # pragma: no cover
    # Windows에서 배치파일 인코딩/경로 문제로 패키지 설치가 누락된 경우를 대비해 자동 설치를 한 번 시도합니다.
    import subprocess
    print("qrcode/Pillow 패키지가 없어 자동 설치를 시도합니다...")
    req_path = Path(__file__).resolve().parent / "requirements.txt"
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", str(req_path)])
        import qrcode
    except Exception:
        print("패키지 자동 설치에 실패했습니다. 인터넷 연결과 Python 설치를 확인한 뒤 START_HERE.cmd를 다시 실행하세요.")
        raise

APP_NAME = "무역창고 QR 재고관리"
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
RUNNING_ON_RAILWAY = bool(os.environ.get("RAILWAY_PROJECT_ID") or os.environ.get("RAILWAY_ENVIRONMENT_NAME"))
PORT = int(os.environ.get("PORT") or os.environ.get("FORESCHOOL_INV_PORT") or "8723")
PUBLIC_BASE_URL = (os.environ.get("PUBLIC_BASE_URL") or "").strip().rstrip("/")
ADMIN_PIN = str(os.environ.get("ADMIN_PIN") or os.environ.get("FORESCHOOL_ADMIN_PIN") or "1204")
SECRET_KEY = str(os.environ.get("SECRET_KEY") or hashlib.sha256((ADMIN_PIN + APP_NAME).encode("utf-8")).hexdigest())
AUTO_OPEN_BROWSER = (os.environ.get("AUTO_OPEN_BROWSER") or ("0" if RUNNING_ON_RAILWAY else "1")) != "0"
DEFAULT_WAREHOUSE = os.environ.get("DEFAULT_WAREHOUSE", "무역창고")
KOREA_WAREHOUSE = os.environ.get("KOREA_WAREHOUSE", "한국포레스쿨창고")
LOW_STOCK_THRESHOLD = float(os.environ.get("LOW_STOCK_THRESHOLD", "100") or 100)
ALERT_WEBHOOK_URL = (os.environ.get("KAKAO_WORK_WEBHOOK_URL") or os.environ.get("ALERT_WEBHOOK_URL") or "").strip()
ALERT_COOLDOWN_SECONDS = int(os.environ.get("ALERT_COOLDOWN_SECONDS", "3600") or 3600)

# GitHub 웹 업로드에서 static 폴더를 빼먹어도 관리자 화면이 열리도록 내장 HTML을 함께 둡니다.
EMBEDDED_INDEX_HTML = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>무역창고 QR 재고관리</title><style>body{font-family:system-ui,sans-serif;background:#f4f1ea;margin:0;padding:24px}.card{max-width:520px;margin:40px auto;background:#fffdf8;border:1px solid #e2ded2;border-radius:20px;padding:24px}a{display:inline-block;background:#2d5a45;color:white;padding:12px 14px;border-radius:12px;text-decoration:none;font-weight:800}</style></head><body><div class="card"><h1>무역창고 QR 재고관리</h1><p>관리자 화면 파일이 누락되었습니다. GitHub에 index.html, app.css, app.js 또는 static 폴더를 다시 업로드해주세요.</p><a href="/api/me">서버 상태 확인</a></div></body></html>"""


def pick_writable_dir() -> Path:
    candidates = []
    if os.environ.get("DATA_DIR"):
        candidates.append(Path(os.environ["DATA_DIR"]))
    if os.environ.get("RAILWAY_VOLUME_MOUNT_PATH"):
        candidates.append(Path(os.environ["RAILWAY_VOLUME_MOUNT_PATH"]))
    if RUNNING_ON_RAILWAY:
        candidates.append(Path("/data"))
    candidates.append(BASE_DIR / "data")
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write_test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return candidate
        except Exception:
            continue
    tmp = Path("/tmp/foreschool_trade_inventory")
    tmp.mkdir(parents=True, exist_ok=True)
    return tmp


DATA_DIR = pick_writable_dir()
BACKUP_DIR = Path(os.environ.get("BACKUP_DIR") or str(DATA_DIR / "backups"))
DB_PATH = DATA_DIR / "warehouse_inventory.sqlite3"
PHOTO_DIR = Path(os.environ.get("PHOTO_DIR") or str(DATA_DIR / "photos"))

BACKUP_DIR.mkdir(parents=True, exist_ok=True)
PHOTO_DIR.mkdir(parents=True, exist_ok=True)

try:
    STATIC_DIR.mkdir(exist_ok=True)
except Exception:
    pass  # Railway/GitHub 배포 환경에서 static 폴더 생성 권한이 없어도 앱은 내장 화면으로 실행됩니다.

KST = timezone(timedelta(hours=9))
KST_FORMAT = "%Y-%m-%d %H:%M:%S"


def now_text() -> str:
    return datetime.now(KST).strftime(KST_FORMAT)


def session_token() -> str:
    return hmac.new(SECRET_KEY.encode("utf-8"), b"foreschool-warehouse-admin", hashlib.sha256).hexdigest()


def pin_matches(pin: Any) -> bool:
    return hmac.compare_digest(str(pin or ""), ADMIN_PIN)


def clean_qr(raw: str) -> str:
    """QR 스캔값에서 FSW-xxxx 형태 코드만 최대한 추출."""
    if not raw:
        return ""
    raw = urllib.parse.unquote(str(raw).strip())
    match = re.search(r"(FSW-[A-Z0-9]{6,16})", raw, re.I)
    if match:
        return match.group(1).upper()
    # URL 마지막 경로를 코드처럼 사용
    if "/scan/" in raw:
        tail = raw.rsplit("/scan/", 1)[-1].split("?", 1)[0].split("#", 1)[0]
        return tail.strip().upper()
    return raw.strip().upper()


def make_qr_code() -> str:
    return "FSW-" + uuid.uuid4().hex[:8].upper()


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def dict_row(row: sqlite3.Row) -> Dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(str(value).replace(",", ""))
    except Exception:
        return default


def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                qr_code TEXT NOT NULL UNIQUE,
                warehouse TEXT DEFAULT '무역창고',
                name TEXT NOT NULL,
                category TEXT DEFAULT '',
                spec TEXT DEFAULT '',
                unit TEXT DEFAULT '개',
                location TEXT DEFAULT '',
                supplier TEXT DEFAULT '',
                pack_qty REAL DEFAULT 1,
                min_stock REAL DEFAULT 0,
                stock_qty REAL DEFAULT 0,
                status TEXT DEFAULT '사용중',
                low_alert_enabled INTEGER DEFAULT 1,
                last_low_alert_at TEXT DEFAULT '',
                memo TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_count_at TEXT DEFAULT '',
                photo_filename TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS movements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                qr_code TEXT NOT NULL,
                item_name TEXT NOT NULL,
                action TEXT NOT NULL,
                qty REAL NOT NULL,
                before_qty REAL NOT NULL,
                after_qty REAL NOT NULL,
                reason TEXT DEFAULT '',
                worker TEXT DEFAULT '',
                ref_no TEXT DEFAULT '',
                memo TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY(item_id) REFERENCES items(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                qr_code TEXT NOT NULL,
                item_name TEXT NOT NULL,
                warehouse TEXT DEFAULT '',
                stock_qty REAL NOT NULL,
                threshold_qty REAL NOT NULL,
                channel TEXT DEFAULT 'webhook',
                message TEXT DEFAULT '',
                success INTEGER DEFAULT 0,
                error TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_items_qr ON items(qr_code);
            CREATE INDEX IF NOT EXISTS idx_items_warehouse ON items(warehouse);
            CREATE INDEX IF NOT EXISTS idx_items_name ON items(name);
            CREATE INDEX IF NOT EXISTS idx_items_location ON items(location);
            CREATE INDEX IF NOT EXISTS idx_movements_item ON movements(item_id);
            CREATE INDEX IF NOT EXISTS idx_movements_created ON movements(created_at);
            """
        )
        # 기존 DB를 쓰는 경우에도 사진 컬럼이 자동으로 추가되도록 보강합니다.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(items)").fetchall()}
        if "warehouse" not in cols:
            conn.execute("ALTER TABLE items ADD COLUMN warehouse TEXT DEFAULT '무역창고'")
            conn.execute("UPDATE items SET warehouse=? WHERE warehouse IS NULL OR warehouse=''", (DEFAULT_WAREHOUSE,))
        if "low_alert_enabled" not in cols:
            conn.execute("ALTER TABLE items ADD COLUMN low_alert_enabled INTEGER DEFAULT 1")
        if "last_low_alert_at" not in cols:
            conn.execute("ALTER TABLE items ADD COLUMN last_low_alert_at TEXT DEFAULT ''")
        if "photo_filename" not in cols:
            conn.execute("ALTER TABLE items ADD COLUMN photo_filename TEXT DEFAULT ''")
        count = conn.execute("SELECT COUNT(*) AS c FROM items").fetchone()["c"]
        if count == 0:
            seed_samples(conn)


def seed_samples(conn: sqlite3.Connection) -> None:
    samples = [
        {
            "qr_code": "FSW-SAMPLE01",
            "name": "OPP 봉투 10x15cm",
            "category": "포장재",
            "spec": "10x15cm / 투명 / 접착형",
            "unit": "장",
            "location": "A-01",
            "supplier": "샘플 거래처",
            "pack_qty": 100,
            "min_stock": 0,
            "stock_qty": 1200,
            "memo": "샘플 데이터입니다. 실제 남은 부자재로 수정해서 사용하세요.",
        },
        {
            "qr_code": "FSW-SAMPLE02",
            "name": "자개 스티커 백카드",
            "category": "종이부자재",
            "spec": "A6 접이식 카드 / 무지",
            "unit": "장",
            "location": "B-02",
            "supplier": "샘플 거래처",
            "pack_qty": 50,
            "min_stock": 0,
            "stock_qty": 160,
            "memo": "발주 후 남은 재고를 기록하는 예시입니다.",
        },
        {
            "qr_code": "FSW-SAMPLE03",
            "name": "택배박스 소형",
            "category": "박스",
            "spec": "220x160x80mm",
            "unit": "개",
            "location": "C-01",
            "supplier": "샘플 거래처",
            "pack_qty": 25,
            "min_stock": 0,
            "stock_qty": 250,
            "memo": "QR 라벨 출력 후 박스 보관칸에 붙여보세요.",
        },
    ]
    t = now_text()
    for s in samples:
        conn.execute(
            """
            INSERT INTO items
            (qr_code, name, category, spec, unit, location, supplier, pack_qty, min_stock, stock_qty, status, memo, created_at, updated_at)
            VALUES (:qr_code, :name, :category, :spec, :unit, :location, :supplier, :pack_qty, :min_stock, :stock_qty,
                    '사용중', :memo, :created_at, :updated_at)
            """,
            {**s, "created_at": t, "updated_at": t},
        )
        item_id = conn.execute("SELECT id FROM items WHERE qr_code=?", (s["qr_code"],)).fetchone()["id"]
        conn.execute(
            """
            INSERT INTO movements
            (item_id, qr_code, item_name, action, qty, before_qty, after_qty, reason, worker, memo, created_at)
            VALUES (?, ?, ?, '초기등록', ?, 0, ?, '초기 샘플 재고', 'system', ?, ?)
            """,
            (item_id, s["qr_code"], s["name"], s["stock_qty"], s["stock_qty"], s["memo"], t),
        )


def backup_db(reason: str = "manual") -> Path:
    if not DB_PATH.exists():
        raise FileNotFoundError("DB 파일이 아직 없습니다.")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = BACKUP_DIR / f"warehouse_inventory_{reason}_{ts}.sqlite3"
    shutil.copy2(DB_PATH, out)
    # 백업 40개까지만 유지
    backups = sorted(BACKUP_DIR.glob("warehouse_inventory_*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in backups[40:]:
        try:
            old.unlink()
        except Exception:
            pass
    return out


def startup_backup() -> None:
    if DB_PATH.exists():
        try:
            backup_db("startup")
        except Exception:
            pass


def get_local_ips() -> List[str]:
    ips = ["127.0.0.1"]
    try:
        hostname = socket.gethostname()
        for item in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = item[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except Exception:
        pass
    # 외부 연결 없이 라우팅 기준 IP 추정
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip not in ips:
            ips.append(ip)
    except Exception:
        pass
    return ips


def item_by_qr(conn: sqlite3.Connection, qr: str) -> Optional[sqlite3.Row]:
    qr = clean_qr(qr)
    return conn.execute("SELECT * FROM items WHERE qr_code=?", (qr,)).fetchone()


def fetch_item(conn: sqlite3.Connection, item_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()


def list_items(params: Dict[str, str]) -> List[Dict[str, Any]]:
    where = []
    args: List[Any] = []
    keyword = params.get("keyword", "").strip()
    category = params.get("category", "").strip()
    location = params.get("location", "").strip()
    status = params.get("status", "").strip()
    warehouse = params.get("warehouse", "").strip()
    zero = params.get("zero", "").strip()

    if keyword:
        where.append("(name LIKE ? OR qr_code LIKE ? OR spec LIKE ? OR memo LIKE ? OR supplier LIKE ?)")
        like = f"%{keyword}%"
        args.extend([like, like, like, like, like])
    if category:
        where.append("category = ?")
        args.append(category)
    if warehouse:
        where.append("COALESCE(NULLIF(warehouse,''), ?) = ?")
        args.extend([DEFAULT_WAREHOUSE, warehouse])
    if location:
        where.append("location LIKE ?")
        args.append(f"%{location}%")
    if status:
        where.append("status = ?")
        args.append(status)
    else:
        where.append("status != '숨김'")
    if zero == "1":
        where.append("stock_qty <= 0")

    sql = "SELECT * FROM items"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY location, category, name"
    with get_conn() as conn:
        rows = conn.execute(sql, args).fetchall()
    return [item_dict(r) for r in rows]


def dashboard(params: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    params = params or {}
    warehouse = params.get("warehouse", "").strip()
    where = "status != '숨김'"
    args: List[Any] = []
    if warehouse:
        where += " AND COALESCE(NULLIF(warehouse,''), ?) = ?"
        args.extend([DEFAULT_WAREHOUSE, warehouse])
    with get_conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) c FROM items WHERE {where}", args).fetchone()["c"]
        zero = conn.execute(f"SELECT COUNT(*) c FROM items WHERE {where} AND stock_qty <= 0", args).fetchone()["c"]
        below100 = conn.execute(f"SELECT COUNT(*) c FROM items WHERE {where} AND stock_qty < ?", args + [LOW_STOCK_THRESHOLD]).fetchone()["c"]
        location_count = conn.execute(f"""
            SELECT COUNT(*) c FROM (
                SELECT COALESCE(NULLIF(location,''),'위치미정') AS loc
                FROM items WHERE {where}
                GROUP BY COALESCE(NULLIF(location,''),'위치미정')
            )
        """, args).fetchone()["c"]
        categories = [dict_row(r) for r in conn.execute(
            f"""
            SELECT COALESCE(NULLIF(category,''),'미분류') AS name, COUNT(*) AS item_count, SUM(stock_qty) AS qty_sum
            FROM items WHERE {where}
            GROUP BY COALESCE(NULLIF(category,''),'미분류')
            ORDER BY item_count DESC, name
            """, args
        ).fetchall()]
        locations = [dict_row(r) for r in conn.execute(
            f"""
            SELECT COALESCE(NULLIF(location,''),'위치미정') AS name, COUNT(*) AS item_count, SUM(stock_qty) AS qty_sum
            FROM items WHERE {where}
            GROUP BY COALESCE(NULLIF(location,''),'위치미정')
            ORDER BY name
            """, args
        ).fetchall()]
        warehouses = [dict_row(r) for r in conn.execute(
            """
            SELECT COALESCE(NULLIF(warehouse,''),?) AS name, COUNT(*) AS item_count, SUM(stock_qty) AS qty_sum
            FROM items WHERE status != '숨김'
            GROUP BY COALESCE(NULLIF(warehouse,''),?)
            ORDER BY CASE WHEN name=? THEN 0 WHEN name=? THEN 1 ELSE 2 END, name
            """, (DEFAULT_WAREHOUSE, DEFAULT_WAREHOUSE, KOREA_WAREHOUSE, DEFAULT_WAREHOUSE)
        ).fetchall()]
        low_korea = [item_dict(r) for r in conn.execute(
            """
            SELECT * FROM items
            WHERE status != '숨김' AND COALESCE(NULLIF(warehouse,''),?)=? AND stock_qty < ?
            ORDER BY stock_qty ASC, name LIMIT 20
            """, (DEFAULT_WAREHOUSE, KOREA_WAREHOUSE, LOW_STOCK_THRESHOLD)
        ).fetchall()]
        recent = [dict_row(r) for r in conn.execute(
            "SELECT * FROM movements ORDER BY id DESC LIMIT 20"
        ).fetchall()]
    return {"total": total, "zero": zero, "below100": below100, "location_count": location_count, "categories": categories, "locations": locations, "warehouses": warehouses, "low_korea": low_korea, "recent": recent, "threshold": LOW_STOCK_THRESHOLD, "selected_warehouse": warehouse}

def create_or_update_item(data: Dict[str, Any]) -> Dict[str, Any]:
    item_id = int(data.get("id") or 0)
    name = str(data.get("name") or "").strip()
    if not name:
        raise ValueError("자재명은 필수입니다.")
    fields = {
        "qr_code": clean_qr(data.get("qr_code") or make_qr_code()),
        "warehouse": str(data.get("warehouse") or DEFAULT_WAREHOUSE).strip(),
        "name": name,
        "category": str(data.get("category") or "").strip(),
        "spec": str(data.get("spec") or "").strip(),
        "unit": str(data.get("unit") or "개").strip(),
        "location": str(data.get("location") or "").strip(),
        "supplier": str(data.get("supplier") or "").strip(),
        "pack_qty": to_float(data.get("pack_qty"), 1),
        # 현재 버전은 부족 기준수량을 사용하지 않습니다. 기존 DB 호환을 위해 컬럼만 0으로 유지합니다.
        "min_stock": 0,
        "stock_qty": to_float(data.get("stock_qty"), 0),
        "status": str(data.get("status") or "사용중").strip(),
        "low_alert_enabled": 1 if str(data.get("low_alert_enabled", "1")) not in {"0", "false", "False", "off"} else 0,
        "memo": str(data.get("memo") or "").strip(),
        "updated_at": now_text(),
    }
    with get_conn() as conn:
        if item_id:
            old = fetch_item(conn, item_id)
            if not old:
                raise ValueError("수정할 자재를 찾을 수 없습니다.")
            before = float(old["stock_qty"])
            conn.execute(
                """
                UPDATE items SET
                    qr_code=:qr_code, warehouse=:warehouse, name=:name, category=:category, spec=:spec, unit=:unit,
                    location=:location, supplier=:supplier, pack_qty=:pack_qty, min_stock=:min_stock,
                    stock_qty=:stock_qty, status=:status, low_alert_enabled=:low_alert_enabled, memo=:memo, updated_at=:updated_at
                WHERE id=:id
                """,
                {**fields, "id": item_id},
            )
            if before != fields["stock_qty"]:
                conn.execute(
                    """
                    INSERT INTO movements
                    (item_id, qr_code, item_name, action, qty, before_qty, after_qty, reason, worker, memo, created_at)
                    VALUES (?, ?, ?, '관리자수정', ?, ?, ?, '자재 정보 화면에서 수량 수정', ?, ?, ?)
                    """,
                    (item_id, fields["qr_code"], fields["name"], fields["stock_qty"] - before, before, fields["stock_qty"], str(data.get("worker") or ""), fields["memo"], now_text()),
                )
        else:
            fields["created_at"] = now_text()
            conn.execute(
                """
                INSERT INTO items
                (qr_code, warehouse, name, category, spec, unit, location, supplier, pack_qty, min_stock, stock_qty, status, low_alert_enabled, memo, created_at, updated_at)
                VALUES (:qr_code, :warehouse, :name, :category, :spec, :unit, :location, :supplier, :pack_qty, :min_stock, :stock_qty,
                        :status, :low_alert_enabled, :memo, :created_at, :updated_at)
                """,
                fields,
            )
            item_id = int(conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"])
            conn.execute(
                """
                INSERT INTO movements
                (item_id, qr_code, item_name, action, qty, before_qty, after_qty, reason, worker, memo, created_at)
                VALUES (?, ?, ?, '초기등록', ?, 0, ?, '신규 자재 등록', ?, ?, ?)
                """,
                (item_id, fields["qr_code"], fields["name"], fields["stock_qty"], fields["stock_qty"], str(data.get("worker") or ""), fields["memo"], now_text()),
            )
        row = fetch_item(conn, item_id)
        return item_dict(row)


def apply_movement(data: Dict[str, Any]) -> Dict[str, Any]:
    item_id = int(data.get("item_id") or 0)
    qr_code = clean_qr(data.get("qr_code") or "")
    action = str(data.get("action") or "OUT").upper().strip()
    qty = to_float(data.get("qty"), 0)
    if qty < 0:
        raise ValueError("수량은 0보다 커야 합니다.")
    if action not in {"IN", "OUT", "SALE", "SET"}:
        raise ValueError("처리 구분은 IN, OUT, SALE, SET 중 하나여야 합니다.")
    with get_conn() as conn:
        row = fetch_item(conn, item_id) if item_id else item_by_qr(conn, qr_code)
        if not row:
            raise ValueError("QR 또는 자재를 찾을 수 없습니다.")
        before = float(row["stock_qty"])
        if action == "IN":
            after = before + qty
            action_label = "입고"
        elif action in {"OUT", "SALE"}:
            after = before - qty
            action_label = "판매" if action == "SALE" else "사용/출고"
            if after < 0:
                raise ValueError(f"현재 재고는 {before:g}{row['unit']}입니다. 출고 후 재고가 음수가 됩니다.")
        else:
            after = qty
            action_label = "실사수정"
        t = now_text()
        conn.execute("UPDATE items SET stock_qty=?, updated_at=?, last_count_at=CASE WHEN ?='SET' THEN ? ELSE last_count_at END WHERE id=?", (after, t, action, t, row["id"]))
        conn.execute(
            """
            INSERT INTO movements
            (item_id, qr_code, item_name, action, qty, before_qty, after_qty, reason, worker, ref_no, memo, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"], row["qr_code"], row["name"], action_label, qty, before, after,
                str(data.get("reason") or "").strip(),
                str(data.get("worker") or "").strip(),
                str(data.get("ref_no") or "").strip(),
                str(data.get("memo") or "").strip(),
                t,
            ),
        )
        new_row = fetch_item(conn, row["id"])
        alert = maybe_send_low_stock_alert(conn, new_row, before, after, action_label)
        new_row = fetch_item(conn, row["id"])
        return {"item": item_dict(new_row), "movement": {"action": action_label, "qty": qty, "before_qty": before, "after_qty": after, "created_at": t}, "alert": alert}


def recent_movements(params: Dict[str, str]) -> List[Dict[str, Any]]:
    limit = min(max(int(params.get("limit", "50") or 50), 1), 500)
    item_id = int(params.get("item_id", "0") or 0)
    with get_conn() as conn:
        if item_id:
            rows = conn.execute("SELECT * FROM movements WHERE item_id=? ORDER BY id DESC LIMIT ?", (item_id, limit)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM movements ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [item_dict(r) for r in rows]


def categories_and_locations() -> Dict[str, List[str]]:
    with get_conn() as conn:
        cats = [r[0] for r in conn.execute("SELECT DISTINCT category FROM items WHERE category!='' ORDER BY category").fetchall()]
        locs = [r[0] for r in conn.execute("SELECT DISTINCT location FROM items WHERE location!='' ORDER BY location").fetchall()]
        suppliers = [r[0] for r in conn.execute("SELECT DISTINCT supplier FROM items WHERE supplier!='' ORDER BY supplier").fetchall()]
        warehouses = [r[0] for r in conn.execute("SELECT DISTINCT COALESCE(NULLIF(warehouse,''),?) FROM items ORDER BY 1", (DEFAULT_WAREHOUSE,)).fetchall()]
    for w in [KOREA_WAREHOUSE, DEFAULT_WAREHOUSE]:
        if w and w not in warehouses:
            warehouses.insert(0, w)
    return {"categories": cats, "locations": locs, "suppliers": suppliers, "warehouses": warehouses}


def export_csv_zip() -> bytes:
    mem = io.BytesIO()
    with get_conn() as conn, zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as zf:
        for table, filename in [("items", "items_자재목록.csv"), ("movements", "movements_입출고기록.csv"), ("notifications", "notifications_알림기록.csv")]:
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            out = io.StringIO()
            if rows:
                writer = csv.DictWriter(out, fieldnames=rows[0].keys())
                writer.writeheader()
                for row in rows:
                    writer.writerow(dict_row(row))
            else:
                out.write("")
            zf.writestr(filename, "\ufeff" + out.getvalue())
        if DB_PATH.exists():
            zf.write(DB_PATH, "warehouse_inventory.sqlite3")
        if PHOTO_DIR.exists():
            for photo in PHOTO_DIR.glob("*"):
                if photo.is_file():
                    zf.write(photo, f"photos/{photo.name}")
        zf.writestr("읽어주세요.txt", "이 파일은 포레스쿨 무역창고 QR 재고관리 백업입니다. CSV는 엑셀에서 바로 열 수 있습니다. sqlite3 파일은 프로그램 원본 DB입니다. photos 폴더에는 자재 대표 사진이 들어 있습니다.\n")
    return mem.getvalue()




def photo_url(filename: str) -> str:
    filename = (filename or "").strip()
    if not filename:
        return ""
    return "/photo/" + urllib.parse.quote(Path(filename).name)


def item_dict(row: sqlite3.Row) -> Dict[str, Any]:
    data = dict_row(row)
    data["photo_url"] = photo_url(str(data.get("photo_filename") or ""))
    return data




def parse_multipart_file(content_type: str, body: bytes, field_name: str = "photo") -> Tuple[bytes, str]:
    match = re.search(r'boundary=(?:"([^"]+)"|([^;]+))', content_type or "")
    if not match:
        raise ValueError("multipart boundary를 찾을 수 없습니다.")
    boundary = ("--" + (match.group(1) or match.group(2)).strip()).encode("utf-8")
    for part in body.split(boundary):
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        if part.endswith(b"--"):
            part = part[:-2].rstrip(b"\r\n")
        if b"\r\n\r\n" not in part:
            continue
        header_blob, file_body = part.split(b"\r\n\r\n", 1)
        headers_text = header_blob.decode("latin-1", errors="ignore")
        if f'name="{field_name}"' not in headers_text:
            continue
        filename_match = re.search(r'filename="([^"]*)"', headers_text)
        filename = filename_match.group(1) if filename_match else "upload"
        return file_body.rstrip(b"\r\n"), filename
    raise ValueError("photo 파일을 찾을 수 없습니다.")

def save_item_photo(item_id: int, raw: bytes, original_name: str = "") -> Dict[str, Any]:
    if not raw:
        raise ValueError("사진 파일이 비어 있습니다.")
    max_size = int(os.environ.get("MAX_PHOTO_UPLOAD_MB", "8")) * 1024 * 1024
    if len(raw) > max_size:
        raise ValueError("사진 파일이 너무 큽니다. 8MB 이하 사진을 올려주세요.")
    try:
        from PIL import Image, ImageOps
    except Exception as exc:
        raise ValueError("Pillow 패키지가 필요합니다. requirements.txt 설치를 확인해주세요.") from exc
    with get_conn() as conn:
        row = fetch_item(conn, item_id)
        if not row:
            raise ValueError("사진을 등록할 자재를 찾을 수 없습니다.")
        old_photo = str(row["photo_filename"] or "") if "photo_filename" in row.keys() else ""
        try:
            img = Image.open(io.BytesIO(raw))
            img = ImageOps.exif_transpose(img)
            img.thumbnail((1200, 1200))
            if img.mode in ("RGBA", "LA"):
                bg = Image.new("RGB", img.size, (255, 255, 255))
                bg.paste(img, mask=img.split()[-1])
                img = bg
            elif img.mode != "RGB":
                img = img.convert("RGB")
            filename = f"item_{item_id}_{datetime.now(KST).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.jpg"
            out = PHOTO_DIR / filename
            img.save(out, format="JPEG", quality=85, optimize=True)
        except Exception as exc:
            raise ValueError("사진 파일을 읽지 못했습니다. JPG/PNG/WEBP 사진으로 다시 시도해주세요.") from exc
        conn.execute("UPDATE items SET photo_filename=?, updated_at=? WHERE id=?", (filename, now_text(), item_id))
        if old_photo:
            try:
                old_path = PHOTO_DIR / Path(old_photo).name
                if old_path.exists() and old_path.name != filename:
                    old_path.unlink()
            except Exception:
                pass
        updated = fetch_item(conn, item_id)
        return item_dict(updated)

def delete_item_permanently(item_id: int) -> Dict[str, Any]:
    """자재를 완전히 삭제합니다. 삭제 전 DB 백업을 만들고, 연결된 입출고 기록과 사진도 함께 정리합니다."""
    if not item_id:
        raise ValueError("삭제할 자재를 선택하세요.")
    with get_conn() as conn:
        row = fetch_item(conn, item_id)
        if not row:
            raise ValueError("삭제할 자재를 찾을 수 없습니다.")
        item = item_dict(row)
        photo_name = str(row["photo_filename"] or "")

    try:
        backup_db("before_delete")
    except Exception:
        print("삭제 전 백업 생성 실패", file=sys.stderr)
        traceback.print_exc()

    with get_conn() as conn:
        conn.execute("DELETE FROM movements WHERE item_id=?", (item_id,))
        conn.execute("DELETE FROM items WHERE id=?", (item_id,))

    if photo_name:
        try:
            target = (PHOTO_DIR / Path(photo_name).name).resolve()
            if target.exists() and target.is_file() and (PHOTO_DIR.resolve() in target.parents):
                target.unlink()
        except Exception:
            print("사진 파일 삭제 실패", photo_name, file=sys.stderr)
            traceback.print_exc()
    return item



def make_low_stock_message(item: sqlite3.Row, before: float, after: float, action_label: str) -> str:
    base = PUBLIC_BASE_URL or public_base_for_console()
    link = f"{base}/scan/{urllib.parse.quote(str(item['qr_code']))}" if base and not base.startswith("Railway >") else str(item['qr_code'])
    return (
        f"[포레스쿨 재고알림]\n"
        f"한국포레스쿨창고 재고가 {LOW_STOCK_THRESHOLD:g}개 미만입니다.\n"
        f"자재: {item['name']}\n"
        f"현재: {after:g}{item['unit']} / 이전: {before:g}{item['unit']}\n"
        f"위치: {item['location'] or '미정'}\n"
        f"처리: {action_label}\n"
        f"QR조회: {link}"
    )


def post_webhook_message(message: str, item: sqlite3.Row) -> Tuple[bool, str]:
    if not ALERT_WEBHOOK_URL:
        return False, "ALERT_WEBHOOK_URL 또는 KAKAO_WORK_WEBHOOK_URL이 설정되지 않았습니다."
    payload = {
        "text": message,
        "message": message,
        "blocks": [{"type": "text", "text": message}],
        "item": {
            "qr_code": item["qr_code"],
            "name": item["name"],
            "warehouse": item["warehouse"],
            "stock_qty": item["stock_qty"],
            "unit": item["unit"],
            "location": item["location"],
        },
    }
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(ALERT_WEBHOOK_URL, data=raw, headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=6) as resp:
            code = getattr(resp, "status", resp.getcode())
            body = resp.read(500).decode("utf-8", errors="ignore")
            if 200 <= int(code) < 300:
                return True, body or f"HTTP {code}"
            return False, f"HTTP {code} {body}"
    except Exception as exc:
        return False, str(exc)


def maybe_send_low_stock_alert(conn: sqlite3.Connection, item: sqlite3.Row, before: float, after: float, action_label: str) -> Optional[Dict[str, Any]]:
    if not item:
        return None
    warehouse = str(item["warehouse"] or DEFAULT_WAREHOUSE)
    if warehouse != KOREA_WAREHOUSE:
        return None
    if int(item["low_alert_enabled"] or 0) != 1:
        return None
    if after >= LOW_STOCK_THRESHOLD:
        return None
    # 기준선 위에서 아래로 내려온 경우는 바로 알림. 이미 100 미만인 상태에서는 쿨다운으로 반복 알림을 줄입니다.
    should_alert = before >= LOW_STOCK_THRESHOLD
    last = str(item["last_low_alert_at"] or "")
    if not should_alert and last:
        try:
            last_dt = datetime.strptime(last, KST_FORMAT).replace(tzinfo=KST)
            should_alert = (datetime.now(KST) - last_dt).total_seconds() >= ALERT_COOLDOWN_SECONDS
        except Exception:
            should_alert = True
    if not should_alert:
        return None
    message = make_low_stock_message(item, before, after, action_label)
    success, error = post_webhook_message(message, item)
    t = now_text()
    conn.execute("UPDATE items SET last_low_alert_at=? WHERE id=?", (t, item["id"]))
    conn.execute(
        """
        INSERT INTO notifications
        (item_id, qr_code, item_name, warehouse, stock_qty, threshold_qty, channel, message, success, error, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (item["id"], item["qr_code"], item["name"], warehouse, after, LOW_STOCK_THRESHOLD, "webhook", message, 1 if success else 0, error, t),
    )
    return {"sent": success, "message": message, "error": error, "created_at": t}


def recent_notifications(limit: int = 50) -> List[Dict[str, Any]]:
    limit = min(max(int(limit or 50), 1), 200)
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM notifications ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict_row(r) for r in rows]

def render_qr_png(value: str) -> bytes:
    img = qrcode.make(value)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()




def public_base_from_headers(headers: Any) -> str:
    """Railway 공개 주소 또는 현재 요청 헤더를 기준으로 QR 조회용 기본 주소를 계산합니다."""
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL
    host = (headers.get("X-Forwarded-Host") or headers.get("Host") or f"localhost:{PORT}").strip()
    proto = (headers.get("X-Forwarded-Proto") or ("https" if RUNNING_ON_RAILWAY else "http")).split(",")[0].strip()
    host_only = host.split(":", 1)[0].lower()
    if not RUNNING_ON_RAILWAY and host_only in {"localhost", "127.0.0.1", "0.0.0.0"}:
        for ip in get_local_ips():
            if not ip.startswith("127."):
                return f"http://{ip}:{PORT}"
    return f"{proto}://{host}"


def public_base_for_console() -> str:
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL
    if RUNNING_ON_RAILWAY:
        return "Railway > Settings > Networking > Public Networking에서 생성한 주소"
    return f"http://localhost:{PORT}"

def serve_index(initial_qr: str = "") -> bytes:
    """관리자 첫 화면. static 파일이 없거나 깨져도 Railway에서 500이 나지 않도록 내장 HTML로 자동 대체합니다."""
    html = EMBEDDED_INDEX_HTML
    # GitHub 웹 업로드에서 static 폴더가 풀려서 index.html/app.css/app.js가 루트에 올라가는 경우까지 지원합니다.
    html_paths = [STATIC_DIR / "index.html", BASE_DIR / "index.html"]
    try:
        for html_path in html_paths:
            if html_path.exists() and html_path.is_file():
                candidate = html_path.read_text(encoding="utf-8")
                if candidate.strip().lower().startswith("<!doctype"):
                    html = candidate
                    break
    except Exception:
        print("static/index.html 로드 실패, 내장 화면으로 대체합니다.", file=sys.stderr)
        traceback.print_exc()
        html = EMBEDDED_INDEX_HTML
    try:
        html = html.replace("__INITIAL_QR__", json.dumps(clean_qr(initial_qr), ensure_ascii=False))
        return html.encode("utf-8")
    except Exception:
        traceback.print_exc()
        minimal = """<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>무역창고 QR 재고관리</title></head><body style=\"font-family:system-ui,sans-serif;padding:24px\"><h1>무역창고 QR 재고관리</h1><p>첫 화면을 단순 모드로 열었습니다. Railway Variables와 업로드 파일을 확인해주세요.</p><p><a href=\"/api/me\">서버 상태 확인</a></p></body></html>"""
        return minimal.encode("utf-8")


def label_page(query: Dict[str, str], headers: Any) -> bytes:
    ids = query.get("ids", "all")
    base = query.get("base", "").strip().rstrip("/")
    if not base:
        base = public_base_from_headers(headers)
    with get_conn() as conn:
        if ids and ids != "all":
            id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()]
            if id_list:
                placeholders = ",".join("?" for _ in id_list)
                rows = conn.execute(f"SELECT * FROM items WHERE id IN ({placeholders}) ORDER BY category, location, name", id_list).fetchall()
            else:
                rows = []
        else:
            rows = conn.execute("SELECT * FROM items WHERE status != '숨김' ORDER BY category, location, name").fetchall()
    cards = []
    for r in rows:
        d = dict_row(r)
        qr_src = f"/api/qr/{urllib.parse.quote(d['qr_code'])}.png?mode=url&base={urllib.parse.quote(base, safe=':/')}"
        cards.append(f"""
        <div class="label">
          <div class="qr"><img src="{qr_src}" alt="QR"></div>
          <div class="txt">
            <b>{html_escape(d['name'])}</b>
            <span>{html_escape(d['warehouse'] or DEFAULT_WAREHOUSE)}</span>
            <span>{html_escape(d['spec'] or '-')}</span>
            <span>위치: {html_escape(d['location'] or '미정')}</span>
            <span>현재: {d['stock_qty']:g}{html_escape(d['unit'])}</span>
            <small>{html_escape(d['qr_code'])}</small>
          </div>
        </div>
        """)
    html = f"""
<!doctype html><html lang="ko"><head><meta charset="utf-8">
<title>QR 라벨 출력</title>
<style>
@page {{ size: A4; margin: 10mm; }}
*{{box-sizing:border-box}} body{{font-family:'Malgun Gothic','Apple SD Gothic Neo',Arial,sans-serif;margin:0;color:#111;background:#f5f3ed}}
.toolbar{{position:sticky;top:0;background:#fff;padding:14px 18px;border-bottom:1px solid #ddd;display:flex;gap:10px;align-items:center;z-index:5}}
button,a{{border:0;background:#2d5a45;color:white;padding:10px 14px;border-radius:10px;text-decoration:none;font-weight:700;cursor:pointer}}
input{{padding:10px;border:1px solid #bbb;border-radius:10px;min-width:320px}}
.help{{font-size:13px;color:#555}}
.sheet{{display:grid;grid-template-columns:repeat(2, 1fr);gap:8px;padding:10mm}}
.label{{height:36mm;background:#fff;border:1px dashed #aaa;border-radius:8px;padding:4mm;display:flex;gap:4mm;align-items:center;break-inside:avoid}}
.qr img{{width:28mm;height:28mm;image-rendering:pixelated}}
.txt{{display:flex;flex-direction:column;gap:1.5mm;min-width:0}}
.txt b{{font-size:12.5pt;line-height:1.15;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.txt span{{font-size:9pt;color:#333;line-height:1.1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.txt small{{font-size:8pt;color:#666;letter-spacing:.4px}}
@media print {{ body{{background:#fff}} .toolbar{{display:none}} .sheet{{padding:0;gap:5mm}} .label{{border:1px solid #ddd;border-radius:4px}} }}
</style></head><body>
<div class="toolbar">
  <button onclick="window.print()">라벨 인쇄</button>
  <a href="/">재고판으로 돌아가기</a>
  <span class="help">이 라벨의 QR은 휴대폰 카메라로 찍으면 바로 자재 조회 화면이 열립니다. 현재 QR 기준 주소: {html_escape(base)}</span>
</div>
<div class="sheet">{''.join(cards) if cards else '<p>출력할 자재가 없습니다.</p>'}</div>
</body></html>
    """
    return html.encode("utf-8")


def public_scan_page(code: str, headers: Any) -> bytes:
    qr = clean_qr(code)
    with get_conn() as conn:
        row = item_by_qr(conn, qr)
    base = public_base_from_headers(headers)
    if not row:
        body = f"""
        <section class="card">
          <p class="eyebrow">QR LOOKUP</p>
          <h1>자재를 찾지 못했습니다</h1>
          <p>QR 코드 <code>{html_escape(qr)}</code>에 연결된 자재가 없습니다.</p>
          <a class="btn" href="/">관리자 화면</a>
        </section>
        """
    else:
        d = item_dict(row)
        photo_html = f'<div class="photo"><img src="{html_escape(d.get("photo_url") or "")}" alt="자재 사진"></div>' if d.get("photo_url") else '<div class="photo empty">등록된 사진 없음</div>'
        body = f"""
        <section class="card">
          <p class="eyebrow">FORESCHOOL WAREHOUSE</p>
          <h1>{html_escape(d['name'])}</h1>
          {photo_html}
          <div class="stock">{d['stock_qty']:g}<span>{html_escape(d['unit'])}</span></div>
          <dl>
            <dt>창고</dt><dd>{html_escape(d['warehouse'] or DEFAULT_WAREHOUSE)}</dd>
            <dt>보관 위치</dt><dd>{html_escape(d['location'] or '미정')}</dd>
            <dt>분류</dt><dd>{html_escape(d['category'] or '미분류')}</dd>
            <dt>규격</dt><dd>{html_escape(d['spec'] or '-')}</dd>
            <dt>거래처/공장</dt><dd>{html_escape(d['supplier'] or '-')}</dd>
            <dt>QR 코드</dt><dd><code>{html_escape(d['qr_code'])}</code></dd>
            <dt>최근 수정</dt><dd>{html_escape(d['updated_at'])}</dd>
          </dl>
          <div class="memo">{html_escape(d['memo'] or '메모 없음')}</div>
          <div class="actions"><a class="btn" href="/">관리자 화면에서 수정</a></div>
        </section>
        """
    html = f"""<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>QR 자재 조회</title>
<style>
:root{{--bg:#f4f1ea;--card:#fffdf8;--ink:#1d211c;--muted:#6c716a;--line:#e2ded2;--brand:#2d5a45;--soft:#edf4ec;}}
*{{box-sizing:border-box}} body{{margin:0;min-height:100vh;background:var(--bg);font-family:'Malgun Gothic','Apple SD Gothic Neo',system-ui,sans-serif;color:var(--ink);padding:18px;display:flex;align-items:center;justify-content:center}}
.card{{width:min(560px,100%);background:var(--card);border:1px solid var(--line);border-radius:24px;padding:24px;box-shadow:0 14px 34px rgba(47,44,34,.1)}}
.eyebrow{{margin:0 0 6px;color:var(--brand);font-weight:900;font-size:12px;letter-spacing:.12em}}
h1{{font-size:26px;margin:0 0 18px;letter-spacing:-.04em;line-height:1.25}}
.photo{{width:100%;border-radius:18px;overflow:hidden;background:#f0eee8;border:1px solid var(--line);margin-bottom:16px;text-align:center}}
.photo img{{width:100%;max-height:360px;object-fit:contain;display:block;background:white}}
.photo.empty{{padding:34px;color:var(--muted);font-weight:800}}
.stock{{background:var(--soft);border:1px solid #d5e3d2;color:var(--brand);font-size:48px;font-weight:900;border-radius:18px;padding:18px;margin-bottom:18px;text-align:center}}
.stock span{{font-size:19px;margin-left:6px;color:#355642}}
dl{{display:grid;grid-template-columns:105px 1fr;gap:10px 12px;margin:0}}
dt{{color:var(--muted);font-weight:800}} dd{{margin:0;font-weight:700;word-break:break-word}} code{{background:#f0eee8;border:1px solid var(--line);padding:2px 7px;border-radius:8px}}
.memo{{margin-top:18px;padding:13px;border-radius:14px;background:#f9f7f1;color:#4a5048;line-height:1.55;white-space:pre-wrap}}
.actions{{margin-top:18px;display:flex;gap:8px}} .btn{{background:var(--brand);color:white;text-decoration:none;border-radius:12px;padding:12px 14px;font-weight:900;display:inline-flex;justify-content:center}}
.footer{{margin-top:12px;text-align:center;color:var(--muted);font-size:12px}}
</style></head><body><main>{body}<p class="footer">조회 주소: {html_escape(base)}</p></main></body></html>"""
    return html.encode("utf-8")


def admin_required_page() -> bytes:
    html = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>관리자 로그인 필요</title>
<style>body{font-family:'Malgun Gothic',system-ui,sans-serif;background:#f4f1ea;margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}.card{background:#fffdf8;border:1px solid #e2ded2;border-radius:20px;padding:24px;max-width:480px;box-shadow:0 14px 34px rgba(47,44,34,.1)}a{display:inline-flex;margin-top:14px;background:#2d5a45;color:white;text-decoration:none;border-radius:12px;padding:12px 14px;font-weight:900}</style></head><body><div class="card"><h1>관리자 로그인이 필요합니다</h1><p>전체 재고판, 라벨 출력, 백업 기능은 관리자 PIN 로그인 후 사용할 수 있습니다.</p><a href="/">관리자 화면으로 이동</a></div></body></html>"""
    return html.encode("utf-8")


def html_escape(s: Any) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


class Handler(BaseHTTPRequestHandler):
    server_version = "ForeschoolWarehouseQR/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        # 콘솔 로그를 너무 시끄럽지 않게 유지
        sys.stderr.write("[%s] %s\n" % (now_text(), fmt % args))

    def _send(self, status: int, content: bytes, content_type: str = "text/plain; charset=utf-8", headers: Optional[Dict[str, str]] = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        if headers:
            for k, v in headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(content)

    def _json(self, data: Any, status: int = 200) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False, default=str).encode("utf-8"), "application/json; charset=utf-8")

    def _error(self, message: str, status: int = 400) -> None:
        self._json({"ok": False, "error": message}, status)

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def _cookie_value(self, name: str) -> str:
        raw = self.headers.get("Cookie", "")
        for part in raw.split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                if k == name:
                    return v
        return ""

    def _is_admin(self) -> bool:
        cookie_token = self._cookie_value("fsw_admin")
        header_pin = self.headers.get("X-Admin-Pin", "")
        return hmac.compare_digest(cookie_token, session_token()) or pin_matches(header_pin)

    def _require_admin_json(self) -> bool:
        if self._is_admin():
            return True
        self._json({"ok": False, "error": "관리자 PIN 로그인이 필요합니다.", "auth_required": True}, 401)
        return False

    def _set_admin_cookie(self) -> None:
        token = session_token()
        self.send_header("Set-Cookie", f"fsw_admin={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000")

    def _clear_admin_cookie(self) -> None:
        self.send_header("Set-Cookie", "fsw_admin=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0")

    def _params(self) -> Tuple[str, Dict[str, str]]:
        parsed = urllib.parse.urlparse(self.path)
        params = {k: v[-1] if v else "" for k, v in urllib.parse.parse_qs(parsed.query).items()}
        return parsed.path, params

    def do_GET(self) -> None:  # noqa: N802
        try:
            path, params = self._params()
            if path == "/" or path == "/index.html":
                return self._send(200, serve_index(), "text/html; charset=utf-8")
            if path.startswith("/scan/"):
                code = path.rsplit("/scan/", 1)[-1]
                return self._send(200, public_scan_page(code, self.headers), "text/html; charset=utf-8")
            if path.startswith("/photo/"):
                name = Path(urllib.parse.unquote(path.split("/photo/", 1)[-1])).name
                target = (PHOTO_DIR / name).resolve()
                if PHOTO_DIR.resolve() not in target.parents and target != PHOTO_DIR.resolve():
                    return self._error("잘못된 사진 경로입니다.", 403)
                if not target.exists() or not target.is_file():
                    return self._error("사진을 찾을 수 없습니다.", 404)
                return self._send(200, target.read_bytes(), "image/jpeg")
            if path == "/labels":
                if not self._is_admin():
                    return self._send(401, admin_required_page(), "text/html; charset=utf-8")
                return self._send(200, label_page(params, self.headers), "text/html; charset=utf-8")

            if path == "/api/me":
                return self._json({"ok": True, "data": {"authenticated": self._is_admin(), "default_pin_warning": ADMIN_PIN == "1204", "data_dir": str(DATA_DIR), "public_base_url": PUBLIC_BASE_URL, "korea_warehouse": KOREA_WAREHOUSE, "low_stock_threshold": LOW_STOCK_THRESHOLD, "alert_webhook_configured": bool(ALERT_WEBHOOK_URL)}})
            if path == "/api/dashboard":
                if not self._require_admin_json(): return
                return self._json({"ok": True, "data": dashboard(params)})
            if path == "/api/items":
                if not self._require_admin_json(): return
                return self._json({"ok": True, "data": list_items(params)})
            if path.startswith("/api/items/"):
                if not self._require_admin_json(): return
                item_id = int(path.split("/")[-1])
                with get_conn() as conn:
                    row = fetch_item(conn, item_id)
                    if not row:
                        return self._error("자재를 찾을 수 없습니다.", 404)
                    return self._json({"ok": True, "data": item_dict(row)})
            if path == "/api/lookup":
                qr = clean_qr(params.get("qr", ""))
                with get_conn() as conn:
                    row = item_by_qr(conn, qr)
                    if not row:
                        return self._error("QR에 연결된 자재를 찾을 수 없습니다.", 404)
                    return self._json({"ok": True, "data": item_dict(row)})
            if path == "/api/movements":
                if not self._require_admin_json(): return
                return self._json({"ok": True, "data": recent_movements(params)})
            if path == "/api/meta":
                if not self._require_admin_json(): return
                return self._json({"ok": True, "data": categories_and_locations()})
            if path == "/api/notifications":
                if not self._require_admin_json(): return
                return self._json({"ok": True, "data": recent_notifications(int(params.get("limit", "50") or 50))})
            if path == "/api/config":
                ips = get_local_ips()
                base = public_base_from_headers(self.headers)
                urls = [base]
                if not RUNNING_ON_RAILWAY:
                    urls += [f"http://{ip}:{PORT}" for ip in ips if not ip.startswith("127.")]
                return self._json({"ok": True, "data": {"app": APP_NAME, "port": PORT, "ips": ips, "urls": list(dict.fromkeys(urls)), "public_base": base, "cloud": RUNNING_ON_RAILWAY, "korea_warehouse": KOREA_WAREHOUSE, "low_stock_threshold": LOW_STOCK_THRESHOLD, "alert_webhook_configured": bool(ALERT_WEBHOOK_URL)}})
            if path.startswith("/api/qr/") and path.endswith(".png"):
                code = urllib.parse.unquote(path.split("/api/qr/", 1)[-1][:-4])
                code = clean_qr(code)
                mode = params.get("mode", "code")
                base = params.get("base", "").rstrip("/")
                value = code
                if mode == "url" and base:
                    value = f"{base}/scan/{urllib.parse.quote(code)}"
                return self._send(200, render_qr_png(value), "image/png")
            if path == "/api/export.zip":
                if not self._is_admin():
                    return self._send(401, b"admin login required", "text/plain; charset=utf-8")
                data = export_csv_zip()
                filename = f"foreschool_warehouse_backup_{datetime.now(KST).strftime('%Y%m%d_%H%M%S')}.zip"
                return self._send(200, data, "application/zip", {"Content-Disposition": f"attachment; filename={filename}"})
            if path == "/api/backup":
                if not self._require_admin_json(): return
                out = backup_db("manual")
                return self._json({"ok": True, "data": {"path": str(out)}})

            # static files. /static/app.css가 없으면 루트 app.css/app.js도 대체로 찾습니다.
            if path.startswith("/static/"):
                rel = path.split("/static/", 1)[-1]
                target = (STATIC_DIR / rel).resolve()
                if not target.exists() and rel in {"app.css", "app.js"}:
                    root_target = (BASE_DIR / rel).resolve()
                    if root_target.exists() and root_target.is_file():
                        target = root_target
                allowed_static = STATIC_DIR.resolve() in target.parents or target == STATIC_DIR.resolve()
                allowed_root_asset = target.parent == BASE_DIR.resolve() and target.name in {"app.css", "app.js"}
                if not allowed_static and not allowed_root_asset:
                    return self._error("잘못된 경로입니다.", 403)
                if not target.exists() or not target.is_file():
                    return self._error("파일을 찾을 수 없습니다.", 404)
                content_type = "text/plain; charset=utf-8"
                if target.suffix == ".css":
                    content_type = "text/css; charset=utf-8"
                elif target.suffix == ".js":
                    content_type = "application/javascript; charset=utf-8"
                elif target.suffix == ".png":
                    content_type = "image/png"
                return self._send(200, target.read_bytes(), content_type)

            self._error("페이지를 찾을 수 없습니다.", 404)
        except Exception as exc:
            print("GET 처리 중 오류:", self.path, file=sys.stderr)
            traceback.print_exc()
            self._error(str(exc), 500)

    def do_POST(self) -> None:  # noqa: N802
        try:
            path, _ = self._params()
            data = {} if "multipart/form-data" in (self.headers.get("Content-Type") or "") else self._read_json()
            if path == "/api/login":
                if not pin_matches(data.get("pin")):
                    return self._error("관리자 PIN이 맞지 않습니다.", 401)
                body = json.dumps({"ok": True, "data": {"authenticated": True}}, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self._set_admin_cookie()
                self.end_headers()
                self.wfile.write(body)
                return
            if path == "/api/logout":
                body = json.dumps({"ok": True, "data": {"authenticated": False}}, ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self._clear_admin_cookie()
                self.end_headers()
                self.wfile.write(body)
                return
            if not self._require_admin_json():
                return
            if path.startswith("/api/items/") and path.endswith("/photo"):
                item_id = int(path.split("/")[-2])
                ctype = self.headers.get("Content-Type", "")
                if "multipart/form-data" not in ctype:
                    return self._error("사진 업로드 형식이 아닙니다.", 400)
                length = int(self.headers.get("Content-Length", "0") or 0)
                raw_body = self.rfile.read(length)
                raw, filename = parse_multipart_file(ctype, raw_body, "photo")
                item = save_item_photo(item_id, raw, filename)
                return self._json({"ok": True, "data": item})
            if path == "/api/items":
                item = create_or_update_item(data)
                return self._json({"ok": True, "data": item})
            if path == "/api/movement":
                result = apply_movement(data)
                return self._json({"ok": True, "data": result})
            if path == "/api/test-alert":
                dummy = {"name": "알림 테스트", "qr_code": "FSW-TEST", "warehouse": KOREA_WAREHOUSE, "stock_qty": 99, "unit": "개", "location": "테스트", "low_alert_enabled": 1, "id": 0}
                # sqlite Row 대신 dict를 비슷하게 사용할 수 있도록 간단 객체 처리
                class D(dict):
                    def __getitem__(self, k): return dict.get(self, k, "")
                item = D(dummy)
                msg = f"[포레스쿨 재고알림 테스트] 한국포레스쿨창고 재고 알림 연결 테스트입니다. 현재 99개 미만 알림 설정이 정상입니다."
                ok, err = post_webhook_message(msg, item)
                return self._json({"ok": True, "data": {"sent": ok, "error": err, "configured": bool(ALERT_WEBHOOK_URL)}})
            if path.startswith("/api/items/") and path.endswith("/hide"):
                item_id = int(path.split("/")[-2])
                with get_conn() as conn:
                    row = fetch_item(conn, item_id)
                    if not row:
                        return self._error("자재를 찾을 수 없습니다.", 404)
                    conn.execute("UPDATE items SET status='숨김', updated_at=? WHERE id=?", (now_text(), item_id))
                return self._json({"ok": True})
            if path.startswith("/api/items/") and path.endswith("/delete"):
                item_id = int(path.split("/")[-2])
                deleted = delete_item_permanently(item_id)
                return self._json({"ok": True, "data": deleted})
            self._error("지원하지 않는 요청입니다.", 404)
        except sqlite3.IntegrityError as exc:
            self._error("QR 코드가 중복되었거나 저장할 수 없습니다. " + str(exc), 400)
        except Exception as exc:
            print("POST 처리 중 오류:", self.path, file=sys.stderr)
            traceback.print_exc()
            self._error(str(exc), 400)


def main() -> None:
    init_db()
    startup_backup()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    urls = [public_base_for_console()]
    if not RUNNING_ON_RAILWAY:
        urls = [f"http://localhost:{PORT}"]
        # 서버 시작 전에는 네트워크 IP 탐색이 환경에 따라 느릴 수 있어 콘솔에는 localhost만 표시합니다.
        # 실제 휴대폰 접속 주소는 관리자 화면의 /api/config에서 표시됩니다.
    print("=" * 70)
    print(f"{APP_NAME} 실행 중")
    print(f"데이터 저장 위치: {DATA_DIR}")
    print("접속 주소:")
    for u in urls:
        print(" -", u)
    if ADMIN_PIN == "1204":
        print("주의: 기본 관리자 PIN은 1204입니다. Railway Variables에서 ADMIN_PIN을 꼭 변경하세요.")
    print("종료: 이 창에서 Ctrl + C")
    print("=" * 70)
    if AUTO_OPEN_BROWSER:
        threading.Timer(0.8, lambda: webbrowser.open(f"http://localhost:{PORT}")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
