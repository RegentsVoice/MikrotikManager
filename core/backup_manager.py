import time
import re
import logging
from typing import Dict, List, Tuple
from .connector import MikroTikSSHConnector

class BackupManager:
    def __init__(self, connector: MikroTikSSHConnector):
        self.connector = connector
        self.logger = logging.getLogger('mikrotik_backup')
        self.backup_prefix = "mum_"
        
        if not self.logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)
    
    def _generate_short_backup_name(self, comment: str = "") -> str:
        timestamp = time.strftime("%m%d_%H%M")
        base_name = f"{self.backup_prefix}{timestamp}"
        
        if comment:
            clean_comment = re.sub(r'[^\w]', '', comment)[:10]
            base_name = f"{base_name}_{clean_comment}"
        
        return base_name[:20]
    
    def _find_matching_backup(self, device_name: str, base_name: str) -> str:
        try:
            all_files, _ = self.connector.execute_command(device_name, '/file print')
            
            for line in all_files:
                if base_name in line and any(ext in line.lower() for ext in ['.backup', '.bac']):
                    match = re.search(r'(\S+\.(?:backup|bac))', line)
                    if match:
                        return match.group(1)
            
            return ""
        except Exception as e:
            self.logger.error(f"❌ Ошибка поиска бэкапа: {str(e)}")
            return ""
    
    def list_backups(self, device_name: str) -> List[str]:
        try:
            self.logger.info(f"🔍 Поиск бэкапов на устройстве {device_name}")
            output, error = self.connector.execute_command(device_name, '/file print where name~"backup"')
            
            mum_backups = []
            for line in output:
                if self.backup_prefix in line:
                    match = re.search(rf'({self.backup_prefix}[^\s"]+\.(?:backup|bac))', line)
                    if match:
                        backup_name = match.group(1)
                        mum_backups.append(backup_name)
                        self.logger.debug(f"Найден бэкап: {backup_name}")
            
            self.logger.info(f"📋 Найдено бэкапов на {device_name}: {len(mum_backups)}")
            return mum_backups
            
        except Exception as e:
            self.logger.error(f"❌ Ошибка получения списка бэкапов для {device_name}: {str(e)}")
            return []
    
    def delete_old_backups(self, device_name: str) -> int:
        try:
            backups = self.list_backups(device_name)
            deleted_count = 0
            
            self.logger.info(f"💥 Удаление {len(backups)} старых бэкапов на {device_name}")
            
            for backup_file in backups:
                try:
                    self.logger.info(f"💥 Удаление: {backup_file}")
                    
                    output, error = self.connector.execute_command(device_name, f'/file remove "{backup_file}"')
                    time.sleep(1)
                    
                    output, error = self.connector.execute_command(device_name, f'/file remove [find name="{backup_file}"]')
                    time.sleep(1)
                    
                    remaining_backups = self.list_backups(device_name)
                    if backup_file not in remaining_backups:
                        deleted_count += 1
                        self.logger.info(f"✅ Бэкап удален: {backup_file}")
                    else:
                        self.logger.error(f"❌ Не удалось удалить: {backup_file}")
                        
                except Exception as e:
                    self.logger.error(f"❌ Ошибка при удалении {backup_file}: {str(e)}")
                    continue
            
            self.logger.info(f"✅ Удалено бэкапов: {deleted_count}/{len(backups)}")
            return deleted_count
            
        except Exception as e:
            self.logger.error(f"❌ Ошибка удаления бэкапов: {str(e)}")
            return 0
    
    def create_backup(self, device_name: str, comment: str = "") -> Tuple[bool, str]:
        try:
            self.logger.info(f"💾 Создание бэкапа для {device_name}")
            
            self.logger.info("🗑️ Очистка старых бэкапов...")
            deleted_count = self.delete_old_backups(device_name)
            
            backup_base_name = self._generate_short_backup_name(comment)
            self.logger.info(f"🔄 Создание бэкапа: {backup_base_name}")
            
            output, error = self.connector.execute_command(
                device_name, 
                f'/system backup save name="{backup_base_name}"'
            )
            
            if error and any('error' in err.lower() for err in error if err.strip()):
                self.logger.error(f"❌ Ошибка при создании бэкапа: {error}")
                return False, ""
            
            self.logger.info("⏳ Ожидание завершения создания бэкапа...")
            
            max_attempts = 8
            actual_backup_name = ""
            
            for attempt in range(max_attempts):
                time.sleep(3) 
                
                actual_backup_name = self._find_matching_backup(device_name, backup_base_name)
                
                if actual_backup_name:
                    self.logger.info(f"✅ Бэкап успешно создан: {actual_backup_name}")
                    return True, actual_backup_name
                else:
                    self.logger.info(f"⌛ Попытка {attempt + 1}/{max_attempts}: бэкап еще не появился")
            
            self.logger.error(f"❌ Бэкап не появился после {max_attempts} попыток: {backup_base_name}")
            
            all_backups = self.list_backups(device_name)
            self.logger.info(f"📁 Все доступные бэкапы: {all_backups}")
            
            return False, ""
                
        except Exception as e:
            self.logger.error(f"❌ Критическая ошибка создания бэкапа для {device_name}: {str(e)}")
            return False, ""
    
    def create_backup_direct(self, device_name: str, comment: str = "") -> Tuple[bool, str]:
        try:
            self.logger.info(f"💾 Создание бэкапа для {device_name}")
            
            self.logger.info("🗑️ Очистка старых бэкапов...")
            deleted_count = self.delete_old_backups(device_name)
            
            backup_base_name = self._generate_short_backup_name(comment)
            self.logger.info(f"🔄 Создание бэкапа: {backup_base_name}")
            
            output, error = self.connector.execute_command(
                device_name, 
                f'/system backup save name="{backup_base_name}"'
            )
            
            if error and any('error' in err.lower() for err in error if err.strip()):
                self.logger.error(f"❌ Ошибка создания бэкапа: {error}")
                return False, ""
            
            time.sleep(8)
            
            actual_backup_name = self._find_matching_backup(device_name, backup_base_name)
            
            if actual_backup_name:
                self.logger.info(f"✅ Бэкап создан: {actual_backup_name}")
                return True, actual_backup_name
            else:
                self.logger.error(f"❌ Бэкап не найден после создания: {backup_base_name}")
                
                all_files, _ = self.connector.execute_command(device_name, '/file print')
                self.logger.info(f"📁 Содержимое файловой системы:")
                for line in all_files:
                    if 'backup' in line.lower() or 'bac' in line.lower():
                        self.logger.info(f"   {line}")
                return False, ""
                
        except Exception as e:
            self.logger.error(f"❌ Ошибка создания бэкапа: {str(e)}")
            return False, ""
    
    def get_backup_info(self, device_name: str) -> Dict[str, any]:
        try:
            backups = self.list_backups(device_name)
            
            backup_details = []
            
            for backup in backups:
                output, error = self.connector.execute_command(
                    device_name, 
                    f'/file print where name="{backup}"'
                )
                
                for line in output:
                    if backup in line:
                        size_match = re.search(r'(\d+\.?\d*)\s*(KiB|MiB|GiB|B)', line)
                        if size_match:
                            size = size_match.group(1)
                            unit = size_match.group(2)
                            backup_details.append({
                                'name': backup,
                                'size': f"{size} {unit}",
                                'full_info': line
                            })
                        else:
                            backup_details.append({
                                'name': backup,
                                'size': "Неизвестно",
                                'full_info': line
                            })
                        break
            
            return {
                'count': len(backups),
                'backups': backup_details,
                'total_size': f"{len(backups)} файлов"
            }
            
        except Exception as e:
            self.logger.error(f"Ошибка получения информации о бэкапах для {device_name}: {str(e)}")
            return {'count': 0, 'backups': [], 'total_size': '0'}