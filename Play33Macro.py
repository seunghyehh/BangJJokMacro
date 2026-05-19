"""
플레이33 (play33.kr) 방탈출 예약 매크로
Selenium 기반 - Chrome 브라우저 자동화
"""

import time
import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import Select
from selenium.common.exceptions import TimeoutException, UnexpectedAlertPresentException, InvalidSessionIdException
from webdriver_manager.chrome import ChromeDriverManager

# ─────────────────────────────────────────
# ★ 여기만 수정하세요
# ─────────────────────────────────────────
CONFIG = {
    "branch":          5,                  # URL의 branch= 숫자
    "theme":           12,                 # URL의 theme= 숫자
    "date":            "2026-05-25",       # YYYY-MM-DD
    "preferred_times": ["22:20","19:10"], # 희망 시간 우선순위 (HH:MM)
    "name":            "홍승혜",
    "phone":           "01024293734",      # 하이픈 있어도 없어도 됨
    "headcount":       "2",
    "max_retries":     0,                  # 최대 재시도 횟수, 0 = 무한 반복
    "headless":        False,              # True = 브라우저 창 숨김
    "open_time":       "10:00:00",         # 예약 오픈 시각 (HH:MM:SS), None이면 즉시 시작
}
# ─────────────────────────────────────────

BASE_URL = "https://play33.kr/reservation"


def build_driver(headless: bool) -> webdriver.Chrome:
    options = webdriver.ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1280,900")
    options.page_load_strategy = "eager"  # DOM 완성 즉시 진행, 이미지/폰트 대기 안 함
    options.add_experimental_option("prefs", {
        "profile.managed_default_content_settings.images": 2  # 이미지 로딩 비활성화
    })
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=options)


def build_url(config: dict) -> str:
    return (
        f"{BASE_URL}"
        f"?branch={config['branch']}"
        f"&theme={config['theme']}"
        f"&date={config['date']}"
        f"#content"
    )


def format_phone(phone: str) -> str:
    """전화번호를 하이픈 포함 형식으로 변환 (01012345678 → 010-1234-5678)"""
    phone = phone.replace("-", "")
    if len(phone) == 11:
        return f"{phone[:3]}-{phone[3:7]}-{phone[7:]}"
    elif len(phone) == 10:
        return f"{phone[:3]}-{phone[3:6]}-{phone[6:]}"
    return phone


def wait_until_open(open_time_str, driver=None, url=None):
    """오픈 시각까지 정밀 대기. 3초 전 페이지 미리 로드로 캐시 워밍, 마지막 1초는 busy-wait."""
    if not open_time_str:
        return
    target = datetime.datetime.combine(
        datetime.date.today(),
        datetime.time.fromisoformat(open_time_str)
    )
    now = datetime.datetime.now()
    if now >= target:
        print(f"  이미 오픈 시각({open_time_str})이 지났습니다. 즉시 시작합니다.")
        return
    wait_secs = (target - now).total_seconds()
    print(f"  오픈까지 {wait_secs:.0f}초 대기 중... (현재: {now.strftime('%H:%M:%S')})")

    # 3초 전까지 sleep
    pre_load_offset = 3
    if wait_secs > pre_load_offset + 1:
        time.sleep(wait_secs - pre_load_offset - 1)

    # 오픈 3초 전: 페이지 미리 로드해서 CSS/JS 캐시 워밍
    if driver and url and datetime.datetime.now() < target:
        print("  [캐시 워밍] 페이지 미리 로드 중...")
        try:
            driver.get(url)
            try:
                WebDriverWait(driver, 1).until(EC.alert_is_present())
                driver.switch_to.alert.accept()
            except Exception:
                pass
        except Exception:
            pass

    # 마지막 1초: busy-wait으로 정밀하게
    while datetime.datetime.now() < target:
        pass
    print(f"  >>> 오픈! 시작 시각: {datetime.datetime.now().strftime('%H:%M:%S.%f')[:-3]}")


def find_available_time(driver, preferred_times):
    """희망 시간 중 예약 가능한 슬롯 찾기. 반환: (시간, 요소) or (None, None)"""
    WebDriverWait(driver, 10, poll_frequency=0.1).until(
        EC.presence_of_element_located((By.XPATH, "//*[contains(text(),'예약 가능') or contains(text(),'예약 불가')]"))
    )

    available = {}
    slots = driver.find_elements(By.XPATH, "//*[contains(text(),'예약 가능')]")
    for slot in slots:
        text = slot.text.strip()
        for pt in preferred_times:
            if pt in text:
                available[pt] = slot

    for pt in preferred_times:
        if pt in available:
            return pt, available[pt]

    return None, None


