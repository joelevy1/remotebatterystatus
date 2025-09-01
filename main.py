from machine import Pin, RTC
import time
import network
import machine
import urequests
import os

#version number is 1.3
VERSION_FILE = "version"
UPDATE_URL = "https://raw.githubusercontent.com/joelevy1/remotebatterystatus/main/main.py"
led = machine.Pin("LED", machine.Pin.OUT)
led_green = machine.Pin(15, machine.Pin.OUT)
led_blue = machine.Pin(14, machine.Pin.OUT)
led_red = machine.Pin(13, machine.Pin.OUT)
url_base = "https://script.google.com/macros/s/AKfycbwedmNVAOrmMc5MsHAiAJmoXPaVYCO2CTXSQS2e99bMg8v-F12ImHTUD_F_Uk1MZkQ4/exec?"
FAIL_FILE = "fail_count.txt"
SLEEP_MS = 300_000   # sleep time in milliseconds
WIFI_SSID = "Levy-Guest"
WIFI_PASSWORD = "welcomehome"
#WIFI_SSID = "Seattle Boat"
#WIFI_PASSWORD = "seaboats"
rtc = RTC() # RTC to store fail count across deep sleep


sensor_temp = machine.ADC(4)




# Fetch variables from Google Sheets
def fetch_vars(retries=3):
    for attempt in range(retries):
        try:
            url_read = url_base + "action=read&"
            response = urequests.get(url_read)
            data = response.json()
            response.close()
            return data
        except Exception as e:
            print(f"Attempt {attempt+1} failed: {e}")
            time.sleep(1)
    print("Failed to fetch variables after retries")
    return None


def get_local_version():
    try:
        with open(VERSION_FILE, "r") as f:
            return f.read().strip()
    except:
        return "0.0"  # default if file missing

def set_local_version(version):
    try:
        # ensure we have a string
        version_str = str(version)
        with open(VERSION_FILE, "w") as f:
            f.write(str(version_str)) 
            f.flush()      # force write to internal buffer
            os.sync()           # ensure filesystem is fully written to flash
    except Exception as e:
        print("Failed to write version file:", e)

def download_new_version(url):
    try:
        print("Downloading new version...")
        response = urequests.get(url)
        new_code = response.text
        print("Downloaded snippet:")
        print(new_code[:100])
        response.close()
        
        # Basic check: ensure it looks like Python code
        if not new_code.strip().startswith(("def", "import", "#", "from")):
            print("Downloaded content does not look like Python code!")
            return False
        
        # Save new code to main.py
        with open("main.py", "w") as f:
            f.write(new_code)
        
        print("Update downloaded successfully!")
        return True
    except Exception as e:
        print("Failed to download new version:", e)
        return False



def version_tuple(v):
    """
    Convert a version string or float into a tuple of ints.
    Examples:
        "1.2" -> (1,2)
        1.2   -> (1,2)
        "1"   -> (1,0)
    """
    try:
        # Convert floats to string first
        v_str = str(v)
        parts = v_str.split(".")
        # make sure we always have at least 2 parts
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
        return (major, minor)
    except:
        return (0, 0)


def check_for_update(sheet_version):
    local_version_tuple = version_tuple(get_local_version())
    print("Local version:", local_version_tuple)
    sheet_version_tuple = version_tuple(sheet_version)
    print("Sheet version:", sheet_version_tuple)
    if sheet_version_tuple > local_version_tuple:
        print("New version available:", sheet_version)
        if download_new_version(UPDATE_URL):
            set_local_version(sheet_version)
            print("Restarting Pico to apply update...")
            time.sleep(2)
            machine.reset()
    else:
       print("Already up to date.")

def read_temperature_f():
    # Read internal sensor in Celsius
    reading = sensor_temp.read_u16()
    voltage = reading * 3.3 / 65535
    temp_c = 27 - (voltage - 0.706)/0.001721
    
    # Convert to Fahrenheit
    temp_f = temp_c * 9 / 5 + 32
    return round(temp_f, 1)

