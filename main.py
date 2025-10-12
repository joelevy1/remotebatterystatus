from machine import Pin, RTC, I2C
import time
import network
import machine
import urequests
import os


# Version number
VERSION = "2.0"
VERSION_FILE = "version"
UPDATE_URL = "https://raw.githubusercontent.com/joelevy1/remotebatterystatus/main/main.py"

# LED pins
led = machine.Pin("LED", machine.Pin.OUT)
led_green = machine.Pin(15, machine.Pin.OUT)
led_blue = machine.Pin(14, machine.Pin.OUT)
led_red = machine.Pin(13, machine.Pin.OUT)

# Google Sheets API
url_base = "https://script.google.com/macros/s/AKfycbwedmNVAOrmMc5MsHAiAJmoXPaVYCO2CTXSQS2e99bMg8v-F12ImHTUD_F_Uk1MZkQ4/exec?"

# Configuration
FAIL_FILE = "fail_count.txt"
MAH_FILE = "total_mah.txt"
SLEEP_MS = 300_000   # sleep time in milliseconds
KNOWN_NETWORKS = [
    {"ssid": "Levy-Guest", "password": "welcomehome"},
    {"ssid": "Seattle Boat", "password": "seaboats"},
    {"ssid": "Joe's iPhone", "password": "123456789"},
]
rtc = RTC()

# Temperature sensor
sensor_temp = machine.ADC(4)

# Temperature calibration constants
TEMP_OFFSET = 27
TEMP_VOLTAGE_REF = 0.706
TEMP_SLOPE = 0.001721


# INA260 Class (for battery monitoring)
class INA260:
    """Driver for INA260 Current/Voltage/Power sensor"""
    
    # Register addresses
    REG_CURRENT = 0x01
    REG_VOLTAGE = 0x02
    REG_POWER = 0x03
    
    def __init__(self, i2c, address=0x40):
        self.i2c = i2c
        self.addr = address
        
    def read_current(self):
        """Returns current in amps"""
        try:
            data = self.i2c.readfrom_mem(self.addr, self.REG_CURRENT, 2)
            raw = (data[0] << 8) | data[1]
            # Handle signed integer
            if raw > 32767:
                raw -= 65536
            return raw * 1.25 / 1000  # 1.25 mA per bit
        except Exception as e:
            print(f"Error reading current: {e}")
            return 0.0
    
    def read_voltage(self):
        """Returns voltage in volts"""
        try:
            data = self.i2c.readfrom_mem(self.addr, self.REG_VOLTAGE, 2)
            raw = (data[0] << 8) | data[1]
            return raw * 1.25 / 1000  # 1.25 mV per bit
        except Exception as e:
            print(f"Error reading voltage: {e}")
            return 0.0
    
    def read_power(self):
        """Returns power in watts"""
        try:
            data = self.i2c.readfrom_mem(self.addr, self.REG_POWER, 2)
            raw = (data[0] << 8) | data[1]
            return raw * 10 / 1000  # 10 mW per bit
        except Exception as e:
            print(f"Error reading power: {e}")
            return 0.0


# INA219 Class (for Pico power monitoring)
class INA219:
    """Driver for INA219 Power Monitor (monitors Pico consumption)"""
    
    def __init__(self, i2c, address=0x40):
        self.i2c = i2c
        self.addr = address
        # Configure and calibrate
        self._write_register(0x00, 0x399F)  # Config
        self._write_register(0x05, 4096)    # Calibration
        
    def _write_register(self, reg, value):
        self.i2c.writeto_mem(self.addr, reg, value.to_bytes(2, 'big'))
        
    def _read_register(self, reg):
        data = self.i2c.readfrom_mem(self.addr, reg, 2)
        return int.from_bytes(data, 'big')
    
    def read_current(self):
        """Returns current in mA"""
        try:
            current_raw = self._read_register(0x04)
            if current_raw > 32767:
                current_raw -= 65536
            return abs(current_raw * 0.1)  # 0.1mA per bit
        except Exception as e:
            print(f"Error reading INA219 current: {e}")
            return 0.0


# --- Helper functions ---

def read_temperature_f():
    """Read internal temperature sensor and return Fahrenheit"""
    reading = sensor_temp.read_u16()
    voltage = reading * 3.3 / 65535
    temp_c = TEMP_OFFSET - (voltage - TEMP_VOLTAGE_REF) / TEMP_SLOPE
    temp_f = temp_c * 9 / 5 + 32
    return round(temp_f, 1)


def get_cumulative_mah():
    """Get cumulative mAh from file"""
    try:
        with open(MAH_FILE, "r") as f:
            return float(f.read().strip())
    except:
        return 0.0


def set_cumulative_mah(value):
    """Save cumulative mAh to file"""
    try:
        with open(MAH_FILE, "w") as f:
            f.write(str(value))
            f.flush()
        os.sync()
    except Exception as e:
        print(f"Failed to write mAh file: {e}")


