import time
import datetime
import threading
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import sys
import os
import json
import tempfile
import subprocess
import re
import urllib.request
from html.parser import HTMLParser
from selenium import webdriver
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.webdriver import WebDriver as ChromeDriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.common.exceptions import TimeoutException, UnexpectedAlertPresentException, InvalidSessionIdException, WebDriverException
from webdriver_manager.chrome import ChromeDriverManager

# ─────────────────────────────────────────
VERSION = "1.1.0"
GITHUB_REPO = "seunghyehh/BangJJokMacro"
# ─────────────────────────────────────────

BASE_URL = "https://play33.kr/reservation"


# ── 지점/테마 파싱 ─────────────────────────

class _SelectParser(HTMLParser):
    def __init__(self, select_name):
        super().__init__()
        self.select_name = select_name
        self.in_select = False
        self.cur_value = None
        self.cur_text = []
        self.options = []  # [(value, text)]

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "select" and attrs.get("name") == self.select_name:
            self.in_select = True
        elif tag == "option" and self.in_select:
            self.cur_value = attrs.get("value", "")
            self.cur_text = []

    def handle_endtag(self, tag):
        if tag == "select":
            self.in_select = False
        elif tag == "option" and self.in_select and self.cur_value:
            text = "".join(self.cur_text).strip()
            if text:
                self.options.append((self.cur_value, text))
            self.cur_value = None

    def handle_data(self, data):
        if self.in_select and self.cur_value is not None:
            self.cur_text.append(data)


def _fetch_options(url, select_name):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=8) as resp:
        html = resp.read().decode("utf-8", errors="ignore")
    parser = _SelectParser(select_name)
    parser.feed(html)
    return parser.options  # [(value, text), ...]


def fetch_branches():
    return _fetch_options(BASE_URL, "branch")


def fetch_themes(branch_val):
    return _fetch_options(f"{BASE_URL}?branch={branch_val}", "theme")


def fetch_time_slots(branch_val, theme_val, date_val):
    """예약 페이지에서 시간 슬롯 파싱. 반환: ['HH:MM', ...]"""
    url = f"{BASE_URL}?branch={branch_val}&theme={theme_val}&date={date_val}"

    # 1차: urllib (빠름, JS 렌더링 안 됨)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        # <button ...>예약 가능/불가 <span>HH:MM</span></button>
        times = re.findall(r'(?:예약\s*가능|예약\s*불가)\s*<span>(\d{1,2}:\d{2})</span>', html)
        if times:
            seen = set()
            return [t for t in times if not (seen.add(t) or t in seen - {t})]
    except Exception:
        pass

    # 2차: Selenium (JS 렌더링된 경우)
    driver = None
    try:
        driver = build_driver()
        driver.get(url)
        try:
            WebDriverWait(driver, 1).until(EC.alert_is_present())
            driver.switch_to.alert.accept()
        except Exception:
            pass
        WebDriverWait(driver, 8, poll_frequency=0.2).until(
            EC.presence_of_element_located((By.XPATH, "//*[contains(text(),'예약 가능') or contains(text(),'예약 불가')]"))
        )
        slots = driver.find_elements(By.XPATH, "//*[contains(text(),'예약 가능') or contains(text(),'예약 불가')]")
        seen = set()
        result = []
        for s in slots:
            m = re.search(r'\b(\d{1,2}:\d{2})\b', s.text)
            if m:
                t = m.group(1)
                if t not in seen:
                    seen.add(t)
                    result.append(t)
        return result
    finally:
        if driver:
            driver.quit()


# ── 자동 업데이트 ──────────────────────────

def _parse_ver(v: str):
    return tuple(int(x) for x in v.lstrip("v").split("."))


