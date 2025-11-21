import paramiko
import yaml
import logging
from typing import Dict, Any, List, Tuple
from dataclasses import dataclass

@dataclass
class DeviceConfig:
    host: str
    username: str
    password: str
    port: int = 22
    description: str = ""

class MikroTikSSHConnector:
    def __init__(self, config_path: str = "config/devices.yaml", enable_logging: bool = True):
        self.config_path = config_path
        self.devices = self._load_devices_config()
        self.enable_logging = enable_logging
        self.logger = self._setup_logger()
    
    def _setup_logger(self) -> logging.Logger:
        logger = logging.getLogger('mikrotik_manager')
        
        if not self.enable_logging:
            logger.handlers = []
            logger.addHandler(logging.NullHandler())
            logger.propagate = False
            return logger
        
        logger.setLevel(logging.INFO)
        
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter(
                '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        
        return logger
    
    def set_logging_enabled(self, enabled: bool):
        self.enable_logging = enabled
        self.logger = self._setup_logger()
    
    def _load_devices_config(self) -> Dict[str, DeviceConfig]:
        try:
            with open(self.config_path, 'r', encoding='utf-8') as file:
                config = yaml.safe_load(file)
            
            devices = {}
            for name, device_config in config['devices'].items():
                devices[name] = DeviceConfig(**device_config)
            
            return devices
        except Exception as e:
            print(f"❌ Ошибка загрузки конфигурации: {str(e)}")
            return {}
    
    def connect(self, device_name: str) -> paramiko.SSHClient:
        if device_name not in self.devices:
            raise ValueError(f"Устройство {device_name} не найдено в конфигурации")
        
        device = self.devices[device_name]
        
        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            if self.enable_logging:
                self.logger.info(f"Подключение к {device_name} ({device.host})")
            
            client.connect(
                hostname=device.host,
                username=device.username,
                password=device.password,
                port=device.port,
                timeout=10,
                look_for_keys=False
            )
            
            return client
            
        except Exception as e:
            if self.enable_logging:
                self.logger.error(f"Ошибка подключения к {device_name}: {str(e)}")
            raise
    
    def execute_command(self, device_name: str, command: str) -> Tuple[List[str], List[str]]:
        client = self.connect(device_name)
        
        try:
            stdin, stdout, stderr = client.exec_command(command)
            output = stdout.read().decode('utf-8', errors='ignore').splitlines()
            error = stderr.read().decode('utf-8', errors='ignore').splitlines()
            
            return output, error
            
        finally:
            client.close()
    
    def get_routeros_info(self, device_name: str) -> Dict[str, str]:
        try:
            commands = {
                'identity': '/system identity print',
                'version': '/system resource print',
                'routerboard': '/system routerboard print'
            }
            
            info = {}
            
            output, error = self.execute_command(device_name, commands['identity'])
            for line in output:
                if 'name:' in line:
                    info['identity'] = line.split('name:')[1].strip()
                    break
            
            output, error = self.execute_command(device_name, commands['version'])
            for line in output:
                if 'version:' in line:
                    info['version'] = line.split('version:')[1].strip()
                elif 'board-name:' in line:
                    info['board'] = line.split('board-name:')[1].strip()
                elif 'architecture-name:' in line:
                    info['architecture'] = line.split('architecture-name:')[1].strip()
            
            output, error = self.execute_command(device_name, commands['routerboard'])
            for line in output:
                if 'model:' in line:
                    info['model'] = line.split('model:')[1].strip()
                elif 'serial-number:' in line:
                    info['serial'] = line.split('serial-number:')[1].strip()
            
            return info
            
        except Exception as e:
            if self.enable_logging:
                self.logger.error(f"Ошибка получения информации RouterOS для {device_name}: {str(e)}")
            return {'error': str(e)}
    
    def test_connection(self, device_name: str) -> bool:
        try:
            client = self.connect(device_name)
            client.close()
            return True
        except:
            return False