def connect_wifi(max_attempts=25):
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    if wlan.isconnected():
        print("Already connected:", wlan.ifconfig())
        led_blue.value(1) #blue light on for wifi on
        return wlan, 0

    print("Connecting to Wi-Fi:", WIFI_SSID)
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)

    attempts = 0
    while not wlan.isconnected() and attempts < max_attempts:
        led_blue.value(0)
        time.sleep(.3)
        led_blue.value(1)
        time.sleep(.7)
        attempts += 1
        print(".", end="")  # show progress

    if wlan.isconnected():
        led_blue.value(1)
        print("\nConnected in ", attempts, " attempts! IP:", wlan.ifconfig()[0])
        return wlan, attempts
    else:
        led_blue.value(0)
        print("\nFailed to connect after {} attempts.".format(max_attempts))
        wlan.active(False)  # turn off radio
        return None, attempts

def disconnect_wifi(wlan):
    if wlan:
        wlan.disconnect()
        wlan.active(False)   # shuts down the radio
        led_blue.value(0)
        print("WiFi disconnected and radio off")

    
def log_to_google(params):
    try:
        url_write = url_base + "action=write&"
        query = urlencode(params)
        url = "{}?{}".format(url_write, query)
        response = urequests.get(url)
        text = response.text
        response.close()
        return text
    except Exception as e:
        return "Error: {}".format(e)
  

def get_fail_count():
    try:
        with open(FAIL_FILE, "r") as f:
            return int(f.read())
    except:
        return 0
    

def set_fail_count(value):
    try:
        with open(FAIL_FILE, "w") as f:
            f.write(str(value))
    except:
        pass

def urlencode(params):
    def esc(s):
        return str(s).replace(" ", "%20").replace(":", "%3A").replace("/", "%2F")
    return "&".join("{}={}".format(k, esc(params[k])) for k in params)


def main():
    time.sleep(1)  # 
    led_green.value(1)
    led_blue.value(0)
    led_red.value(0)
    wlan, attempts = connect_wifi()
    fail_count = get_fail_count()
    temp_f = read_temperature_f()
    local_version = get_local_version()  


    if wlan is None or not wlan.isconnected():
        # Wi-Fi failed
        fail_count += 1
        set_fail_count(fail_count)
        print(f"Wi-Fi failed {fail_count} times, skipping trying to upload to goolge")
    else:
        # Wi-Fi connected
        #first check varaibles and version number for possible update
        vars_from_sheet = fetch_vars()
        if vars_from_sheet:
            print("Variables from Google Sheets:")
            for key, val in vars_from_sheet.items():
                print(f"  {key}: {val}")
       
        # Update sleep time from sheet
        sleep_seconds = vars_from_sheet.get("Sleep-seconds")
        if sleep_seconds is not None:
        try:
            sleep_sec = int(vars_from_sheet.get("Sleep-seconds", SLEEP_MS // 1000))
            SLEEP_MS = sleep_sec * 1000
        except Exception as e:
            print("Failed to update SLEEP_MS from sheet:", e)


            
            sheet_version = str(vars_from_sheet.get("Version", "0.0"))
            print ("Sheet version = " + str(sheet_version))
            check_for_update(sheet_version)
        else:
            print("Skipping update check because fetch failed")
 
    
        #now update google
        ip = wlan.ifconfig()[0]
        data = {
            "Rounds_to_Connect": fail_count,
            "Wifi_Attempts_This_Round": attempts,
            "IP_address": ip,
            "Temp": temp_f,
            "House_Battery": "13.4",
            "Engine_Battery": "14.4",
            "Engine_Solar": "13.9",
            "House_Solar": "12.3"
            "Local_Version": local_version
        }
        result = log_to_google(data)
        print("Google Sheets response:", result)
        if result == "OK":
            led_red.value(1)
            fail_count = 0
            set_fail_count(fail_count)
            time.sleep(1)
    print("Going to sleep...")
    led_red.value(0)
    led_blue.value(0)
    led_green.value(0)
    disconnect_wifi(wlan)
    time.sleep(0.2)
    machine.deepsleep(SLEEP_MS)

#while True:
main()
#    time.sleep(300)
#    time.sleep(5)