def fill_reservation_form(driver, config):
    """예약 폼 입력 및 제출"""
    print("[2] 예약 정보 입력")

    def fill_field_js(name_attr, value):
        elems = driver.find_elements(By.NAME, name_attr)
        if elems:
            driver.execute_script("arguments[0].value = arguments[1];", elems[0], value)
            return True
        print(f"  [!] name='{name_attr}' 필드를 찾지 못했습니다.")
        return False

    fill_field_js("name", config["name"])
    fill_field_js("phone", format_phone(config["phone"]))

    # 인원 선택
    people_elems = driver.find_elements(By.NAME, "people")
    if people_elems:
        driver.execute_script("arguments[0].value = arguments[1];", people_elems[0], config["headcount"])
    else:
        print("  [!] name='people' 인원 선택 필드를 찾지 못했습니다.")

    # 개인정보 동의 체크박스 (hidden input이므로 JS로 클릭)
    policy_elems = driver.find_elements(By.NAME, "policy")
    if policy_elems:
        if not policy_elems[0].is_selected():
            driver.execute_script("arguments[0].click();", policy_elems[0])
    else:
        print("  [!] name='policy' 동의 체크박스를 찾지 못했습니다.")

    print("[3] 예약 제출")
    submit_btns = driver.find_elements(
        By.XPATH,
        "//button[contains(text(),'예약') or contains(text(),'확인') or contains(text(),'완료')] | "
        "//input[@type='submit']"
    )
    if submit_btns:
        submit_btns[0].click()
        print("  [OK] 예약 제출 완료! 완료 화면을 확인하세요.")
    else:
        print("  [!] 제출 버튼을 찾지 못했습니다. 수동으로 확인해주세요.")


def run(config):
    t_total_start = time.perf_counter()
    driver = build_driver(config["headless"])
    t_browser_ready = time.perf_counter()

    retries = 0
    url = build_url(config)

    try:
        print("=" * 50)
        print("  플레이33 예약 매크로 시작")
        print(f"  branch={config['branch']} | theme={config['theme']}")
        print(f"  날짜: {config['date']} | 희망시간: {config['preferred_times']}")
        print(f"  URL: {url}")
        print(f"  [시간] 브라우저 초기화: {t_browser_ready - t_total_start:.2f}s")
        print("=" * 50)

        wait_until_open(config.get("open_time"), driver, url)

        while True:
            t_load_start = time.perf_counter()
            driver.get(url)
            # 날짜 오픈 전 알림창 자동 닫기
            try:
                WebDriverWait(driver, 1).until(EC.alert_is_present())
                driver.switch_to.alert.accept()
            except Exception:
                pass
            t_load_end = time.perf_counter()
            print(f"  [시간] 페이지 로드: {t_load_end - t_load_start:.2f}s")

            try:
                time_str, slot_elem = find_available_time(driver, config["preferred_times"])
            except (UnexpectedAlertPresentException, TimeoutException):
                try:
                    driver.switch_to.alert.accept()
                except Exception:
                    pass
                time_str, slot_elem = None, None
            except InvalidSessionIdException:
                print("\n브라우저가 닫혔습니다. 종료합니다.")
                return
            t_find_end = time.perf_counter()
            print(f"  [시간] 슬롯 탐색: {t_find_end - t_load_end:.2f}s")

            if time_str:
                print(f"[1] '{time_str}' 예약 가능! 클릭합니다.")
                slot_elem.click()

                # 폼이 나타날 때까지 대기 (고정 sleep 대신 조건부 대기)
                WebDriverWait(driver, 5, poll_frequency=0.1).until(
                    EC.presence_of_element_located((By.NAME, "name"))
                )

                t_form_start = time.perf_counter()
                fill_reservation_form(driver, config)
                t_form_end = time.perf_counter()
                print(f"  [시간] 폼 입력+제출: {t_form_end - t_form_start:.2f}s")
                print(f"  [시간] 총 소요시간: {t_form_end - t_total_start:.2f}s")
                break

            else:
                retries += 1
                max_r = config["max_retries"]
                if max_r > 0 and retries >= max_r:
                    print(f"[!] 최대 재시도 횟수({max_r})에 도달했습니다. 종료합니다.")
                    break

                print(f"[재시도 {retries}] 원하는 시간 없음 → 즉시 재시도...")

        print("\n브라우저를 닫으려면 Enter를 누르세요...")
        input()

    except KeyboardInterrupt:
        print("\n사용자가 중단했습니다.")
    finally:
        driver.quit()


if __name__ == "__main__":
    run(CONFIG)