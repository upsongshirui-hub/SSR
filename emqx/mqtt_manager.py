import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion
import json
import random
import ssl
from config import DEFAULT_MQTT_CONFIG


class BrokerClient:
    def __init__(self, host, port, username, password, on_connect_cb=None, on_message_cb=None, is_primary=False):
        self.host = host
        self.port = int(port)
        self.client = mqtt.Client(CallbackAPIVersion.VERSION2, client_id=f"py_cli_{random.randint(1000, 9999)}")
        if username and password:
            self.client.username_pw_set(username, password)
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            self.client.tls_set_context(ctx)
        except Exception as e:
            print(f"SSL Warning: {e}")
        self.is_primary = is_primary
        self.connected = False
        self.external_on_connect = on_connect_cb
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        if is_primary and on_message_cb:
            self.client.on_message = on_message_cb

    def _on_connect(self, client, userdata, flags, rc, properties=None):
        if rc == 0:
            self.connected = True
            print(f"✅ 已连接: {self.host}")
            if self.is_primary:
                client.subscribe(f"{DEFAULT_MQTT_CONFIG['topic_root']}/#")
            if self.external_on_connect:
                self.external_on_connect(self)

    def _on_disconnect(self, client, userdata, flags, rc, properties=None):
        self.connected = False

    def connect(self):
        try:
            self.client.connect(self.host, self.port, 60)
            self.client.loop_start()
        except Exception as e:
            print(f"连接错误: {e}")

    def publish(self, topic, payload):
        if self.connected:
            try:
                self.client.publish(topic, json.dumps(payload))
            except Exception:
                pass

    def disconnect(self):
        try:
            self.client.loop_stop()
            self.client.disconnect()
        except Exception:
            pass

    def get_display_name(self):
        status = "🟢" if self.connected else "🔴"
        role = " (主接收)" if self.is_primary else ""
        return f"{status} {self.host}:{self.port}{role}"