def check_update():
    """최신 릴리즈 확인. 반환: (latest_tag, download_url) or (None, None)"""
    try:
        api = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
        req = urllib.request.Request(api, headers={"User-Agent": "Play33Macro"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        latest_tag = data["tag_name"]
        if _parse_ver(latest_tag) <= _parse_ver(VERSION):
            return None, None
        for asset in data.get("assets", []):
            if asset["name"].endswith(".exe"):
                return latest_tag, asset["browser_download_url"]
        return latest_tag, None
    except Exception:
        return None, None


def do_update(download_url, log=None):
    """새 exe 다운로드 후 교체 스크립트 실행"""
    try:
        if not getattr(sys, "frozen", False):
            if log:
                log("개발 환경에서는 업데이트를 건너뜁니다.")
            return False
        current_exe = sys.executable
        tmp_path = current_exe + ".new"
        if log:
            log("새 버전 다운로드 중...")
        urllib.request.urlretrieve(download_url, tmp_path)
        bat = os.path.join(tempfile.gettempdir(), "play33_update.bat")
        with open(bat, "w") as f:
            f.write(
                f"@echo off\n"
                f"timeout /t 2 /nobreak > nul\n"
                f"move /y \"{tmp_path}\" \"{current_exe}\"\n"
                f"start \"\" \"{current_exe}\"\n"
                f"del \"%~f0\"\n"
            )
        subprocess.Popen(["cmd", "/c", bat], creationflags=subprocess.CREATE_NO_WINDOW)
        return True
    except Exception as e:
        if log:
            log(f"업데이트 실패: {e}")
        return False


# ── 매크로 로직 ────────────────────────────

def format_phone(phone: str) -> str:
    phone = phone.replace("-", "")
    if len(phone) == 11:
        return f"{phone[:3]}-{phone[3:7]}-{phone[7:]}"
    elif len(phone) == 10:
        return f"{phone[:3]}-{phone[3:6]}-{phone[6:]}"
    return phone


def build_driver() -> ChromeDriver:
    options = ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1280,900")
    options.page_load_strategy = "eager"
    options.add_experimental_option("prefs", {
        "profile.managed_default_content_settings.images": 2
    })
    service = ChromeService(ChromeDriverManager().install())
    return ChromeDriver(service=service, options=options)


def build_url(config: dict) -> str:
    return f"{BASE_URL}?branch={config['branch']}&theme={config['theme']}&date={config['date']}#content"


def wait_until_open(open_time_str, driver=None, url=None, log=print, stop_event=None):
    if not open_time_str:
        return
    target = datetime.datetime.combine(datetime.date.today(), datetime.time.fromisoformat(open_time_str))
    now = datetime.datetime.now()
    if now >= target:
        log(f"이미 오픈 시각({open_time_str})이 지났습니다. 즉시 시작합니다.")
        return
    wait_secs = (target - now).total_seconds()
    log(f"오픈까지 {wait_secs:.0f}초 대기 중... (현재: {now.strftime('%H:%M:%S')})")

    sleep_time = wait_secs - 3 - 1
    if sleep_time > 0:
        elapsed = 0
        while elapsed < sleep_time:
            if stop_event and stop_event.is_set():
                return
            chunk = min(0.5, sleep_time - elapsed)
            time.sleep(chunk)
            elapsed += chunk

    if driver and url and datetime.datetime.now() < target:
        log("[캐시 워밍] 페이지 미리 로드 중...")
        try:
            driver.get(url)
            try:
                WebDriverWait(driver, 1).until(EC.alert_is_present())
                driver.switch_to.alert.accept()
            except Exception:
                pass
        except Exception:
            pass

    while datetime.datetime.now() < target:
        if stop_event and stop_event.is_set():
            return
    log(f">>> 오픈! 시작 시각: {datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]}")


def find_available_time(driver, preferred_times):
    WebDriverWait(driver, 10, poll_frequency=0.1).until(
        EC.presence_of_element_located((By.XPATH, "//*[contains(text(),'예약 가능') or contains(text(),'예약 불가')]"))
    )
    available = {}
    for slot in driver.find_elements(By.XPATH, "//*[contains(text(),'예약 가능')]"):
        text = slot.text.strip()
        for pt in preferred_times:
            if pt in text:
                available[pt] = slot
    for pt in preferred_times:
        if pt in available:
            return pt, available[pt]
    return None, None


def fill_reservation_form(driver, config, log=print):
    log("[2] 예약 정보 입력")

    def fill_js(name_attr, value):
        elems = driver.find_elements(By.NAME, name_attr)
        if elems:
            driver.execute_script("arguments[0].value = arguments[1];", elems[0], value)
        else:
            log(f"  [!] '{name_attr}' 필드를 찾지 못했습니다.")

    fill_js("name", config["name"])
    fill_js("phone", format_phone(config["phone"]))

    people = driver.find_elements(By.NAME, "people")
    if people:
        driver.execute_script("arguments[0].value = arguments[1];", people[0], config["headcount"])
    else:
        log("  [!] 인원 선택 필드를 찾지 못했습니다.")

    policy = driver.find_elements(By.NAME, "policy")
    if policy:
        if not policy[0].is_selected():
            driver.execute_script("arguments[0].click();", policy[0])
    else:
        log("  [!] 개인정보 동의 체크박스를 찾지 못했습니다.")

    if config.get("test_mode"):
        log("[테스트] 폼 입력 완료. 제출은 건너뜁니다. 브라우저에서 확인하세요.")
        return

    log("[3] 예약 제출")
    btns = driver.find_elements(
        By.XPATH,
        "//button[contains(text(),'예약') or contains(text(),'확인') or contains(text(),'완료')] | //input[@type='submit']"
    )
    if btns:
        btns[0].click()
        log("  [완료] 예약 제출 완료! 브라우저에서 확인하세요.")
    else:
        log("  [!] 제출 버튼을 찾지 못했습니다. 수동으로 확인해주세요.")


def run_macro(config, log=print, stop_event=None, label=""):
    def _log(msg):
        if label:
            log(f"[{label}] {msg}")
        else:
            log(msg)

    t_start = time.perf_counter()
    driver = None
    try:
        _log("브라우저 시작 중...")
        driver = build_driver()
        _log(f"브라우저 초기화: {time.perf_counter() - t_start:.2f}s")
    except Exception as e:
        _log(f"브라우저 시작 실패: {e}")
        return

    url = build_url(config)
    retries = 0

    try:
        _log("=" * 40)
        _log(f"branch={config['branch']} | theme={config['theme']}")
        _log(f"날짜: {config['date']} | 희망시간: {config['preferred_times']}")
        _log("=" * 40)

        wait_until_open(config.get("open_time"), driver, url, log=_log, stop_event=stop_event)

        while True:
            if stop_event and stop_event.is_set():
                _log("중지되었습니다.")
                break

            t_load = time.perf_counter()
            driver.get(url)
            try:
                WebDriverWait(driver, 1).until(EC.alert_is_present())
                driver.switch_to.alert.accept()
            except Exception:
                pass
            _log(f"페이지 로드: {time.perf_counter() - t_load:.2f}s")

            try:
                t_find = time.perf_counter()
                time_str, slot_elem = find_available_time(driver, config["preferred_times"])
                _log(f"슬롯 탐색: {time.perf_counter() - t_find:.2f}s")
            except (UnexpectedAlertPresentException, TimeoutException):
                try:
                    driver.switch_to.alert.accept()
                except Exception:
                    pass
                time_str, slot_elem = None, None
                _log("슬롯 탐색: 슬롯 없음")
            except InvalidSessionIdException:
                _log("브라우저가 닫혔습니다.")
                return

            if time_str:
                _log(f"[1] '{time_str}' 예약 가능! 클릭합니다.")
                slot_elem.click()
                WebDriverWait(driver, 5, poll_frequency=0.1).until(
                    EC.presence_of_element_located((By.NAME, "name"))
                )
                t_form = time.perf_counter()
                fill_reservation_form(driver, config, log=_log)
                _log(f"폼 입력+제출: {time.perf_counter() - t_form:.2f}s")
                _log(f"총 소요시간: {time.perf_counter() - t_start:.2f}s")
                break
            else:
                retries += 1
                max_r = config["max_retries"]
                if max_r > 0 and retries >= max_r:
                    _log(f"최대 재시도 {max_r}회 도달. 종료합니다.")
                    break
                _log(f"[재시도 {retries}] 즉시 재시도...")

    except Exception as e:
        _log(f"오류 발생: {e}")
    finally:
        if config.get("test_mode"):
            _log("테스트 완료. 브라우저를 확인 후 직접 닫아주세요.")
        else:
            try:
                driver.quit()
            except Exception:
                pass


# ── GUI ───────────────────────────────────

class MacroApp:
    def __init__(self, root):
        self.root = root
        self.root.title(f"플레이33 예약 매크로  v{VERSION}")
        self.root.resizable(False, False)
        self.stop_event = None
        self.threads = []  # 멀티 스레드 지원
        self.threads_done = 0
        self.reservation_slots = []  # [{theme_name, theme_val, times: [...]}, ...]
        self._build_ui()
        threading.Thread(target=self._check_update_bg, daemon=True).start()
        threading.Thread(target=self._fetch_branches_bg, daemon=True).start()

    def _check_update_bg(self):
        latest_tag, url = check_update()
        if latest_tag and url:
            self.root.after(0, lambda: self._prompt_update(latest_tag, url))

    def _prompt_update(self, latest_tag, url):
        if messagebox.askyesno(
            "업데이트 있음",
            f"새 버전이 있습니다!\n\n현재: v{VERSION}  →  최신: {latest_tag}\n\n지금 업데이트할까요?"
        ):
            self.log(f"업데이트 시작: {latest_tag}")
            threading.Thread(target=self._run_update, args=(url,), daemon=True).start()

    def _run_update(self, url):
        if do_update(url, log=self.log):
            self.log("업데이트 완료. 앱을 재시작합니다...")
            self.root.after(1500, self.root.destroy)

    def _build_ui(self):
        p = {"padx": 8, "pady": 5}

        url_frame = ttk.LabelFrame(self.root, text=" URL 정보 ")
        url_frame.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 4))

        self.branch_map = {}  # {표시이름: value}
        self.theme_map = {}   # {표시이름: value}
        self.theme_times = {}  # {테마이름: [시간들]}

        ttk.Label(url_frame, text="지점").grid(row=0, column=0, **p, sticky="e")
        self.branch_combo = ttk.Combobox(url_frame, width=14, state="readonly")
        self.branch_combo.grid(row=0, column=1, **p)
        self.branch_combo.bind("<<ComboboxSelected>>", self._on_branch_change)

        ttk.Label(url_frame, text="날짜").grid(row=0, column=2, **p, sticky="e")
        self.date = ttk.Entry(url_frame, width=16)
        self.date.insert(0, "2026-07-18")
        self.date.grid(row=0, column=3, **p, sticky="w")

        # 예약 목록 (Treeview)
        slots_frame = ttk.LabelFrame(self.root, text=" 예약 목록 ")
        slots_frame.grid(row=1, column=0, sticky="ew", padx=12, pady=4)

        tree_frame = ttk.Frame(slots_frame)
        tree_frame.pack(padx=8, pady=4, fill="x")

        self.slots_tree = ttk.Treeview(tree_frame, columns=("theme", "times"), show="headings", height=4)
        self.slots_tree.heading("theme", text="테마")
        self.slots_tree.heading("times", text="희망 시간")
        self.slots_tree.column("theme", width=150)
        self.slots_tree.column("times", width=200)
        self.slots_tree.pack(side="left", fill="x", expand=True)

        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.slots_tree.yview)
        tree_scroll.pack(side="right", fill="y")
        self.slots_tree.configure(yscrollcommand=tree_scroll.set)

        btn_frame_slots = ttk.Frame(slots_frame)
        btn_frame_slots.pack(pady=4)
        ttk.Button(btn_frame_slots, text="+ 예약 추가", command=self._show_add_dialog, width=14).pack(side="left", padx=4)
        ttk.Button(btn_frame_slots, text="- 선택 삭제", command=self._remove_reservation, width=14).pack(side="left", padx=4)

        info_frame = ttk.LabelFrame(self.root, text=" 예약자 정보 ")
        info_frame.grid(row=2, column=0, sticky="ew", padx=12, pady=4)

        ttk.Label(info_frame, text="이름").grid(row=0, column=0, **p, sticky="e")
        self.name = ttk.Entry(info_frame, width=14)
        self.name.insert(0, "박태근")
        self.name.grid(row=0, column=1, **p)

        ttk.Label(info_frame, text="인원").grid(row=0, column=2, **p, sticky="e")
        self.headcount = ttk.Combobox(info_frame, values=["2", "3", "4"], width=5, state="readonly")
        self.headcount.set("2")
        self.headcount.grid(row=0, column=3, **p)

        ttk.Label(info_frame, text="전화번호").grid(row=1, column=0, **p, sticky="e")
        self.phone = ttk.Entry(info_frame, width=18)
        self.phone.insert(0, "01024293734")
        self.phone.grid(row=1, column=1, columnspan=3, **p, sticky="w")

        cfg_frame = ttk.LabelFrame(self.root, text=" 설정 ")
        cfg_frame.grid(row=3, column=0, sticky="ew", padx=12, pady=4)

        ttk.Label(cfg_frame, text="오픈 시각").grid(row=0, column=0, **p, sticky="e")
        self.open_time = ttk.Entry(cfg_frame, width=12)
        self.open_time.insert(0, "20:00:00")
        self.open_time.grid(row=0, column=1, **p)

        self.use_open_time = tk.BooleanVar(value=True)
        ttk.Checkbutton(cfg_frame, text="오픈 시각까지 자동 대기", variable=self.use_open_time).grid(
            row=0, column=2, **p)

        self.test_mode = tk.BooleanVar(value=False)
        ttk.Checkbutton(cfg_frame, text="테스트 모드 (제출 직전 중단)", variable=self.test_mode).grid(
            row=1, column=0, columnspan=3, **p, sticky="w")

        btn_frame = ttk.Frame(self.root)
        btn_frame.grid(row=4, column=0, pady=8)

        self.run_btn = ttk.Button(btn_frame, text="▶  실행", command=self.start, width=16)
        self.run_btn.grid(row=0, column=0, padx=8)

        self.stop_btn = ttk.Button(btn_frame, text="■  중지", command=self.stop, width=16, state="disabled")
        self.stop_btn.grid(row=0, column=1, padx=8)

        log_frame = ttk.LabelFrame(self.root, text=" 로그 ")
        log_frame.grid(row=5, column=0, sticky="nsew", padx=12, pady=(4, 12))

        self.log_box = scrolledtext.ScrolledText(
            log_frame, width=58, height=13, state="disabled",
            font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4",
            insertbackground="white"
        )
        self.log_box.pack(padx=6, pady=6)

    def log(self, msg):
        def _update():
            self.log_box.config(state="normal")
            ts = datetime.datetime.now().strftime("%H:%M:%S")
            self.log_box.insert(tk.END, f"[{ts}] {msg}\n")
            self.log_box.see(tk.END)
            self.log_box.config(state="disabled")
        self.root.after(0, _update)

    def _fetch_branches_bg(self):
        try:
            options = fetch_branches()
            self.branch_map = {text: val for val, text in options}
            names = list(self.branch_map.keys())
            self.root.after(0, lambda: self._update_branch_combo(names))
        except Exception as e:
            self.root.after(0, lambda: self.log(f"지점 불러오기 실패: {e}"))

    def _update_branch_combo(self, names):
        self.branch_combo["values"] = names
        if names:
            self.branch_combo.set(names[0])
            self._on_branch_change()

    def _on_branch_change(self, event=None):
        name = self.branch_combo.get()
        val = self.branch_map.get(name)
        if val:
            self.theme_map = {}
            threading.Thread(target=self._fetch_themes_bg, args=(val,), daemon=True).start()

    def _fetch_themes_bg(self, branch_val):
        try:
            options = fetch_themes(branch_val)
            self.theme_map = {text: val for val, text in options}
            self.theme_times = {}
            self.root.after(0, lambda: self.log(f"테마 {len(self.theme_map)}개 로드 완료. 시간 로딩 중..."))

            # 오늘 날짜로 각 테마별 시간 병렬 로딩
            today = datetime.date.today().isoformat()
            from concurrent.futures import ThreadPoolExecutor, as_completed

            def fetch_one(theme_name, theme_val):
                try:
                    times = fetch_time_slots(branch_val, theme_val, today)
                    return theme_name, times
                except Exception:
                    return theme_name, []

            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = {
                    executor.submit(fetch_one, name, val): name
                    for name, val in self.theme_map.items()
                }
                for future in as_completed(futures):
                    theme_name, times = future.result()
                    self.theme_times[theme_name] = times
                    self.root.after(0, lambda n=theme_name, t=times: self.log(f"  [{n}] 시간 {len(t)}개"))

            self.root.after(0, lambda: self.log("모든 테마 시간 로드 완료"))
        except Exception as e:
            self.root.after(0, lambda: self.log(f"테마 불러오기 실패: {e}"))

    def _show_add_dialog(self):
        """예약 추가 다이얼로그 표시"""
        if not self.theme_map:
            messagebox.showwarning("알림", "지점을 먼저 선택하고 테마가 로드될 때까지 기다려주세요.")
            return
        if not self.theme_times:
            messagebox.showwarning("알림", "시간 로딩 중입니다. 잠시 후 다시 시도해주세요.")
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("예약 추가")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        p = {"padx": 8, "pady": 5}

        # 테마 선택
        ttk.Label(dialog, text="테마").grid(row=0, column=0, **p, sticky="e")
        theme_combo = ttk.Combobox(dialog, values=list(self.theme_map.keys()), width=20, state="readonly")
        theme_combo.grid(row=0, column=1, **p)
        if self.theme_map:
            theme_combo.set(list(self.theme_map.keys())[0])

        # 시간 리스트
        times_frame = ttk.Frame(dialog)
        times_frame.grid(row=1, column=0, columnspan=2, padx=8, pady=4)

        ttk.Label(times_frame, text="전체 시간", foreground="gray").grid(row=0, column=0)
        ttk.Label(times_frame, text="선택한 시간", foreground="gray").grid(row=0, column=2)

        all_times_lb = tk.Listbox(times_frame, height=6, width=11, selectmode=tk.SINGLE, exportselection=False)
        all_times_lb.grid(row=1, column=0, padx=4)

        mid_frame = ttk.Frame(times_frame)
        mid_frame.grid(row=1, column=1, padx=4)

        selected_times_lb = tk.Listbox(times_frame, height=6, width=11, selectmode=tk.SINGLE, exportselection=False)
        selected_times_lb.grid(row=1, column=2, padx=4)

        def update_times():
            """캐시된 시간으로 리스트 업데이트"""
            theme_name = theme_combo.get()
            times = self.theme_times.get(theme_name, [])
            all_times_lb.delete(0, tk.END)
            for t in times:
                all_times_lb.insert(tk.END, t)

        def add_time():
            sel = all_times_lb.curselection()
            if sel:
                t = all_times_lb.get(sel[0])
                if t not in selected_times_lb.get(0, tk.END):
                    selected_times_lb.insert(tk.END, t)

        def remove_time():
            sel = selected_times_lb.curselection()
            if sel:
                selected_times_lb.delete(sel[0])

        ttk.Button(mid_frame, text=">>", command=add_time, width=4).pack(pady=2)
        ttk.Button(mid_frame, text="<<", command=remove_time, width=4).pack(pady=2)

        # 확인/취소 버튼
        btn_frame = ttk.Frame(dialog)
        btn_frame.grid(row=2, column=0, columnspan=2, pady=8)

        def on_ok():
            theme_name = theme_combo.get()
            theme_val = self.theme_map.get(theme_name)
            times = list(selected_times_lb.get(0, tk.END))
            if not theme_val:
                messagebox.showwarning("알림", "테마를 선택해주세요.")
                return
            if not times:
                messagebox.showwarning("알림", "희망 시간을 하나 이상 선택해주세요.")
                return

            # 예약 슬롯 추가
            self.reservation_slots.append({
                "theme_name": theme_name,
                "theme_val": theme_val,
                "times": times
            })
            self._refresh_slots_tree()
            dialog.destroy()

        ttk.Button(btn_frame, text="추가", command=on_ok, width=10).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="취소", command=dialog.destroy, width=10).pack(side="left", padx=4)

        # 테마 변경 시 시간 업데이트
        theme_combo.bind("<<ComboboxSelected>>", lambda e: update_times())
        update_times()

    def _refresh_slots_tree(self):
        """Treeview 새로고침"""
        for item in self.slots_tree.get_children():
            self.slots_tree.delete(item)
        for slot in self.reservation_slots:
            times_str = ", ".join(slot["times"])
            self.slots_tree.insert("", "end", values=(slot["theme_name"], times_str))

    def _remove_reservation(self):
        """선택한 예약 슬롯 삭제"""
        selected = self.slots_tree.selection()
        if not selected:
            messagebox.showwarning("알림", "삭제할 예약을 선택해주세요.")
            return
        idx = self.slots_tree.index(selected[0])
        del self.reservation_slots[idx]
        self._refresh_slots_tree()

    def start(self):
        try:
            if not self.reservation_slots:
                raise ValueError("예약 목록이 비어있습니다.\n[+ 예약 추가] 버튼으로 테마와 시간을 추가해주세요.")
            branch_val = self.branch_map.get(self.branch_combo.get())
            if not branch_val:
                raise ValueError("지점을 선택해주세요.")

            # 공통 설정
            common = {
                "branch":      int(branch_val),
                "date":        self.date.get().strip(),
                "name":        self.name.get().strip(),
                "phone":       self.phone.get().strip(),
                "headcount":   self.headcount.get(),
                "max_retries": 0,
                "open_time":   self.open_time.get().strip() if self.use_open_time.get() else None,
                "test_mode":   self.test_mode.get(),
            }
        except ValueError as e:
            messagebox.showerror("입력 오류", str(e))
            return

        self.log_box.config(state="normal")
        self.log_box.delete("1.0", tk.END)
        self.log_box.config(state="disabled")

        self.stop_event = threading.Event()
        self.run_btn.config(state="disabled")
        self.stop_btn.config(state="normal")

        # 각 예약 슬롯별로 스레드 생성
        self.threads = []
        self.threads_done = 0
        for slot in self.reservation_slots:
            config = {
                **common,
                "theme": int(slot["theme_val"]),
                "preferred_times": slot["times"],
            }
            label = slot["theme_name"]
            t = threading.Thread(target=self._worker, args=(config, label), daemon=True)
            self.threads.append(t)

        self.log(f"총 {len(self.threads)}개 예약 매크로 시작")

        # 모든 스레드 동시 시작
        for t in self.threads:
            t.start()

    def _worker(self, config, label=""):
        try:
            run_macro(config, log=self.log, stop_event=self.stop_event, label=label)
        except Exception as e:
            self.log(f"[{label}] 오류 발생: {e}" if label else f"오류 발생: {e}")
        finally:
            self.root.after(0, self._on_thread_done)

    def _on_thread_done(self):
        self.threads_done += 1
        if self.threads_done >= len(self.threads):
            self._on_done()

    def _on_done(self):
        self.run_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.log("=== 모든 매크로 종료 ===")

    def stop(self):
        if self.stop_event:
            self.stop_event.set()
            self.log("중지 요청...")


if __name__ == "__main__":
    root = tk.Tk()
    MacroApp(root)
    root.mainloop()
