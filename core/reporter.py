import requests
import json
from datetime import datetime
from typing import Dict, Any
from config.telegram import TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID

class ReportManager:
    def __init__(self):
        self.bot_token = TELEGRAM_BOT_TOKEN
        self.channel_id = TELEGRAM_CHANNEL_ID
    
    def send_telegram_message(self, message: str) -> bool:
        if not self.bot_token or not self.channel_id:
            print("⚠️  Telegram токен или ID канала не настроены")
            return False
        
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {
                'chat_id': self.channel_id,
                'text': message,
                'parse_mode': 'HTML'
            }
            
            response = requests.post(url, json=payload, timeout=10)
            return response.status_code == 200
            
        except Exception as e:
            print(f"❌ Ошибка отправки в Telegram: {str(e)}")
            return False
    
    def create_version_report(self, version_data: Dict[str, Any]) -> str:
        report = "📋 <b>Отчет о версиях MikroTik</b>\n"
        report += f"📅 Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
        report += "─" * 40 + "\n\n"
        
        online_devices = 0
        version_stats = {}
        
        for device_name, info in version_data.items():
            if info.get('status') == 'online':
                online_devices += 1
                status_icon = "🟢"
                version = info.get('current_version', 'Неизвестно')
                
                if version != 'Неизвестно':
                    version_short = version.split()[0]
                    if version_short in version_stats:
                        version_stats[version_short] += 1
                    else:
                        version_stats[version_short] = 1
                
                report += f"{status_icon} <b>{device_name}</b>\n"
                report += f"   📋 Версия: {version}\n"
                
                if 'identity' in info:
                    report += f"   🏷️ Имя: {info['identity']}\n"
                if 'model' in info:
                    report += f"   💻 Модель: {info.get('model', 'Неизвестно')}\n"
                if 'architecture' in info:
                    report += f"   🏗️ Архитектура: {info.get('architecture', 'Неизвестно')}\n"
                
                report += "\n"
            else:
                report += f"🔴 <b>{device_name}</b>\n"
                report += f"   ❌ Устройство недоступно: {info.get('message', 'Неизвестно')}\n\n"
        
        report += "📊 <b>Статистика:</b>\n"
        report += f"• Всего устройств: {len(version_data)}\n"
        report += f"• Онлайн: {online_devices}\n"
        report += f"• Оффлайн: {len(version_data) - online_devices}\n"
        
        if version_stats:
            report += f"• Распределение версий:\n"
            for version, count in sorted(version_stats.items()):
                report += f"   - {version}: {count} устройств\n"
        
        return report
    
    def create_update_report(self, update_results: Dict[str, Any]) -> str:
        report = "🔄 <b>Отчет об обновлениях MikroTik</b>\n"
        report += f"📅 Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
        report += "─" * 40 + "\n\n"
        
        online_devices = 0
        updates_available = 0
        version_stats = {}
        
        for device_name, info in update_results.items():
            status_icon = "🟢" if info.get('status') == 'online' else "🔴"
            
            report += f"{status_icon} <b>{device_name}</b>\n"
            
            if info.get('status') == 'online':
                online_devices += 1
                
                current_version = info.get('current_version', 'Неизвестно')
                latest_version = info.get('latest_version', 'Неизвестно')
                update_available = info.get('update_available', False)
                
                # Собираем статистику по версиям
                if current_version != 'Неизвестно':
                    version_short = current_version.split()[0]
                    if version_short in version_stats:
                        version_stats[version_short] += 1
                    else:
                        version_stats[version_short] = 1
                
                if update_available:
                    updates_available += 1
                    update_icon = "🔄"
                    status_text = f"Требуется обновление: {current_version} → {latest_version}"
                else:
                    update_icon = "✅"
                    status_text = "Система актуальна"
                
                report += f"   📋 Текущая версия: {current_version}\n"
                report += f"   🆕 Последняя версия: {latest_version}\n"
                report += f"   📊 Статус: {update_icon} {status_text}\n"
            else:
                report += f"   ❌ Устройство недоступно: {info.get('message', 'Неизвестно')}\n"
            
            report += "\n"
        

        report += "📈 <b>Статистика:</b>\n"
        report += f"• Всего устройств: {len(update_results)}\n"
        report += f"• Онлайн: {online_devices}\n"
        report += f"• Оффлайн: {len(update_results) - online_devices}\n"
        report += f"• Требуют обновления: {updates_available}\n"
        report += f"• Актуальны: {online_devices - updates_available}\n"
        
        if version_stats:
            report += f"• Распределение версий:\n"
            for version, count in sorted(version_stats.items()):
                report += f"   - {version}: {count} устройств\n"
        
        return report
    
    def create_backup_report(self, backup_results: Dict[str, Any]) -> str:
        report = "💾 <b>Отчет о создании бэкапов</b>\n"
        report += f"📅 Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
        report += "─" * 40 + "\n\n"
        
        success_count = 0
        failed_count = 0
        
        for device_name, result in backup_results.items():
            status = result.get('status')
            
            if status == 'success':
                success_count += 1
                report += f"✅ <b>{device_name}</b>\n"
                report += f"   💾 Бэкап: {result.get('backup_file', 'Неизвестно')}\n"
                report += f"   📊 Статус: Успешно создан\n\n"
            
            elif status == 'failed':
                failed_count += 1
                report += f"❌ <b>{device_name}</b>\n"
                report += f"   💥 Ошибка: {result.get('message', 'Неизвестно')}\n\n"
            
            else:  # error
                failed_count += 1
                report += f"❌ <b>{device_name}</b>\n"
                report += f"   💥 Ошибка: {result.get('message', 'Неизвестно')}\n\n"
        
        report += "📊 <b>Итоги:</b>\n"
        report += f"• Всего устройств: {len(backup_results)}\n"
        report += f"• Успешно создано: {success_count}\n"
        report += f"• Ошибок: {failed_count}\n"
        report += f"• Процент успеха: {round(success_count/len(backup_results)*100 if backup_results else 0, 1)}%\n"
        
        return report
    
    def create_auto_update_report(self, update_results: Dict[str, Any]) -> str:
        report = "⚡ <b>Отчет об автоматическом обновлении</b>\n"
        report += f"📅 Дата: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
        report += "─" * 40 + "\n\n"
        
        updated_count = 0
        failed_count = 0
        no_update_count = 0
        backup_failed_count = 0
        
        for device_name, result in update_results.items():
            status = result.get('status')
            
            if status == 'updated':
                updated_count += 1
                report += f"✅ <b>{device_name}</b>\n"
                report += f"   🆕 Версия: {result.get('new_version', 'Неизвестно')}\n"
                report += f"   💾 Бэкап: {result.get('backup_file', 'Неизвестно')}\n"
                report += f"   📊 Статус: Успешно обновлено\n\n"
            
            elif status == 'failed':
                failed_count += 1
                report += f"❌ <b>{device_name}</b>\n"
                report += f"   💥 Ошибка: {result.get('message', 'Неизвестно')}\n"
                report += f"   💾 Бэкап: {result.get('backup_file', 'Создан')}\n\n"
            
            elif status == 'no_update':
                no_update_count += 1
                report += f"✅ <b>{device_name}</b>\n"
                report += f"   📊 Статус: Обновления не требуются\n"
                report += f"   💾 Бэкап: {result.get('backup_file', 'Создан')}\n\n"
            
            elif status == 'backup_failed':
                backup_failed_count += 1
                report += f"❌ <b>{device_name}</b>\n"
                report += f"   💥 Ошибка: Не удалось создать бэкап\n\n"
            
            else:  # error
                failed_count += 1
                report += f"❌ <b>{device_name}</b>\n"
                report += f"   💥 Ошибка: {result.get('message', 'Неизвестно')}\n\n"
        
        report += "📈 <b>Итоги обновления:</b>\n"
        report += f"• Всего обработано: {len(update_results)}\n"
        report += f"• Успешно обновлено: {updated_count}\n"
        report += f"• Не требовали обновления: {no_update_count}\n"
        report += f"• Ошибок обновления: {failed_count}\n"
        report += f"• Ошибок бэкапа: {backup_failed_count}\n"
        report += f"• Процент успеха: {round(updated_count/len(update_results)*100 if update_results else 0, 1)}%\n"
        
        return report
    
    def print_console_report(self, data: Dict[str, Any], report_type: str = "versions"):
        if report_type == "versions":
            print("\n" + "="*60)
            print("ОТЧЕТ О ВЕРСИЯХ MIKROTIK")
            print("="*60)
            
            online_count = 0
            
            for device_name, info in data.items():
                status = "Онлайн" if info.get('status') == 'online' else "Оффлайн"
                status_icon = "🟢" if info.get('status') == 'online' else "🔴"
                
                print(f"\n{status_icon} Устройство: {device_name}")
                print(f"   Статус: {status}")
                
                if info.get('status') == 'online':
                    online_count += 1
                    print(f"   Версия RouterOS: {info.get('current_version', 'Неизвестно')}")
                    if 'identity' in info:
                        print(f"   Имя устройства: {info['identity']}")
                    if 'model' in info:
                        print(f"   Модель: {info.get('model', 'Неизвестно')}")
                else:
                    print(f"   Ошибка: {info.get('message', 'Устройство недоступно')}")
            
            print(f"\n📊 Итого: {online_count}/{len(data)} устройств онлайн")
            
        elif report_type == "updates":
            print("\n" + "="*60)
            print("ОТЧЕТ ОБ ОБНОВЛЕНИЯХ MIKROTIK")
            print("="*60)
            
            updates_available = 0
            online_count = 0
            version_stats = {}
            
            for device_name, info in data.items():
                status = "Онлайн" if info.get('status') == 'online' else "Оффлайн"
                status_icon = "🟢" if info.get('status') == 'online' else "🔴"
                update_available = info.get('update_available', False)
                
                if info.get('status') == 'online':
                    online_count += 1
                    
                    current_version = info.get('current_version', 'Неизвестно')
                    if current_version != 'Неизвестно':
                        version_short = current_version.split()[0]
                        if version_short in version_stats:
                            version_stats[version_short] += 1
                        else:
                            version_stats[version_short] = 1
                    
                    if update_available:
                        updates_available += 1
                        update_status = "🔄 ТРЕБУЕТ ОБНОВЛЕНИЯ"
                    else:
                        update_status = "✅ АКТУАЛЬНА"
                else:
                    update_status = "❌ НЕДОСТУПНО"
                
                print(f"\n{status_icon} Устройство: {device_name}")
                print(f"   Статус: {status}")
                
                if info.get('status') == 'online':
                    print(f"   Текущая версия: {info.get('current_version', 'Неизвестно')}")
                    print(f"   Последняя версия: {info.get('latest_version', 'Неизвестно')}")
                    print(f"   Статус обновления: {update_status}")
                else:
                    print(f"   Ошибка: {info.get('message', 'Устройство недоступно')}")
            
            print(f"\n📊 ИТОГО:")
            print(f"   • Устройств онлайн: {online_count}/{len(data)}")
            print(f"   • Требуют обновления: {updates_available}")
            
            if version_stats:
                print(f"   • Распределение версий:")
                for version, count in sorted(version_stats.items()):
                    print(f"     - {version}: {count} устройств")