def measure_power_during_cycle(ina219_pico):
    """Measure power consumption throughout the cycle and return total mAh"""
    if not ina219_pico:
        return 0.0
    
    cycle_mah = 0.0
    start_time = time.ticks_ms()
    last_sample_time = start_time
    sample_count = 0
    
    # Sample periodically during operations
    def sample_power():
        nonlocal cycle_mah, last_sample_time, sample_count
        try:
            current_time = time.ticks_ms()
            current_ma = ina219_pico.read_current()
            
            # Calculate time since last sample (in hours)
            time_diff_ms = time.ticks_diff(current_time, last_sample_time)
            time_diff_hours = time_diff_ms / (1000 * 3600)
            
            # Add to running total: mAh = mA × hours
            mah_this_sample = current_ma * time_diff_hours
            cycle_mah += mah_this_sample
            
            sample_count += 1
            last_sample_time = current_time
            
            if sample_count <= 3:  # Print first few samples
                print(f"  Power sample: {current_ma:.1f}mA, cycle total: {cycle_mah:.6f}mAh")
        except Exception as e:
            print(f"Error sampling power: {e}")
    
    return sample_power, cycle_mah


def fetch_vars(retries=3):
    """Fetch variables from Google Sheets"""
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
    """Get version from file"""
    try:
        with open(VERSION_FILE, "r") as f:
            return f.read().strip()
    except:
        return "0.0"


def set_local_version(version):
    """Save version to file"""
    try:
        version_str = str(version)
        with open(VERSION_FILE, "w") as f:
            f.write(version_str)
            f.flush()
        os.sync()
    except Exception as e:
        print("Failed to write version file:", e)


def download_new_version(url):
    """Download new version from GitHub"""
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
    """Convert version string or float to tuple of ints"""
    try:
        v_str = str(v)
        parts = v_str.split(".")
        major = int(parts[0])
        minor = int(parts[1]) if len(parts) > 1 else 0
        return (major, minor)
    except:
        return (0, 0)


def check_for_update(sheet_version):
    """Check if update is available and apply it"""
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


def connect_wifi(max_attempts=25):
    """Connect to WiFi from known networks list"""
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    if wlan.isconnected():
        print("Already connected:", wlan.ifconfig())
        led_blue.value(1)
        return wlan, 0

    print("Scanning for networks...")
    available_networks = [net[0].decode('utf-8') for net in wlan.scan()]
    print("Available networks:", available_networks)

    for net in KNOWN_NETWORKS:
        if net["ssid"] in available_networks:
            print(f"Found known network {net['ssid']}, connecting...")
            wlan.connect(net["ssid"], net["password"])
            
            attempts = 0
            while not wlan.isconnected() and attempts < max_attempts:
                led_blue.value(0)
                time.sleep(0.3)
                led_blue.value(1)
                time.sleep(0.7)
                attempts += 1
                print(".", end="")

            if wlan.isconnected():
                print(f"\nConnected to {net['ssid']} in {attempts} attempts! IP: {wlan.ifconfig()[0]}")
                led_blue.value(1)
                return wlan, attempts
            else:
                print(f"\nFailed to connect to {net['ssid']}, trying next network...")

    print("Could not connect to any known networks.")
    wlan.active(False)
    led_blue.value(0)
    return None, 0


def disconnect_wifi(wlan):
    """Disconnect WiFi and turn off radio"""
    if wlan and wlan.active():
        wlan.disconnect()
        wlan.active(False)
        led_blue.value(0)
        print("WiFi disconnected and radio off")

    
def log_to_google(params):
    """Send data to Google Sheets"""
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
    """Get fail count from file"""
    try:
        with open(FAIL_FILE, "r") as f:
            return int(f.read())
    except:
        return 0
    

def set_fail_count(value):
    """Save fail count to file"""
    try:
        with open(FAIL_FILE, "w") as f:
            f.write(str(value))
    except:
        pass


def urlencode(params):
    """Simple URL encoder for GET parameters"""
    def esc(s):
        return str(s).replace(" ", "%20").replace(":", "%3A").replace("/", "%2F")
    return "&".join("{}={}".format(k, esc(params[k])) for k in params)


