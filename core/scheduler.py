import schedule
import time
import threading
import yaml
import logging
from datetime import datetime
from typing import Dict, Any, List
from .connector import MikroTikSSHConnector
from .updater import MikroTikUpdater
from .reporter import ReportManager
from .backup_manager import BackupManager


class TaskScheduler:
    def __init__(self, connector: MikroTikSSHConnector, updater: MikroTikUpdater, reporter: ReportManager):
        self.connector = connector
        self.updater = updater
        self.reporter = reporter
        self.backup_manager = BackupManager(connector)
        self.scheduler_config = self._load_scheduler_config()
        self.is_running = False
        self.scheduler_thread = None

        self._setup_logging()

    def _setup_logging(self):
        self.logger = logging.getLogger('mikrotik_scheduler')

        notifications_config = self.scheduler_config.get('notifications', {})
        log_to_file = notifications_config.get('log_to_file', True)
        console_logging = notifications_config.get('console_logging', True)

        if not log_to_file and not console_logging:
            self.logger.handlers = []
            self.logger.addHandler(logging.NullHandler())
            self.logger.propagate = False
            return

        self.logger.setLevel(logging.INFO)

        if log_to_file:
            import os
            if not os.path.exists('logs'):
                os.makedirs('logs')

            file_handler = logging.FileHandler('logs/scheduler.log', encoding='utf-8')
            file_handler.setLevel(logging.INFO)
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            file_handler.setFormatter(formatter)

            if not any(isinstance(h, logging.FileHandler) for h in self.logger.handlers):
                self.logger.addHandler(file_handler)

        if console_logging:
            console_handler = logging.StreamHandler()
            console_handler.setLevel(logging.INFO)
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            console_handler.setFormatter(formatter)

            if not any(isinstance(h, logging.StreamHandler) for h in self.logger.handlers):
                self.logger.addHandler(console_handler)

    def _load_scheduler_config(self) -> Dict[str, Any]:
        try:
            with open('config/scheduler.yaml', 'r', encoding='utf-8') as file:
                return yaml.safe_load(file)
        except Exception as e:
            print(f"❌ Ошибка загрузки конфигурации планировщика: {e}")
            return {}

    def _should_run_today(self, days: List[str]) -> bool:
        if not days:
            return True
        today = datetime.now().strftime("%A").lower()
        return today in [day.lower() for day in days]

    def _get_devices_for_task(self, task_config: Dict[str, Any]) -> List[str]:
        devices = task_config.get('devices', [])
        if not devices or devices == ['all']:
            return list(self.connector.devices.keys())
        return [d for d in devices if d in self.connector.devices]

    def task_version_check(self, show_progress: bool = True, force: bool = False):
        config = self.scheduler_config.get('scheduler', {}).get('version_check', {})

        if not force:
            if not config.get('enabled', False):
                return
            if not self._should_run_today(config.get('days', [])):
                return

        self.logger.info("🔄 Запуск автоматической проверки версий")
        if show_progress:
            print("🔄 Запуск проверки версий на всех устройствах...")

        try:
            results = {}
            devices = self._get_devices_for_task(config)
            total_devices = len(devices)

            for index, device_name in enumerate(devices, 1):
                if show_progress:
                    print(f"🔍 [{index}/{total_devices}] Проверка {device_name}...")

                if not self.connector.test_connection(device_name):
                    results[device_name] = {'status': 'offline', 'message': 'Устройство недоступно'}
                    continue

                device_info = self.connector.get_routeros_info(device_name)

                if 'error' in device_info:
                    results[device_name] = {'status': 'error', 'message': device_info['error']}
                else:
                    results[device_name] = {
                        'status': 'online',
                        'current_version': device_info.get('version', 'Неизвестно'),
                        'identity': device_info.get('identity', 'Неизвестно'),
                        'model': device_info.get('model', 'Неизвестно')
                    }

            if config.get('telegram_report', False):
                report = self.reporter.create_version_report(results)
                self.reporter.send_telegram_message(report)

        except Exception as e:
            self.logger.error(f"❌ Ошибка при проверке версий: {str(e)}")

    def task_update_check(self, show_progress: bool = True, force: bool = False):
        config = self.scheduler_config.get('scheduler', {}).get('update_check', {})

        if not force:
            if not config.get('enabled', False):
                return
            if not self._should_run_today(config.get('days', [])):
                return

        try:
            results = self.updater.check_all_devices()

            if config.get('telegram_report', False):
                report = self.reporter.create_update_report(results)
                self.reporter.send_telegram_message(report)

        except Exception as e:
            self.logger.error(f"❌ Ошибка при проверке обновлений: {str(e)}")

    def task_auto_update(self, show_progress: bool = True, force: bool = False):
        config = self.scheduler_config.get('scheduler', {}).get('auto_update', {})

        if not force:
            if not config.get('enabled', False):
                return
            if not self._should_run_today(config.get('days', [])):
                return

        devices = self._get_devices_for_task(config)
        results = {}

        for device_name in devices:
            try:
                backup_success, backup_name = self.backup_manager.create_backup(device_name, "auto_update")
                if not backup_success:
                    results[device_name] = {'status': 'backup_failed'}
                    continue

                update_info = self.updater.check_for_updates(device_name)
                if not update_info.get('available'):
                    results[device_name] = {'status': 'no_update', 'backup_file': backup_name}
                    continue

                result = self.updater.install_update(device_name)
                results[device_name] = result

            except Exception as e:
                results[device_name] = {'status': 'error', 'message': str(e)}

        if config.get('telegram_report', False):
            report = self.reporter.create_auto_update_report(results)
            self.reporter.send_telegram_message(report)

    def task_create_backups(self, show_progress: bool = True, force: bool = False):
        """Создание бэкапов на всех устройствах (ручной/авто режим)"""

        config = self.scheduler_config.get('scheduler', {}).get('auto_backup', {})

        if not force:
            if not config.get('enabled', False):
                return
            if not self._should_run_today(config.get('days', [])):
                return

        results = {}
        devices = self._get_devices_for_task(config)

        for device_name in devices:
            try:
                ok, backup_name = self.backup_manager.create_backup(device_name, "scheduled")
                results[device_name] = {
                    'status': 'success' if ok else 'failed',
                    'backup_file': backup_name
                }
            except Exception as e:
                results[device_name] = {'status': 'error', 'message': str(e)}

        if config.get('telegram_report', False):
            report = self.reporter.create_backup_report(results)
            self.reporter.send_telegram_message(report)

    def setup_schedules(self):
        schedule.clear()
        scheduler_config = self.scheduler_config.get('scheduler', {})

        vc = scheduler_config.get('version_check', {})
        if vc.get('enabled', False):
            schedule.every().day.at(vc.get('time', '09:00')).do(self.task_version_check)

        uc = scheduler_config.get('update_check', {})
        if uc.get('enabled', False):
            schedule.every().day.at(uc.get('time', '10:00')).do(self.task_update_check)

        au = scheduler_config.get('auto_update', {})
        if au.get('enabled', False):
            schedule.every().day.at(au.get('time', '23:00')).do(self.task_auto_update)

        ab = scheduler_config.get('auto_backup', {})
        if ab.get('enabled', False):
            schedule.every().day.at(ab.get('time', '02:00')).do(self.task_create_backups)

    def start_scheduler(self):
        if self.is_running:
            return

        self.is_running = True
        self.scheduler_thread = threading.Thread(target=self._scheduler_loop, daemon=True)
        self.scheduler_thread.start()

    def stop_scheduler(self):
        self.is_running = False
        schedule.clear()

    def _scheduler_loop(self):
        while self.is_running:
            try:
                schedule.run_pending()
                time.sleep(1)
            except Exception as e:
                self.logger.error(f"❌ Ошибка в цикле планировщика: {str(e)}")
                time.sleep(60)

    def get_scheduler_config(self) -> Dict[str, Any]:
        return self.scheduler_config.copy()
