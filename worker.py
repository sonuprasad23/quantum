# worker.py
import time
import threading
import tempfile
import shutil
import os

from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException


class QuantumBot:
    def __init__(self, socketio, app):
        self.socketio = socketio
        self.app = app
        self.driver = None
        self.DEFAULT_TIMEOUT = 30
        self.termination_event = threading.Event()
        self.temp_user_dir = None
        self.temp_cache_dir = None

    def initialize_driver(self):
        try:
            self.micro_status("Initializing headless browser...")
            
            # Create isolated temporary directories for Railway
            self.temp_user_dir = tempfile.mkdtemp(prefix="railway-chrome-user-")
            self.temp_cache_dir = tempfile.mkdtemp(prefix="railway-chrome-cache-")
            
            options = ChromeOptions()
            
            # Railway-specific Chrome configuration
            options.binary_location = "/usr/bin/chromium"
            options.add_argument(f"--user-data-dir={self.temp_user_dir}")
            options.add_argument(f"--disk-cache-dir={self.temp_cache_dir}")
            options.add_argument("--remote-debugging-port=0")
            
            # Headless and stability flags
            options.add_argument("--headless=new")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--disable-gpu")
            options.add_argument("--disable-software-rasterizer")
            options.add_argument("--no-first-run")
            options.add_argument("--no-default-browser-check")
            options.add_argument("--disable-extensions")
            options.add_argument("--disable-plugins")
            options.add_argument("--window-size=1920,1080")
            options.add_argument("--disable-features=Translate,AutomationControlled")
            
            # User agent for better compatibility
            user_agent = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            options.add_argument(f"--user-agent={user_agent}")
            
            service = ChromeService(executable_path="/usr/bin/chromedriver")
            self.driver = webdriver.Chrome(service=service, options=options)
            
            # Anti-detection
            self.driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
                'source': "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            })
            
            return True, None
            
        except Exception as e:
            error_message = f"Message: {str(e)}"
            print(f"CRITICAL ERROR in WebDriver Initialization: {error_message}")
            self._cleanup_temp_dirs()
            return False, error_message

    def _cleanup_temp_dirs(self):
        """Clean up temporary directories"""
        for temp_dir in [self.temp_user_dir, self.temp_cache_dir]:
            if temp_dir and os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)

    def micro_status(self, message):
        print(f"[Bot Action] {message}")
        with self.app.app_context():
            self.socketio.emit('micro_status_update', {'message': message})

    def stop(self):
        self.micro_status("Termination signal received. Finishing current patient...")
        self.termination_event.set()

    def login(self, username, password):
        try:
            self.micro_status("Navigating to login page...")
            self.driver.get("https://gateway.quantumepay.com/")
            time.sleep(2)
            self.micro_status("Entering credentials...")
            WebDriverWait(self.driver, self.DEFAULT_TIMEOUT).until(
                EC.presence_of_element_located((By.ID, "Username"))
            ).send_keys(username)
            WebDriverWait(self.driver, self.DEFAULT_TIMEOUT).until(
                EC.presence_of_element_located((By.ID, "Password"))
            ).send_keys(password)
            WebDriverWait(self.driver, self.DEFAULT_TIMEOUT).until(
                EC.element_to_be_clickable((By.ID, "login"))
            ).click()
            self.micro_status("Waiting for OTP screen...")
            WebDriverWait(self.driver, self.DEFAULT_TIMEOUT).until(
                EC.presence_of_element_located((By.ID, "code1"))
            )
            return True, None
        except Exception as e:
            error_message = f"Error during login: {str(e)}"
            print(f"[Bot] ERROR during login: {error_message}")
            return False, error_message

    def submit_otp(self, otp):
        try:
            self.micro_status(f"Submitting OTP...")
            otp_digits = list(otp)
            for i in range(6):
                self.driver.find_element(By.ID, f"code{i+1}").send_keys(otp_digits[i])
            WebDriverWait(self.driver, self.DEFAULT_TIMEOUT).until(
                EC.element_to_be_clickable((By.ID, "login"))
            ).click()
            self.micro_status("Verifying login success...")
            WebDriverWait(self.driver, self.DEFAULT_TIMEOUT).until(
                EC.element_to_be_clickable((By.XPATH, "//span[text()='Payments']"))
            )
            return True, None
        except Exception as e:
            error_message = f"Error during OTP submission: {str(e)}"
            print(f"[Bot] ERROR during OTP submission: {error_message}")
            return False, error_message

    def process_patient_list(self, patient_list):
        results = []
        for index, patient_name in enumerate(patient_list):
            if self.termination_event.is_set():
                print("[Bot] Termination detected. Stopping process.")
                break
            with self.app.app_context():
                self.socketio.emit('stats_update', {
                    'processed': len(results),
                    'remaining': len(patient_list) - len(results)
                })
            self.micro_status(f"Processing '{patient_name}' ({index + 1}/{len(patient_list)})...")
            status = self._process_single_patient(patient_name)
            results.append({'Name': patient_name, 'Status': status})
            with self.app.app_context():
                self.socketio.emit('log_update', {'name': patient_name, 'status': status})
        return results

    def _process_single_patient(self, patient_name):
        try:
            self.micro_status(f"Navigating to Void page for '{patient_name}'")
            self.driver.get("https://gateway.quantumepay.com/credit-card/void")

            search_successful = False
            for attempt in range(15):
                try:
                    self.micro_status(f"Searching for patient (Attempt {attempt + 1})...")
                    WebDriverWait(self.driver, 2).until(
                        EC.presence_of_element_located((By.XPATH, "//div[contains(@class, 'table-wrapper')]"))
                    )
                    search_box = WebDriverWait(self.driver, 2).until(
                        EC.element_to_be_clickable((By.XPATH, "//input[@placeholder='Search']"))
                    )
                    search_box.click(); time.sleep(0.5)
                    search_box.clear(); time.sleep(0.5)
                    search_box.send_keys(patient_name)
                    search_successful = True
                    break
                except Exception:
                    time.sleep(1)

            if not search_successful:
                raise Exception("Failed to search for patient.")

            time.sleep(3)
            self.micro_status("Opening transaction details...")
            WebDriverWait(self.driver, self.DEFAULT_TIMEOUT).until(
                EC.element_to_be_clickable((By.XPATH, f"//tr[contains(., \"{patient_name}\")]//button[@data-v-b6b33fa0]"))
            ).click()
            WebDriverWait(self.driver, self.DEFAULT_TIMEOUT).until(
                EC.element_to_be_clickable((By.LINK_TEXT, "Transaction Detail"))
            ).click()

            self.micro_status("Adding to Vault...")
            WebDriverWait(self.driver, self.DEFAULT_TIMEOUT).until(
                EC.element_to_be_clickable((By.XPATH, "//button/span[normalize-space()='Add to Vault']"))
            ).click()
            WebDriverWait(self.driver, self.DEFAULT_TIMEOUT).until(
                EC.element_to_be_clickable((By.XPATH, "//div[@class='modal-footer']//button/span[normalize-space()='Confirm']"))
            ).click()

            try:
                self.micro_status("Verifying success and saving...")
                company_input = WebDriverWait(self.driver, 10).until(
                    EC.element_to_be_clickable((By.NAME, "company_name"))
                )
                company_input.clear()
                company_input.send_keys(patient_name)
                WebDriverWait(self.driver, self.DEFAULT_TIMEOUT).until(
                    EC.element_to_be_clickable((By.XPATH, "//button/span[normalize-space()='Save Changes']"))
                ).click()
                WebDriverWait(self.driver, self.DEFAULT_TIMEOUT).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[.//span[normalize-space()='Confirm']]"))
                ).click()
                time.sleep(5)
                return 'Done'
            except TimeoutException:
                self.micro_status(f"'{patient_name}' is in a bad state, cancelling.")
                WebDriverWait(self.driver, self.DEFAULT_TIMEOUT).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[.//span[normalize-space()='Cancel']]"))
                ).click()
                return 'Bad'
        except Exception as e:
            print(f"An error occurred while processing {patient_name}: {e}")
            return 'Error'

    def shutdown(self):
        try:
            if self.driver:
                self.driver.quit()
            self._cleanup_temp_dirs()
            print("[Bot] Chrome session closed and cleaned up.")
        except Exception as e:
            print(f"[Bot] Error during shutdown: {e}")