def main():
    """Main program loop"""
    global SLEEP_MS
    
    time.sleep(1)
    led_green.value(1)
    led_blue.value(0)
    led_red.value(0)
    
    # Load cumulative power usage
    cumulative_mah = get_cumulative_mah()
    print(f"Cumulative power used: {cumulative_mah:.3f} mAh")
    
    # Initialize I2C buses and sensors
    print("Initializing sensors...")
    try:
        # INA260 sensors for battery monitoring (existing)
        i2c0 = I2C(0, scl=Pin(17), sda=Pin(16), freq=400000)
        i2c1 = I2C(1, scl=Pin(3), sda=Pin(2), freq=400000)
        
        ina260_house = INA260(i2c0, address=0x40)
        ina260_engine = INA260(i2c1, address=0x40)
        
        # INA219 sensor for Pico power monitoring (NEW - on GP0/GP1)
        i2c_pico = I2C(0, scl=Pin(1), sda=Pin(0), freq=400000)
        ina219_pico = INA219(i2c_pico, address=0x40)
        
        # Test I2C devices
        devices0 = i2c0.scan()
        devices1 = i2c1.scan()
        devices_pico = i2c_pico.scan()
        print(f"I2C0 devices (House - GP16/GP17): {[hex(d) for d in devices0]}")
        print(f"I2C1 devices (Engine - GP2/GP3): {[hex(d) for d in devices1]}")
        print(f"I2C Pico (Power - GP0/GP1): {[hex(d) for d in devices_pico]}")
        
    except Exception as e:
        print(f"Error initializing sensors: {e}")
        ina260_house = None
        ina260_engine = None
        ina219_pico = None
    
    # Start power monitoring for this cycle
    cycle_start_time = time.ticks_ms()
    sample_power, cycle_mah = measure_power_during_cycle(ina219_pico)
    
    # Sample power at start
    sample_power()
    
    # Connect to WiFi
    wlan, attempts = connect_wifi()
    sample_power()  # Sample after WiFi
    
    fail_count = get_fail_count()
    temp_f = read_temperature_f()
    local_version = get_local_version()

    if wlan is None or not wlan.isconnected():
        # Wi-Fi failed
        fail_count += 1
        set_fail_count(fail_count)
        print(f"Wi-Fi failed {fail_count} times, skipping upload to Google")
    else:
        # Wi-Fi connected
        connected_ssid = wlan.config('essid')
        
        # Fetch variables and check for updates
        vars_from_sheet = fetch_vars()
        sample_power()  # Sample after fetch
        
        if vars_from_sheet:
            print("Variables from Google Sheets:")
            for key, val in vars_from_sheet.items():
                print(f"  {key}: {val}")
       
            # Update sleep time from sheet
            sleep_ms = SLEEP_MS
            try:
                sleep_sec = int(vars_from_sheet.get("Sleep-seconds", SLEEP_MS // 1000))
                sleep_ms = sleep_sec * 1000
                print("Updated sleep time to", sleep_ms, "ms")
            except Exception as e:
                print("Failed to update sleep time from sheet:", e)
            SLEEP_MS = sleep_ms
            
            # Check for updates
            sheet_version = str(vars_from_sheet.get("Version", "0.0"))
            print("Sheet version = " + str(sheet_version))
            check_for_update(sheet_version)
        else:
            print("Skipping variable update because fetch from Google failed")
 
        # Read sensor data
        if ina260_house:
            house_battery_voltage = round(ina260_house.read_voltage(), 2)
            house_solar_current = round(ina260_house.read_current(), 2)
            if house_battery_voltage < 10.0:
                print("House Battery disconnected or very low")
                house_battery_voltage = 0
        else:
            house_battery_voltage = 0
            house_solar_current = 0
            
        if ina260_engine:
            engine_battery_voltage = round(ina260_engine.read_voltage(), 2)
            engine_solar_current = round(ina260_engine.read_current(), 2)
            if engine_battery_voltage < 10.0:
                print("Engine Battery disconnected or very low")
                engine_battery_voltage = 0
        else:
            engine_battery_voltage = 0
            engine_solar_current = 0
        
        sample_power()  # Sample after reading sensors
        
        print(f"House: {house_battery_voltage}V, {house_solar_current}A")
        print(f"Engine: {engine_battery_voltage}V, {engine_solar_current}A")
        
        # Final power measurement before upload
        sample_power()
        
        # Calculate total power for this cycle
        cycle_duration_ms = time.ticks_diff(time.ticks_ms(), cycle_start_time)
        cumulative_mah += cycle_mah
        
        print(f"\n=== Power Usage This Cycle ===")
        print(f"Duration: {cycle_duration_ms}ms")
        print(f"Energy this cycle: {cycle_mah:.6f} mAh")
        print(f"TOTAL cumulative: {cumulative_mah:.3f} mAh")
        
        # Upload to Google Sheets (NOW INCLUDING Power_Used)
        ip = wlan.ifconfig()[0]
        data = {
            "Rounds_to_Connect": fail_count,
            "Wifi_Attempts_This_Round": attempts,
            "IP_address": ip,
            "SSID": connected_ssid,
            "Temp": temp_f,
            "House_Battery": house_battery_voltage,
            "Engine_Battery": engine_battery_voltage,
            "Engine_Solar": engine_solar_current,
            "House_Solar": house_solar_current,
            "Local_Version": local_version,
            "Power_Used": round(cumulative_mah, 3)  # NEW: Total mAh used
        }
        result = log_to_google(data)
        print("Google Sheets response:", result)
        
        if result == "OK":
            led_red.value(1)
            fail_count = 0
            set_fail_count(fail_count)
            # Save cumulative power usage
            set_cumulative_mah(cumulative_mah)
            time.sleep(1)
    
    # Go to sleep
    print("Going to sleep...")
    led_red.value(0)
    led_blue.value(0)
    led_green.value(0)
    disconnect_wifi(wlan)
    time.sleep(0.2)
    machine.deepsleep(SLEEP_MS)


# Run main program
main()
